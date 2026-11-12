"""The help assistant in the sidebar: answers questions about TraceBack and
can look things up for the person asking.

Same language models as the matching agent, in the same order (OpenAI,
then Gemini, then Groq; see llm.py), and the same daily AI limit.

What it may do (tools, all read-only):
  - search_found_items  the Found page's own search: only items that are
                        held at an office, the same fields any student sees
  - my_reports          the asker's OWN reports and claims, with status
  - offices             the open security offices, hours and directions
What it can never do: change anything, approve or reject claims, or see
other people's names, emails, phone or roll numbers, claims or proofs.
The browser keeps the conversation; nothing of it is stored on the server.
Each question is logged in agent_runs as counts only (no message text), so
it counts towards AI_DAILY_CALL_LIMIT like every other AI call.
"""

import json
import logging
import threading
import time
from collections import defaultdict, deque

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.clock import now
from app.core.config import get_settings
from app.core.errors import AppError
from app.models import AgentRun, Claim, Item, ItemKind, Office, User
from app.services import items as items_service
from app.services.ai import llm

log = logging.getLogger(__name__)

MAX_HISTORY = 12          # messages of the conversation sent to the model
MAX_MESSAGE_CHARS = 1000  # per message from the user
MAX_RESULTS = 6

SYSTEM = """You are the help assistant of {portal}, the IIT Kanpur Lost & Found portal.
Answer in plain, friendly English, in at most about 120 words. Today is {today}.
The person asking is signed in as a {role}.

How {portal} works:
- Lost something: "Report an item" -> Lost. Describe it well (brand, colour, marks). The AI
  matches it against found items and emails the owner when a strong match is held at an office.
- Found something: "Report an item" -> Found, then hand it in at the security office chosen in
  the report. Until a guard receives it, it is not shown to other students.
- To claim a found item: open it, press "This is mine", and describe a private detail only the
  owner would know (you may add proof photos). A security guard reviews the claim; they may ask
  you to visit the office. If approved, collect it within 7 days with your student ID card.
- Found ID cards are read on the server (never sent to an AI); the owner is emailed when a
  guard receives the card.
- "My reports" and "My claims" in the sidebar show the status of everything you reported or claimed.

Rules:
- Use the tools to look things up; never invent items, references, offices or statuses.
- When you mention an item or claim, give its reference (like FND-0012 or CLM-0003).
- You cannot change anything: you cannot approve, reject, claim or hand over items. Say how
  the person can do it in the app instead.
- Never ask for or reveal personal details (phone numbers, roll numbers, emails, addresses).
- Ownership is decided only by a guard, never by you.
- If you don't know, say so and suggest the security office.
"""

TOOLS = [
    {"type": "function", "function": {
        "name": "search_found_items",
        "description": "Search found items currently held at a security office (what the Found page shows).",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Words to search for, e.g. 'black boat earphones'"},
            "category": {"type": "string", "description": "Optional category slug, e.g. electronics"},
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "my_reports",
        "description": "The asker's own lost and found reports and their claims, with current status.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "offices",
        "description": "Open security offices with opening hours and directions.",
        "parameters": {"type": "object", "properties": {}}}},
]


# ---------------- per-user rate limit (per API process) ----------------

_recent: dict[int, deque] = defaultdict(deque)
_lock = threading.Lock()


def _check_rate(user_id: int) -> None:
    limit = get_settings().assistant_messages_per_hour
    cutoff = time.monotonic() - 3600
    with _lock:
        recent = _recent[user_id]
        while recent and recent[0] < cutoff:
            recent.popleft()
        if len(recent) >= limit:
            raise AppError(429, "You've asked a lot of questions this hour. Please try again a little later.")
        recent.append(time.monotonic())


def reset_rate_limits() -> None:
    """Tests."""
    with _lock:
        _recent.clear()


# ---------------- tools ----------------

def _item_row(item: Item) -> dict:
    office = item.current_office
    return {"ref": item.ref, "title": item.title,
            "category": item.category.name if item.category else None,
            "colour": item.colour, "place": item.location_text,
            "date": item.happened_on.isoformat(), "status": item.status.value,
            "held_at": office.name if office else None}


