"""Tests for AI Phase B: the Recovery Agent.

A FakeLLM plays the model's side of the conversation (which tools to call,
with which arguments), so these tests never use a real API or its quota.
What's tested is OUR side: the tools, the guardrails, the fallback to the
rule-based matching, the audit log, and the owner email.
"""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import today
from app.models import AgentRun, Category, Item, MatchSuggestion, Office, UserRole
from app.services import email as email_service
from app.services.ai import llm, queue


@pytest.fixture
def place(db):
    bags = Category(name="AI Bags", slug="ai-bags", sort_order=1)
    bottles = Category(name="AI Bottles", slug="ai-bottles", sort_order=2)
    office = Office(name="AI Office", location_description="Gate", opening_hours="24 hours")
    db.add_all([bags, bottles, office])
    db.commit()
    return {"bags": bags.id, "bottles": bottles.id, "office": office.id}


@pytest.fixture
def person(make_user, sign_in):
    count = {"n": 0}

    def _person(role=UserRole.STUDENT):
        count["n"] += 1
        email = f"agentperson{count['n']}@iitk.ac.in"
        extra = {"roll_number": f"G{count['n']:04d}"} if role == UserRole.STUDENT else {}
        make_user(email, role=role, full_name=f"Person {count['n']}", **extra)
        return sign_in(email)

    return _person


@pytest.fixture
def emails(monkeypatch):
    sent = []
    monkeypatch.setattr(email_service, "send",
                        lambda to, mail: sent.append((to, mail.subject, email_service.render_text(mail))))
    return sent


BACKPACK = {"title": "Black Wildcraft backpack", "colour": "Black", "brand": "Wildcraft",
            "description": "Black Wildcraft backpack with a laptop sleeve."}
BOTTLE = {"title": "Blue Milton bottle", "colour": "Blue", "brand": "Milton",
          "description": "Blue steel bottle with a dent."}


def body(place, details, category="bags", **extra):
    return {**details, "category_id": place[category], "location_text": "LHC",
            "happened_on": str(today()), **extra}


def report_held(finder, guard, place, details=BACKPACK, **kw):
    item_id = finder.post("/items/found", json=body(place, details, dropoff_office_id=place["office"], **kw)).json()["id"]
    guard.post(f"/staff/items/{item_id}/receive", json={"office_id": place["office"]})
    return item_id


class FakeLLM:
    """Plays a scripted model. `script` is a function(turn, last_tool_result,
    messages) returning the tool calls to make this turn: [(name, args), ...]."""

    def __init__(self, script, fail=False):
        self.script, self.fail = script, fail
        self.turn, self.seen_messages = 0, []

    def chat(self, messages, tools):
        if self.fail:
            raise llm.LLMUnavailable("groq: RateLimitError; gemini: APIConnectionError")
        self.seen_messages = list(messages)
        last = json.loads(messages[-1]["content"]) if messages[-1]["role"] == "tool" else None
        calls = self.script(self.turn, last, messages)
        self.turn += 1
        return ({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{self.turn}_{i}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}
            for i, (name, args) in enumerate(calls)]}, "fake", "fake-model")


@pytest.fixture
def mode(monkeypatch):
    """Set AGENT_MATCHING for one test."""
    from app.core.config import get_settings

    def _set(value):
        monkeypatch.setattr(get_settings(), "agent_matching", value)
    return _set


@pytest.fixture
def use_llm(mode):
    """Plug in a fake LLM. Most tests use AGENT_MATCHING=always so the agent
    runs whatever the scores; the mode tests below change it."""
    mode("always")

    def _use(client):
        llm.set_llm(client)
        return client
    yield _use
    llm.reset_llm()


def good_agent(score=0.9, categories=("AI Bags",)):
    """Search with filters, save every result with `score`, notify, finish."""
    state = {}

    def script(turn, last, messages):
        if turn == 0:
            return [("search_candidates", {"categories": list(categories), "colours": ["black"],
                                           "keywords": ["wildcraft"]})]
        if turn == 1:
            state["ids"] = [r["id"] for r in last["results"]]
            report = json.loads(messages[1]["content"])["report"]
            return [("save_matches", {"matches": [{"id": i, "score": score, "reason": "Same Wildcraft brand and black colour"}
                                                  for i in state["ids"]]})]
        if turn == 2 and state["ids"]:
            report = json.loads(messages[1]["content"])["report"]
            lost, found = (report["id"], state["ids"][0]) if report["kind"] == "LOST" else (state["ids"][0], report["id"])
            return [("notify_lost_owner", {"lost_item_id": lost, "found_item_id": found})]
        return [("finish", {"summary": "done"})]

    return script


