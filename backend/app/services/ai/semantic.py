"""Semantic matching: for each report, the top-K most similar reports of
the opposite kind (a lost report gets found items, and the reverse).

Runs in the background (ITEM_ANALYSE jobs), never while a page loads:

  1. Build the item's text (name, category, colour, brand, description, place).
  2. Turn it into a vector with the embedder (embeddings.py).
  3. Pre-filter candidates in PostgreSQL: opposite kind, still active,
     same embedding model, dates that make sense.
  4. Score each candidate (numpy):
        70%  meaning of the text   (cosine similarity of the vectors)
        20%  same category
        10%  dates close together
  5. Keep the best K above a minimum score, with a short reason, and store
     them in match_suggestions in BOTH directions.

The item page reads match_suggestions (fast), and matching.py still
filters everything for safety before showing it.

Phase B: when an LLM is configured, the agent (agent.py) chooses the
filters and writes the scores and reasons instead; this file's
compute_matches() remains the fallback when the LLM is unavailable.
"""

from datetime import date, timedelta

import numpy as np
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, selectinload, undefer

from app.core.clock import now
from app.core.config import get_settings
from app.models import AiJob, Item, ItemKind, ItemStatus, MatchSuggestion
from app.services.ai import queue
from app.services.ai.embeddings import get_embedder
from app.services.matching import Match

JOB_KIND = "ITEM_ANALYSE"

# Statuses where an item is still "looking": a lost report still missing,
# or a found item not yet returned.
ACTIVE = {
    ItemKind.LOST: [ItemStatus.OPEN],
    ItemKind.FOUND: [ItemStatus.AWAITING_DROPOFF, ItemStatus.IN_CUSTODY, ItemStatus.CLAIM_PENDING],
}

WEIGHT_TEXT, WEIGHT_CATEGORY, WEIGHT_DATE = 0.70, 0.20, 0.10
DATE_SLACK_DAYS = 3  # an item can't be found long before it was lost
DATE_SCALE_DAYS = 30  # the date part of the score fades to 0 over this many days


def item_text(item: Item) -> str:
    """Everything that describes the item, as one text for the embedder.
    Deliberately nothing personal (no reporter, no contact details)."""
    parts = [item.title, item.category.name if item.category else "", item.colour or "",
             item.brand or "", item.description or "", f"place: {item.location_text}",
             # Phase C: what the AI saw in the photos (brand, marks...).
             item.ai_description or ""]
    return ". ".join(part for part in parts if part)


def _date_score(a: date, b: date) -> float:
    return max(0.0, 1.0 - abs((a - b).days) / DATE_SCALE_DAYS)


def _reason(item: Item, candidate: Item, text_sim: float) -> str:
    bits = []
    if text_sim >= 0.6:
        bits.append("very similar description")
    elif text_sim >= 0.35:
        bits.append("similar description")
    else:
        bits.append("some details in common")
    if candidate.category_id == item.category_id:
        bits.append(f"same category ({candidate.category.name})")
    if item.colour and candidate.colour and item.colour.strip().lower() == candidate.colour.strip().lower():
        bits.append(f"same colour ({candidate.colour})")
    phrase = date_phrase(item, candidate)
    if phrase:
        bits.append(phrase)
    text = ", ".join(bits)
    return text[0].upper() + text[1:]


def days_found_after_lost(item: Item, candidate: Item) -> int:
    """Found date minus lost date, in days (negative: found before it was lost)."""
    lost, found = (item, candidate) if item.kind == ItemKind.LOST else (candidate, item)
    return (found.happened_on - lost.happened_on).days


def date_phrase(item: Item, candidate: Item) -> str | None:
    """The date part of a reason, always computed here, never by the AI
    (language models are unreliable at date arithmetic)."""
    gap = days_found_after_lost(item, candidate)
    if gap == 0:
        return "found the same day it was lost"
    if gap > 0:
        return f"found {gap} day{'s' if gap != 1 else ''} after it was lost"
    return None


def candidates_for(db: Session, item: Item) -> list[Item]:
    """Every item that could be the same thing: opposite kind, still active,
    embedded by the same model, and with dates that make sense (a thing
    can't be found well before it was lost). The agent narrows this further."""
    if item.status not in ACTIVE[item.kind] or not item.embedding:
        return []
    other = ItemKind.FOUND if item.kind == ItemKind.LOST else ItemKind.LOST
    query = (
        select(Item)
        .where(Item.kind == other, Item.status.in_(ACTIVE[other]), Item.id != item.id,
               Item.embedding_model == item.embedding_model, Item.embedding.is_not(None))
        .options(undefer(Item.embedding), selectinload(Item.category))
    )
    if item.kind == ItemKind.LOST:
        query = query.where(Item.happened_on >= item.happened_on - timedelta(days=DATE_SLACK_DAYS))
    else:
        query = query.where(Item.happened_on <= item.happened_on + timedelta(days=DATE_SLACK_DAYS))
    return list(db.scalars(query))


def text_similarities(item: Item, candidates: list[Item]) -> list[float]:
    """Cosine similarity (0..1) between the item's vector and each candidate's."""
    if not candidates:
        return []
    vectors = np.asarray([c.embedding for c in candidates], dtype=np.float32)
    sims = np.clip(vectors @ np.asarray(item.embedding, dtype=np.float32), 0.0, 1.0)
    return [float(x) for x in sims]


