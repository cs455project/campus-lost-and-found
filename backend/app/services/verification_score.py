"""The deterministic claim-verification score (US-22, FR-SCORE-01..04).

    C = 50·M + 20·R + 30·H        (0 to 100)

    M  match fraction    = details a guard marked MATCH / all details on the claim
    R  recency factor    = 1 - d / 30, never below 0, where d is the number of
                           days between the date the item was found and the
                           date the claim was sent (claim within the same day = 1)
    H  history factor    = min(n, 3) / 3, where n is the number of found items
                           the claimant has handed in that were later verified
                           and returned to their owner by a guard

What the score is for: ordering the Claims desk so guards look at the most
convincing claims first. What it is NOT: a decision. It never approves,
rejects or hands over anything (BR-04, FR-SCORE-04); only a guard does.

Independence from AI (FR-SCORE-02/03): this module uses nothing from the AI
matching code (services/matching.py) and the matcher never calls this one.
The weights are fixed constants below, not settings, so no model, prompt or
config can change them. tests/test_verification_score.py checks all of this.

Reproducibility: R and H are frozen when the claim is sent, factors are
stored rounded to 4 decimals, and C is computed from those stored factors,
so anyone can recompute C from the claim row and get the same number.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

from app.core.clock import now
from app.models import Claim, DetailVerdict

WEIGHT_MATCH: Final = 50
WEIGHT_RECENCY: Final = 20
WEIGHT_HISTORY: Final = 30
WEIGHTS: Final = {"M": WEIGHT_MATCH, "R": WEIGHT_RECENCY, "H": WEIGHT_HISTORY}

RECENCY_WINDOW_DAYS: Final = 30  # a claim this many days after the find gets R = 0
HISTORY_CAP: Final = 3  # three verified returns already give the full H = 1


@dataclass(frozen=True)
class Breakdown:
    m: float
    r: float
    h: float
    score: float


def _round_factor(value: float) -> float:
    return round(value, 4)


def match_fraction(verdicts: Sequence[DetailVerdict | None]) -> float:
    """Unchecked details count as not matched: M only grows as a guard
    confirms details. No details at all gives M = 0."""
    if not verdicts:
        return 0.0
    matched = sum(1 for verdict in verdicts if verdict == DetailVerdict.MATCH)
    return _round_factor(matched / len(verdicts))


def recency_factor(found_on: date, claimed_on: date) -> float:
    days = max(0, (claimed_on - found_on).days)
    return _round_factor(max(0.0, 1 - days / RECENCY_WINDOW_DAYS))


def history_factor(verified_returns: int) -> float:
    return _round_factor(min(max(verified_returns, 0), HISTORY_CAP) / HISTORY_CAP)


def compute(m: float, r: float, h: float) -> float:
    """C = 50M + 20R + 30H, rounded to 2 decimals. Factors must be in 0..1."""
    for name, value in (("M", m), ("R", r), ("H", h)):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be between 0 and 1, got {value}")
    return round(WEIGHT_MATCH * m + WEIGHT_RECENCY * r + WEIGHT_HISTORY * h, 2)


def breakdown(verdicts: Sequence[DetailVerdict | None], r: float, h: float) -> Breakdown:
    m = match_fraction(verdicts)
    return Breakdown(m=m, r=r, h=h, score=compute(m, r, h))


# ---------------- Applying it to a claim (no commit) ----------------


def _store(claim: Claim, result: Breakdown) -> None:
    claim.score_m, claim.score_r, claim.score_h = result.m, result.r, result.h
    claim.score = result.score
    claim.scored_at = now()


def score_new_claim(claim: Claim, found_on: date, claimed_on: date, verified_returns: int) -> Breakdown:
    """When a claim is sent: freeze R and H, compute M from its details."""
    result = breakdown([d.verdict for d in claim.details],
                       recency_factor(found_on, claimed_on), history_factor(verified_returns))
    _store(claim, result)
    return result


def rescore(claim: Claim) -> Breakdown:
    """After a guard checks a detail: recompute M; R and H stay as frozen."""
    result = breakdown([d.verdict for d in claim.details], claim.score_r or 0.0, claim.score_h or 0.0)
    _store(claim, result)
    return result