def test_agent_ranks_matches_with_its_own_reasons(db, person, place, use_llm, emails):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    found_id = report_held(finder, guard, place)
    queue.run_all(db)  # no LLM yet: the found item is analysed with the rules
    fake = use_llm(FakeLLM(good_agent()))
    lost_id = owner.post("/items/lost", json=body(place, BACKPACK)).json()["id"]
    queue.run_all(db)

    result = owner.get(f"/items/{lost_id}/similar").json()
    assert [m["item"]["id"] for m in result["matches"]] == [found_id]
    assert result["matches"][0]["reason"] == "Same Wildcraft brand and black colour, found the same day it was lost"
    assert result["matches"][0]["method"] == "ai"

    run = db.scalar(select(AgentRun).where(AgentRun.item_id == lost_id))
    assert run.outcome == "ok" and run.llm_calls == 4 and run.model == "fake-model"
    assert [step["tool"] for step in run.steps] == ["search_candidates", "save_matches", "notify_lost_owner", "finish"]
    # Nothing personal was ever sent to the model.
    sent_text = json.dumps(fake.seen_messages)
    assert "agentperson" not in sent_text and "@" not in sent_text


def test_owner_is_emailed_once_about_a_strong_match(db, person, place, use_llm, emails):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    report_held(finder, guard, place)
    use_llm(FakeLLM(good_agent()))
    lost_id = owner.post("/items/lost", json=body(place, BACKPACK)).json()["id"]
    queue.run_all(db)
    assert len(emails) == 1
    to, subject, text = emails[0]
    assert to == "agentperson1@iitk.ac.in" and subject.startswith("A found item may match your lost report")
    assert "AI Office" in text

    # Editing the report re-runs the agent; the same match isn't emailed again.
    use_llm(FakeLLM(good_agent()))
    owner.put(f"/items/{lost_id}", json=body(place, {**BACKPACK, "description": "Black Wildcraft bag."}))
    queue.run_all(db)
    assert len(emails) == 1


def test_weak_matches_are_never_emailed(db, person, place, use_llm, emails):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    report_held(finder, guard, place)
    use_llm(FakeLLM(good_agent(score=0.5)))
    owner.post("/items/lost", json=body(place, BACKPACK))
    queue.run_all(db)
    assert emails == []


def test_agent_cannot_save_ids_it_was_not_shown(db, person, place, use_llm, emails):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    found_id = report_held(finder, guard, place)
    queue.run_all(db)
    other = report_held(finder, guard, place, BOTTLE, category="bottles")  # not in the search below
    queue.run_all(db)

    def script(turn, last, messages):
        if turn == 0:
            return [("search_candidates", {"categories": ["AI Bags"]})]
        if turn == 1:
            return [("save_matches", {"matches": [{"id": other, "score": 0.99, "reason": "made up"},
                                                  {"id": 999999, "score": 0.99, "reason": "made up"},
                                                  {"id": found_id, "score": 0.8, "reason": "real"}]})]
        return [("finish", {})]

    use_llm(FakeLLM(script))
    lost_id = owner.post("/items/lost", json=body(place, BACKPACK)).json()["id"]
    queue.run_all(db)
    assert [m["item"]["id"] for m in owner.get(f"/items/{lost_id}/similar").json()["matches"]] == [found_id]


def test_agent_filters_by_the_categories_it_chooses(db, person, place, use_llm):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    report_held(finder, guard, place)
    queue.run_all(db)
    results = {}

    def script(turn, last, messages):
        if turn == 0:
            return [("search_candidates", {"categories": ["AI Bottles"]})]
        results["count"] = last["count"]
        return [("save_matches", {"matches": []}), ("finish", {})]

    use_llm(FakeLLM(script))
    owner.post("/items/lost", json=body(place, BACKPACK))
    queue.run_all(db)
    assert results["count"] == 0  # the backpack is in "AI Bags", so filtered out


def test_provider_failure_falls_back_to_rule_based_matching(db, person, place, use_llm):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    found_id = report_held(finder, guard, place)
    queue.run_all(db)
    use_llm(FakeLLM(good_agent(), fail=True))
    lost_id = owner.post("/items/lost", json=body(place, BACKPACK)).json()["id"]
    queue.run_all(db)

    result = owner.get(f"/items/{lost_id}/similar").json()
    assert result["status"] == "ready"
    assert [m["item"]["id"] for m in result["matches"]] == [found_id]
    assert result["matches"][0]["method"] == "basic"
    run = db.scalar(select(AgentRun).where(AgentRun.item_id == lost_id))
    assert run.outcome == "fallback" and "RateLimitError" in run.error


