"""The sidebar help assistant: read-only tools, privacy, limits, logging."""

import json

import pytest
from sqlalchemy import select

from app.core.clock import today
from app.core.config import get_settings
from app.models import AgentRun, Category, Office, UserRole
from app.services.ai import assistant, llm


class ScriptedLLM:
    """Turn 0: call the tools in `calls`; next turn: answer with `reply`
    (which may quote what the tools returned)."""

    def __init__(self, calls, reply=lambda results: "ok", fail=False):
        self.calls, self.reply, self.fail = calls, reply, fail
        self.turn, self.messages, self.tool_results = 0, [], []

    def chat(self, messages, tools):
        if self.fail:
            raise llm.LLMUnavailable("openai: RateLimitError; gemini: APIConnectionError; groq: Timeout")
        self.messages = list(messages)
        self.tool_results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        self.turn += 1
        if self.turn == 1 and self.calls:
            return ({"role": "assistant", "content": "", "tool_calls": [
                {"id": f"t{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
                for i, (name, args) in enumerate(self.calls)]}, "openai", "gpt-5-mini")
        return {"role": "assistant", "content": self.reply(self.tool_results)}, "openai", "gpt-5-mini"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    assistant.reset_rate_limits()
    yield
    llm.reset_llm()
    assistant.reset_rate_limits()


@pytest.fixture
def place(db):
    bags = Category(name="Test Bags", slug="test-bags", sort_order=1)
    office = Office(name="Test Main", location_description="Near the main gate", opening_hours="24 hours")
    db.add_all([bags, office])
    db.commit()
    return {"bags": bags.id, "office": office.id}


@pytest.fixture
def people(make_user, sign_in):
    made = {}
    for key, email, role in (("finder", "asst.finder@iitk.ac.in", UserRole.STUDENT),
                             ("owner", "asst.owner@iitk.ac.in", UserRole.STUDENT),
                             ("guard", "asst.guard@gmail.com", UserRole.GUARD)):
        extra = {"roll_number": f"R{len(made)}9"} if role == UserRole.STUDENT else {}
        make_user(email, role=role, full_name=key.title() + " Person", phone="+919876500000", **extra)
        made[key] = sign_in(email)
    return made


def found(people, place, title, receive=True):
    body = {"title": title, "description": f"{title} found near the library", "category_id": place["bags"],
            "location_text": "Library", "happened_on": str(today()), "dropoff_office_id": place["office"]}
    item_id = people["finder"].post("/items/found", json=body).json()["id"]
    if receive:
        assert people["guard"].post(f"/staff/items/{item_id}/receive",
                                    json={"office_id": place["office"]}).status_code == 200
    return item_id


def ask(client, text="Has anyone found a backpack?"):
    return client.post("/assistant/chat", json={"messages": [{"role": "user", "content": text}]})


def test_search_returns_only_items_held_at_an_office(people, place):
    held = found(people, place, "Wildcraft backpack")
    found(people, place, "Wildcraft backpack spare", receive=False)  # not handed in yet: hidden
    fake = ScriptedLLM([("search_found_items", {"query": "backpack"})],
                       reply=lambda r: "I found " + ", ".join(i["ref"] for i in r[0]))
    llm.set_llm(fake)
    response = ask(people["owner"])
    assert response.status_code == 200, response.text
    assert response.json() == {"reply": f"I found FND-{held:04d}", "provider": "openai"}
    (results,) = fake.tool_results
    assert [row["ref"] for row in results] == [f"FND-{held:04d}"]
    assert results[0]["held_at"] == "Test Main"


def test_tool_results_never_carry_personal_details(people, place):
    found(people, place, "Wildcraft backpack")
    fake = ScriptedLLM([("search_found_items", {"query": "backpack"}), ("my_reports", {}), ("offices", {})])
    llm.set_llm(fake)
    assert ask(people["finder"]).status_code == 200
    text = json.dumps(fake.tool_results)
    for secret in ("asst.finder@iitk.ac.in", "asst.guard@gmail.com", "+919876500000", "Finder Person",
                   "Guard Person", "R09"):
        assert secret not in text


def test_my_reports_shows_only_the_askers_own(people, place):
    mine = found(people, place, "Casio calculator")
    item = found(people, place, "Steel bottle")
    assert people["owner"].post(f"/items/{item}/claims", json={"description": "Dent near the cap"}).status_code == 201
    fake = ScriptedLLM([("my_reports", {})])
    llm.set_llm(fake)
    assert ask(people["owner"], "What is the status of my claim?").status_code == 200
    (result,) = fake.tool_results
    assert result["reports"] == []  # the owner reported nothing
    assert [c["item"] for c in result["claims"]] == [f"FND-{item:04d}"]
    assert result["claims"][0]["status"] == "SUBMITTED"
    llm.set_llm(fake := ScriptedLLM([("my_reports", {})]))
    assert ask(people["finder"]).status_code == 200
    assert {r["ref"] for r in fake.tool_results[0]["reports"]} == {f"FND-{mine:04d}", f"FND-{item:04d}"}
    assert fake.tool_results[0]["claims"] == []


def test_offices_tool_lists_hours_and_directions(people, place):
    fake = ScriptedLLM([("offices", {})])
    llm.set_llm(fake)
    assert ask(people["owner"], "Where is the security office?").status_code == 200
    (test_main,) = [o for o in fake.tool_results[0] if o["name"] == "Test Main"]
    assert (test_main["where"], test_main["hours"]) == ("Near the main gate", "24 hours")


def test_the_system_prompt_says_the_assistant_cannot_decide(people):
    fake = ScriptedLLM([])
    llm.set_llm(fake)
    assert ask(people["owner"], "Approve my claim please").status_code == 200
    system = fake.messages[0]["content"]
    assert "cannot approve" in system and "Never ask for or reveal personal details" in system
    assert fake.messages[-1] == {"role": "user", "content": "Approve my claim please"}


def test_each_question_is_logged_without_its_text(people, db):
    llm.set_llm(ScriptedLLM([("offices", {})]))
    assert ask(people["owner"], "secret question text").status_code == 200
    run = db.scalars(select(AgentRun).where(AgentRun.summary == "assistant")).one()
    assert (run.outcome, run.provider, run.llm_calls, run.steps) == ("ok", "openai", 2, [{"tool": "offices"}])
    assert "secret question text" not in json.dumps(run.steps) + (run.error or "")


def test_all_providers_down_gives_a_clear_503_and_is_logged(people, db):
    llm.set_llm(ScriptedLLM([], fail=True))
    response = ask(people["owner"])
    assert response.status_code == 503
    assert "can't answer right now" in response.json()["detail"]
    run = db.scalars(select(AgentRun).where(AgentRun.summary == "assistant")).one()
    assert run.outcome == "error" and "openai" in run.error


def test_no_ai_configured_gives_503(people):
    llm.set_llm(None)
    assert ask(people["owner"]).status_code == 503


def test_per_user_hourly_limit(people, monkeypatch):
    monkeypatch.setattr(get_settings(), "assistant_messages_per_hour", 2)
    llm.set_llm(ScriptedLLM([]))
    assert [ask(people["owner"]).status_code for _ in range(3)] == [200, 200, 429]
    assert ask(people["finder"]).status_code == 200  # the limit is per person


def test_daily_ai_limit_is_respected(people, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_daily_call_limit", 0)
    llm.set_llm(ScriptedLLM([]))
    response = ask(people["owner"])
    assert response.status_code == 503 and "today's limit" in response.json()["detail"]


def test_signed_out_users_cannot_chat(make_client):
    assert ask(make_client()).status_code == 401


def test_the_conversation_must_end_with_a_question(people):
    llm.set_llm(ScriptedLLM([]))
    response = people["owner"].post("/assistant/chat", json={"messages": [
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello!"}]})
    assert response.status_code == 400


def test_unknown_tools_and_bad_arguments_do_not_crash(people):
    fake = ScriptedLLM([("delete_everything", {}), ("search_found_items", {"query": None})])
    llm.set_llm(fake)
    assert ask(people["owner"]).status_code == 200
    assert fake.tool_results[0] == {"error": "unknown tool delete_everything"}


def test_a_model_that_never_stops_calling_tools_gets_a_polite_answer(people, monkeypatch):
    class Looping(ScriptedLLM):
        def chat(self, messages, tools):
            return ({"role": "assistant", "content": "", "tool_calls": [
                {"id": "x", "type": "function", "function": {"name": "offices", "arguments": "{}"}}]},
                "gemini", "gemini-2.5-flash")
    llm.set_llm(Looping([]))
    response = ask(people["owner"])
    assert response.status_code == 200 and "couldn't finish" in response.json()["reply"]