def clear_matches(db: Session, item: Item) -> None:
    db.execute(delete(MatchSuggestion).where(
        or_(MatchSuggestion.item_id == item.id, MatchSuggestion.candidate_id == item.id)))


def store_matches(db: Session, item: Item, matches: list[tuple[Item, float, str]], method: str) -> None:
    """Replace the item's suggestions with `matches` (candidate, score, reason),
    in both directions."""
    clear_matches(db, item)
    for candidate, score, reason in matches:
        score = round(min(max(float(score), 0.0), 1.0), 4)
        db.add(MatchSuggestion(item_id=item.id, candidate_id=candidate.id, score=score,
                               reason=reason, method=method))
        db.add(MatchSuggestion(item_id=candidate.id, candidate_id=item.id, score=score,
                               reason=reason, method=method))


def rule_scores(db: Session, item: Item) -> list[tuple[float, Item, float]]:
    """Rule-based scores for every plausible candidate, best first:
    (score, candidate, text similarity). Nothing is saved."""
    settings = get_settings()
    candidates = candidates_for(db, item)
    scored = []
    for candidate, text_sim in zip(candidates, text_similarities(item, candidates)):
        score = (WEIGHT_TEXT * text_sim
                 + WEIGHT_CATEGORY * (candidate.category_id == item.category_id)
                 + WEIGHT_DATE * _date_score(item.happened_on, candidate.happened_on))
        # Same category alone isn't enough: the text must share something.
        if text_sim >= 0.15 and score >= settings.match_min_score:
            scored.append((round(min(score, 1.0), 4), candidate, text_sim))
    scored.sort(key=lambda row: row[0], reverse=True)
    return scored


def compute_matches(db: Session, item: Item, scored: list | None = None) -> list[tuple[float, Item, float]]:
    """The rule-based matching (also the fallback when the AI agent is
    unavailable). Replaces the item's suggestions; returns the saved ones."""
    if scored is None:
        scored = rule_scores(db, item)
    best = scored[: get_settings().match_top_k]
    # The reverse row (on the candidate's page) gets its own reason text below.
    clear_matches(db, item)
    for score, candidate, text_sim in best:
        db.add(MatchSuggestion(item_id=item.id, candidate_id=candidate.id, score=score,
                               reason=_reason(item, candidate, text_sim), method="basic"))
        db.add(MatchSuggestion(item_id=candidate.id, candidate_id=item.id, score=score,
                               reason=_reason(candidate, item, text_sim), method="basic"))
    return best


def analyse_item(db: Session, job: AiJob) -> None:
    """Job handler: embed the item, then refresh its matches."""
    item = db.get(Item, job.item_id)
    if item is None:
        return
    # Phase C: describe new photos first, so the description is part of the
    # text that gets embedded and matched.
    from app.services.ai import vision  # here, not at the top: vision imports agent

    vision.describe_photos(db, item, job)

    embedder = get_embedder()
    item.embedding = embedder.embed([item_text(item)])[0]
    item.embedding_model = embedder.name
    db.flush()

    # The AI agent ranks the candidates (agent.py). Without an LLM (no key,
    # quota used up, provider down), the rule-based matching runs instead.
    from app.services.ai import (  # here, not at the top: agent imports this module
        agent,
        idcard,
    )

    # Phase D, before matching: a found ID card / document is read with local
    # OCR (once per set of photos), and its owner's email is queued first, so
    # they get the "your ID card" email instead of a general match email.
    after_commit: list = []
    if idcard.should_scan(item):
        idcard.scan(db, item)
    idcard.send_due_alerts(db, item, after_commit)  # only once a guard holds it

    after_commit += agent.match_with_agent(db, item, job)

    # After matching: the certain link to the owner's own lost report.
    idcard.relink(db, item)
    item.ai_status = "done"
    item.ai_updated_at = now()
    db.commit()
    for send in after_commit:  # emails go out only once everything is saved
        send()


def _analysis_failed(db: Session, job: AiJob) -> None:
    item = db.get(Item, job.item_id) if job.item_id else None
    if item is not None:
        item.ai_status = "failed"
        item.ai_updated_at = now()


queue.HANDLERS[JOB_KIND] = analyse_item
queue.FAILURE_HANDLERS[JOB_KIND] = _analysis_failed


class SemanticMatcher:
    """Plugs into services/matching.py (MATCHING_BACKEND=semantic)."""

    enabled = True

    def find_similar(self, db: Session, item: Item, limit: int) -> list[Match]:
        rows = db.scalars(
            select(MatchSuggestion).where(MatchSuggestion.item_id == item.id)
            .order_by(MatchSuggestion.score.desc()).limit(limit * 2)
        )
        return [Match(row.candidate_id, row.score, row.reason, row.method) for row in rows]

    def item_saved(self, db: Session, item: Item) -> None:
        """Queue (re)analysis. Called after a report is created or edited,
        a photo changes, or a guard receives the item."""
        queue.enqueue(db, JOB_KIND, item.id)
        item.ai_status = "pending"
        db.commit()

    def item_removed(self, item_id: int) -> None:
        pass  # suggestions and jobs are deleted with the item (ON DELETE CASCADE)

    def status(self, db: Session, item: Item) -> str:
        """For the item page: "pending" (still working), "ready", "failed"."""
        if item.ai_status == "pending":
            return "pending"
        if item.ai_status == "failed":
            return "failed"
        return "ready"