def test_daily_limit_stops_ai_calls(db, person, place, use_llm, monkeypatch):
    from app.core.config import get_settings

    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    report_held(finder, guard, place)
    queue.run_all(db)
    monkeypatch.setattr(get_settings(), "ai_daily_call_limit", 0)
    fake = use_llm(FakeLLM(good_agent()))
    lost_id = owner.post("/items/lost", json=body(place, BACKPACK)).json()["id"]
    queue.run_all(db)
    assert fake.turn == 0  # the model was never called
    run = db.scalar(select(AgentRun).where(AgentRun.item_id == lost_id))
    assert run.outcome == "fallback" and "limit" in run.error
    assert owner.get(f"/items/{lost_id}/similar").json()["matches"]  # rules still matched


def test_model_that_never_saves_keeps_the_rule_based_list(db, person, place, use_llm):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    found_id = report_held(finder, guard, place)
    queue.run_all(db)
    use_llm(FakeLLM(lambda turn, last, messages: [("finish", {"summary": "nothing"})]))
    lost_id = owner.post("/items/lost", json=body(place, BACKPACK)).json()["id"]
    queue.run_all(db)
    assert [m["item"]["id"] for m in owner.get(f"/items/{lost_id}/similar").json()["matches"]] == [found_id]


def test_no_llm_call_when_there_is_nothing_to_rank(db, person, place, use_llm):
    owner = person()
    fake = use_llm(FakeLLM(good_agent()))
    owner.post("/items/lost", json=body(place, BACKPACK))
    queue.run_all(db)
    assert fake.turn == 0


# ---------------- AGENT_MATCHING: when the agent is used ----------------


def test_off_mode_never_calls_the_llm(db, person, place, use_llm, mode):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    report_held(finder, guard, place)
    queue.run_all(db)
    mode("off")
    fake = use_llm(FakeLLM(good_agent()))
    lost_id = owner.post("/items/lost", json=body(place, BACKPACK)).json()["id"]
    queue.run_all(db)
    assert fake.turn == 0
    assert owner.get(f"/items/{lost_id}/similar").json()["matches"][0]["method"] == "basic"


def test_ambiguous_mode_skips_the_llm_for_a_clear_single_match(db, person, place, use_llm, mode):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    report_held(finder, guard, place)
    queue.run_all(db)
    mode("ambiguous")
    fake = use_llm(FakeLLM(good_agent()))
    owner.post("/items/lost", json=body(place, BACKPACK))
    queue.run_all(db)
    assert fake.turn == 0  # one clear winner: the maths is enough


def test_ambiguous_mode_asks_the_agent_when_scores_are_close(db, person, place, use_llm, mode):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    report_held(finder, guard, place)
    report_held(finder, guard, place)  # two identical candidates: the maths can't choose
    queue.run_all(db)
    mode("ambiguous")
    fake = use_llm(FakeLLM(good_agent()))
    lost_id = owner.post("/items/lost", json=body(place, BACKPACK)).json()["id"]
    queue.run_all(db)
    assert fake.turn > 0
    assert owner.get(f"/items/{lost_id}/similar").json()["matches"][0]["method"] == "ai"


def test_is_ambiguous_rules():
    from app.services.ai.agent import is_ambiguous

    assert is_ambiguous([]) is False
    assert is_ambiguous([(0.9, None, 0)]) is False  # one clear, strong match
    assert is_ambiguous([(0.5, None, 0)]) is True  # one middling match
    assert is_ambiguous([(0.9, None, 0), (0.85, None, 0)]) is True  # two close ones
    assert is_ambiguous([(0.9, None, 0), (0.6, None, 0)]) is False  # a clear winner


def test_without_the_agent_a_very_clear_match_still_emails_the_owner(db, person, place, use_llm, mode, emails):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    report_held(finder, guard, place)
    queue.run_all(db)
    mode("off")
    owner.post("/items/lost", json=body(place, BACKPACK))
    queue.run_all(db)
    assert [subject.split(" (")[0] for _, subject, _ in emails] == ["A found item may match your lost report"]


def test_ai_date_claims_are_replaced_by_the_real_date_difference(db, person, place, use_llm):
    """Language models get date arithmetic wrong (it once said "1 day later" for a
    14-day gap), so the app removes any date wording and adds the true gap itself."""
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    found_id = report_held(finder, guard, place)  # found today
    queue.run_all(db)

    def script(turn, last, messages):
        if turn == 0:
            return [("search_candidates", {})]
        if turn == 1:
            assert last["results"][0]["days_found_after_lost"] == 10  # facts come from code
            return [("save_matches", {"matches": [
                {"id": found_id, "score": 0.8, "reason": "Same black backpack with laptop sleeve, found 1 day later"}]})]
        return [("finish", {})]

    use_llm(FakeLLM(script))
    lost_id = owner.post("/items/lost", json={**body(place, BACKPACK),
                                              "happened_on": str(today() - timedelta(days=10))}).json()["id"]
    queue.run_all(db)
    reason = owner.get(f"/items/{lost_id}/similar").json()["matches"][0]["reason"]
    assert reason == "Same black backpack with laptop sleeve, found 10 days after it was lost"