def _search(db: Session, args: dict) -> list[dict]:
    query = str(args.get("query") or "")[:100]
    category = str(args.get("category") or "")[:60] or None
    page = items_service.list_public(db, ItemKind.FOUND, category, query, 1, MAX_RESULTS)
    return [_item_row(item) for item in page["items"]]


def _mine(db: Session, user: User) -> dict:
    reports = db.scalars(
        select(Item).where(Item.reporter_id == user.id).order_by(Item.created_at.desc()).limit(10)
        .options(selectinload(Item.category), selectinload(Item.current_office))).all()
    claims = db.scalars(
        select(Claim).where(Claim.claimant_id == user.id).order_by(Claim.created_at.desc()).limit(10)
        .options(selectinload(Claim.item))).all()
    return {
        "reports": [{"kind": item.kind.value, **_item_row(item)} for item in reports],
        "claims": [{"ref": claim.ref, "item": claim.item.ref, "item_title": claim.item.title,
                    "status": claim.status.value,
                    "collect_by": claim.collect_by.date().isoformat() if claim.collect_by else None}
                   for claim in claims],
    }


def _offices(db: Session) -> list[dict]:
    rows = db.scalars(select(Office).where(Office.is_active).order_by(Office.is_main.desc(), Office.name))
    return [{"name": office.name, "where": office.directions, "hours": office.opening_hours,
             "main": office.is_main} for office in rows]


def _run_tool(db: Session, user: User, name: str, raw_args: str) -> str:
    try:
        args = json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            args = {}
    except ValueError:
        args = {}
    if name == "search_found_items":
        result = _search(db, args)
    elif name == "my_reports":
        result = _mine(db, user)
    elif name == "offices":
        result = _offices(db)
    else:
        result = {"error": f"unknown tool {name}"}
    return json.dumps(result, ensure_ascii=False, default=str)


# ---------------- the conversation ----------------

def _clean(messages: list[dict]) -> list[dict]:
    """Only user/assistant text from the browser; the last MAX_HISTORY of them."""
    out = []
    for message in messages[-MAX_HISTORY:]:
        role = message.get("role")
        text = str(message.get("content") or "").strip()[:MAX_MESSAGE_CHARS]
        if role in ("user", "assistant") and text:
            out.append({"role": role, "content": text})
    return out


def answer(db: Session, user: User, messages: list[dict]) -> dict:
    settings = get_settings()
    if not settings.assistant_enabled:
        raise AppError(503, "The assistant is switched off.")
    history = _clean(messages)
    if not history or history[-1]["role"] != "user":
        raise AppError(400, "Ask a question first.")
    client = llm.get_llm()
    if client is None:
        raise AppError(503, "The assistant isn't available right now: no AI service is set up.")
    if llm.calls_today(db) >= settings.ai_daily_call_limit:
        raise AppError(503, "The assistant has reached today's limit. Please try again tomorrow.")
    _check_rate(user.id)

    convo = [{"role": "system", "content": SYSTEM.format(
        portal=settings.portal_name, today=now().date().isoformat(), role=user.role.value.lower())}] + history
    started, steps, calls = now(), [], 0
    provider = model = None
    reply, outcome, error = "", "ok", None
    try:
        for _ in range(settings.assistant_max_turns):
            message, provider, model = client.chat(convo, TOOLS)
            calls += 1
            convo.append(message)
            if not message.get("tool_calls"):
                reply = message.get("content", "").strip()
                break
            for call in message["tool_calls"]:
                name = call["function"]["name"]
                steps.append({"tool": name})  # names only: no message text is stored
                convo.append({"role": "tool", "tool_call_id": call["id"],
                              "content": _run_tool(db, user, name, call["function"]["arguments"])})
        else:
            reply = "Sorry, I couldn't finish looking that up. Please ask in a simpler way."
    except llm.LLMUnavailable as exc:
        outcome, error = "error", str(exc)[:500]
        reply = ""
    finally:
        db.add(AgentRun(item_id=None, outcome=outcome, provider=provider, model=model, llm_calls=max(calls, 1),
                        steps=steps, summary="assistant", error=error, started_at=started, finished_at=now()))
        db.commit()
    if not reply:
        raise AppError(503, "The assistant can't answer right now. Please try again in a minute.")
    return {"reply": reply[:4000], "provider": provider}
