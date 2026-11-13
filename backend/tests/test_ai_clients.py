"""Tests for the clients that talk to AI services: the chat model with its
fallback provider (services/ai/llm.py), the photo-description client
(services/ai/vision.py) and the choice of text embedder
(services/ai/embeddings.py).

No real service is called: a fake OpenAI SDK records what would be sent and
answers like Groq or Gemini would."""

import base64
import io
import logging
import sys
import types
from datetime import date, timedelta
from types import SimpleNamespace
from typing import ClassVar

import numpy as np
import openai
import pytest
from PIL import Image
from sqlalchemy import delete, select

from app.core.clock import now, today
from app.core.config import get_settings
from app.models import (
    AgentRun,
    AiJob,
    Category,
    Item,
    ItemKind,
    ItemPhoto,
    ItemStatus,
    Office,
)
from app.services import storage
from app.services.ai import embeddings, idcard, llm, semantic, vision

# ---------------- a fake OpenAI SDK ----------------


def sdk_message(content=None, tool_calls=None):
    calls = [SimpleNamespace(id=c["id"], function=SimpleNamespace(name=c["name"], arguments=c.get("arguments")))
             for c in (tool_calls or [])]
    return SimpleNamespace(content=content, tool_calls=calls or None)


class FakeOpenAI:
    """Stands in for openai.OpenAI. `behaviour[base_url]` is either an
    exception to raise or a message to answer with."""

    behaviour: ClassVar[dict] = {}
    made: ClassVar[list] = []

    def __init__(self, api_key, base_url, timeout, max_retries):
        self.settings = {"api_key": api_key, "base_url": base_url, "timeout": timeout, "max_retries": max_retries}
        self.requests = []
        FakeOpenAI.made.append(self)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.requests.append(request)
        answer = FakeOpenAI.behaviour[self.settings["base_url"]]
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(choices=[SimpleNamespace(message=answer)])


@pytest.fixture
def fake_sdk(monkeypatch):
    FakeOpenAI.behaviour, FakeOpenAI.made = {}, []
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    return FakeOpenAI


GROQ = llm.Provider("groq", "https://groq.test/v1", "groq-key", "gpt-oss-20b")
GEMINI = llm.Provider("gemini", "https://gemini.test/v1", "gemini-key", "gemini-2.5-flash")
TOOLS = [{"type": "function", "function": {"name": "search", "parameters": {"type": "object"}}}]


# ---------------- which providers are configured ----------------


@pytest.fixture
def ai_settings(monkeypatch):
    settings = get_settings()
    for name, value in {"ai_enabled": True, "llm_api_key": "", "llm_model": "gpt-oss-20b",
                        "llm_fallback_api_key": "", "llm_fallback_model": "", "gemini_api_key": "",
                        "gemini_model": "gemini-2.5-flash", "openai_api_key": "",
                        "openai_model": "gpt-5-mini"}.items():
        monkeypatch.setattr(settings, name, value)
    return settings


def test_no_keys_means_no_providers(ai_settings):
    assert llm.configured_providers() == []


def test_openai_comes_first_then_gemini_then_groq(ai_settings, monkeypatch):
    monkeypatch.setattr(ai_settings, "llm_api_key", "groq-key")
    monkeypatch.setattr(ai_settings, "gemini_api_key", "gemini-key")
    monkeypatch.setattr(ai_settings, "openai_api_key", "openai-key")
    providers = llm.configured_providers()
    assert [(p.name, p.api_key, p.model) for p in providers] == [
        ("openai", "openai-key", "gpt-5-mini"), ("gemini", "gemini-key", "gemini-2.5-flash"),
        ("groq", "groq-key", "gpt-oss-20b")]
    assert providers[0].base_url == ai_settings.openai_base_url
    assert providers[1].base_url == ai_settings.llm_fallback_base_url


def test_without_an_openai_key_gemini_comes_first_then_groq(ai_settings, monkeypatch):
    monkeypatch.setattr(ai_settings, "llm_api_key", "groq-key")
    monkeypatch.setattr(ai_settings, "gemini_api_key", "gemini-key")
    assert [p.name for p in llm.configured_providers()] == ["gemini", "groq"]


def test_openai_gets_reasoning_off_and_no_temperature_the_others_a_temperature(fake_sdk):
    openai_p = llm.Provider("openai", "https://openai.test/v1", "k", "gpt-5-mini")
    fake_sdk.behaviour = {openai_p.base_url: sdk_message("ok"), GROQ.base_url: sdk_message("ok")}
    llm.LLMClient([openai_p]).chat([], TOOLS)
    llm.LLMClient([GROQ]).chat([], TOOLS)
    first, second = (made.requests[0] for made in fake_sdk.made)
    assert "temperature" not in first and first["reasoning_effort"] == "none"
    assert second["temperature"] == 0.2 and "reasoning_effort" not in second


def test_an_openai_model_without_reasoning_is_asked_again_without_it(fake_sdk):
    openai_p = llm.Provider("openai", "https://openai.test/v1", "k", "gpt-4o-mini")
    answers = iter([RuntimeError("400 Unrecognized request argument supplied: reasoning_effort"),
                    sdk_message("ok")])

    class Flaky(dict):
        def __getitem__(self, key):
            answer = next(answers)
            return answer

    fake_sdk.behaviour = Flaky()
    message, provider, _ = llm.LLMClient([openai_p]).chat([], TOOLS)
    assert (message["content"], provider) == ("ok", "openai")
    first, second = fake_sdk.made[0].requests
    assert first["reasoning_effort"] == "none" and "reasoning_effort" not in second


def test_other_openai_errors_are_not_retried(fake_sdk):
    openai_p = llm.Provider("openai", "https://openai.test/v1", "k", "gpt-5-mini")
    fake_sdk.behaviour = {openai_p.base_url: RuntimeError("401 invalid api key")}
    with pytest.raises(llm.LLMUnavailable, match="invalid api key"):
        llm.LLMClient([openai_p]).chat([], TOOLS)
    assert len(fake_sdk.made[0].requests) == 1


def test_explicit_fallback_settings_win_over_the_gemini_ones(ai_settings, monkeypatch):
    monkeypatch.setattr(ai_settings, "gemini_api_key", "gemini-key")
    monkeypatch.setattr(ai_settings, "llm_fallback_api_key", "other-key")
    monkeypatch.setattr(ai_settings, "llm_fallback_model", "other-model")
    assert [(p.name, p.api_key, p.model) for p in llm.configured_providers()] == [
        ("gemini", "other-key", "other-model")]


def test_a_key_without_a_model_is_not_used(ai_settings, monkeypatch):
    monkeypatch.setattr(ai_settings, "llm_api_key", "groq-key")
    monkeypatch.setattr(ai_settings, "llm_model", "")
    assert llm.configured_providers() == []


def test_switching_ai_off_ignores_all_keys(ai_settings, monkeypatch):
    monkeypatch.setattr(ai_settings, "llm_api_key", "groq-key")
    monkeypatch.setattr(ai_settings, "gemini_api_key", "gemini-key")
    monkeypatch.setattr(ai_settings, "ai_enabled", False)
    assert llm.configured_providers() == []


# ---------------- the chat client ----------------


def test_the_client_sends_tools_and_returns_the_answer_as_a_plain_dict(fake_sdk):
    fake_sdk.behaviour = {GROQ.base_url: sdk_message(None, [
        {"id": "call_1", "name": "search", "arguments": '{"query": "black bag"}'},
        {"id": "call_2", "name": "finish", "arguments": None}])}
    message, provider, model = llm.LLMClient([GROQ]).chat([{"role": "user", "content": "hi"}], TOOLS)
    assert (provider, model) == ("groq", "gpt-oss-20b")
    assert message == {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "search", "arguments": '{"query": "black bag"}'}},
        {"id": "call_2", "type": "function", "function": {"name": "finish", "arguments": "{}"}}]}
    (client,) = fake_sdk.made
    assert client.settings == {"api_key": "groq-key", "base_url": GROQ.base_url,
                               "timeout": llm.TIMEOUT_SECONDS, "max_retries": 1}
    (request,) = client.requests
    assert request["model"] == "gpt-oss-20b" and request["tools"] == TOOLS
    assert request["tool_choice"] == "auto" and request["temperature"] == 0.2


def test_a_plain_text_answer_has_no_tool_calls(fake_sdk):
    fake_sdk.behaviour = {GROQ.base_url: sdk_message("No match found.")}
    message, _, _ = llm.LLMClient([GROQ]).chat([], TOOLS)
    assert message == {"role": "assistant", "content": "No match found."}


def test_when_groq_fails_gemini_answers(fake_sdk, caplog):
    fake_sdk.behaviour = {GROQ.base_url: RuntimeError("429 rate limit"),
                          GEMINI.base_url: sdk_message("ok")}
    with caplog.at_level(logging.WARNING):
        message, provider, model = llm.LLMClient([GROQ, GEMINI]).chat([], TOOLS)
    assert (message["content"], provider, model) == ("ok", "gemini", "gemini-2.5-flash")
    assert "LLM provider groq failed: 429 rate limit" in caplog.text


def test_when_every_provider_fails_the_caller_is_told_why(fake_sdk):
    fake_sdk.behaviour = {GROQ.base_url: TimeoutError("read timed out"),
                          GEMINI.base_url: PermissionError("bad key")}
    with pytest.raises(llm.LLMUnavailable) as error:
        llm.LLMClient([GROQ, GEMINI]).chat([], TOOLS)
    assert str(error.value) == "groq: TimeoutError: read timed out; gemini: PermissionError: bad key"


def test_with_no_provider_the_client_is_unavailable(fake_sdk):
    with pytest.raises(llm.LLMUnavailable, match="No LLM provider configured"):
        llm.LLMClient([]).chat([], TOOLS)


@pytest.fixture
def fresh_llm():
    llm.reset_llm()
    yield
    llm.reset_llm()


def test_get_llm_is_none_without_keys_and_built_once_with_them(fake_sdk, monkeypatch, fresh_llm):
    monkeypatch.setattr(llm, "configured_providers", list)
    assert llm.get_llm() is None
    monkeypatch.setattr(llm, "configured_providers", lambda: [GROQ, GEMINI])
    client = llm.get_llm()
    assert isinstance(client, llm.LLMClient) and client.providers == [GROQ, GEMINI]
    assert llm.get_llm() is client  # built once, then reused
    llm.set_llm(None)  # tests can switch the LLM off...
    assert llm.get_llm() is None
    llm.reset_llm()  # ...and back to the settings
    assert isinstance(llm.get_llm(), llm.LLMClient)


def test_calls_today_counts_only_todays_llm_calls(db):
    db.execute(delete(AgentRun))
    db.add_all([AgentRun(outcome="ok", llm_calls=3), AgentRun(outcome="fallback", llm_calls=2),
                AgentRun(outcome="ok", llm_calls=7, started_at=now() - timedelta(days=2))])
    db.commit()
    assert llm.calls_today(db) == 5


def test_calls_today_is_zero_on_a_quiet_day(db):
    db.execute(delete(AgentRun))
    assert llm.calls_today(db) == 0


# ---------------- photo descriptions ----------------


@pytest.fixture
def fresh_vision():
    vision.reset_vision()
    yield
    vision.reset_vision()


def jpeg_bytes(colour=(30, 60, 90)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), colour).save(out, format="JPEG")
    return out.getvalue()


def test_the_vision_client_sends_the_report_and_each_photo(fake_sdk, fresh_vision):
    url = "https://gemini.test/v1"
    fake_sdk.behaviour = {url: sdk_message('```json\n{"object": "water bottle", "colour": "blue"}\n```')}
    client = vision.VisionClient(url, "gemini-key", "gemini-2.5-flash")
    photos = [jpeg_bytes(), jpeg_bytes((200, 0, 0))]
    answer = client.describe(photos, {"title": "Bottle", "place": "Gymkhana – near the café"})
    assert answer == {"object": "water bottle", "colour": "blue"}
    (request,) = fake_sdk.made[0].requests
    assert request["model"] == "gemini-2.5-flash" and request["temperature"] == 0.1
    (turn,) = request["messages"]
    text, *images = turn["content"]
    assert '"place": "Gymkhana – near the café"' in text["text"]  # report sent as readable JSON
    assert [part["type"] for part in images] == ["image_url", "image_url"]
    sent = [base64.b64decode(part["image_url"]["url"].removeprefix("data:image/jpeg;base64,"))
            for part in images]
    assert sent == photos


def test_an_answer_without_json_is_an_error(fake_sdk):
    url = "https://gemini.test/v1"
    fake_sdk.behaviour = {url: sdk_message("Sorry, I can't help with that.")}
    with pytest.raises(ValueError, match="did not answer with JSON"):
        vision.VisionClient(url, "k", "m").describe([jpeg_bytes()], {})


@pytest.mark.parametrize("change", [
    {"ai_enabled": False}, {"ai_photo_descriptions": False}, {"gemini_api_key": ""}, {"gemini_model": ""},
])
def test_photo_descriptions_need_ai_on_and_a_gemini_key(fake_sdk, fresh_vision, monkeypatch, change):
    settings = get_settings()
    for name, value in {"ai_enabled": True, "ai_photo_descriptions": True, "gemini_api_key": "gemini-key",
                        "gemini_model": "gemini-2.5-flash", "openai_api_key": "", **change}.items():
        monkeypatch.setattr(settings, name, value)
    assert vision.get_vision() is None


def test_the_vision_client_is_built_once_from_the_settings(fake_sdk, fresh_vision, monkeypatch):
    settings = get_settings()
    for name, value in {"ai_enabled": True, "ai_photo_descriptions": True, "gemini_api_key": "gemini-key",
                        "gemini_model": "gemini-2.5-flash", "openai_api_key": ""}.items():
        monkeypatch.setattr(settings, name, value)
    client = vision.get_vision()
    assert client.model == "gemini-2.5-flash"
    assert fake_sdk.made[0].settings["base_url"] == settings.llm_fallback_base_url
    assert fake_sdk.made[0].settings["api_key"] == "gemini-key"
    assert vision.get_vision() is client


def test_with_an_openai_key_photos_go_to_openai_first_then_gemini(fake_sdk, fresh_vision, monkeypatch):
    settings = get_settings()
    for name, value in {"ai_enabled": True, "ai_photo_descriptions": True, "gemini_api_key": "gemini-key",
                        "gemini_model": "gemini-2.5-flash", "openai_api_key": "openai-key",
                        "openai_model": "gpt-5-mini"}.items():
        monkeypatch.setattr(settings, name, value)
    chain = vision.get_vision()
    assert isinstance(chain, vision.VisionChain)
    assert [(c.provider, c.model) for c in chain.clients] == [("openai", "gpt-5-mini"),
                                                              ("gemini", "gemini-2.5-flash")]
    fake_sdk.behaviour = {settings.openai_base_url: RuntimeError("quota"),
                          settings.llm_fallback_base_url: sdk_message('{"object": "bottle"}')}
    assert chain.describe([jpeg_bytes()], {}) == {"object": "bottle"}
    assert (chain.provider, chain.model) == ("gemini", "gemini-2.5-flash")
    openai_request = fake_sdk.made[0].requests[0]
    assert "temperature" not in openai_request  # OpenAI's newer models refuse it
    fake_sdk.behaviour = {settings.openai_base_url: sdk_message('{"object": "bag"}')}
    assert chain.describe([jpeg_bytes()], {}) == {"object": "bag"} and chain.provider == "openai"


def test_when_every_vision_provider_fails_the_error_names_them(fake_sdk):
    a = vision.VisionClient("https://a.test/v1", "k", "m1", provider="openai")
    b = vision.VisionClient("https://b.test/v1", "k", "m2")
    fake_sdk.behaviour = {"https://a.test/v1": RuntimeError("quota"), "https://b.test/v1": RuntimeError("down")}
    with pytest.raises(RuntimeError, match="openai: RuntimeError: quota; gemini: RuntimeError: down"):
        vision.VisionChain([a, b]).describe([jpeg_bytes()], {})


class CountingVision:
    def __init__(self):
        self.calls = 0

    def describe(self, images, report):
        self.calls += 1
        return {"object": "bag"}


@pytest.fixture
def photographed_item(db, make_user):
    owner = make_user("vision.owner@iitk.ac.in", full_name="Owner", roll_number="V0001")
    bags = db.scalar(select(Category).where(Category.slug == "bags"))
    item = Item(kind=ItemKind.LOST, status=ItemStatus.OPEN, title="Black bag", description="Laptop bag",
                category_id=bags.id, location_text="Library", happened_on=today(), reporter_id=owner.id)
    db.add(item)
    db.flush()
    data = jpeg_bytes()
    db.add(ItemPhoto(item_id=item.id, stored_name=storage.save(data, "jpg"), content_type="image/jpeg",
                     size_bytes=len(data), uploaded_by_id=owner.id))
    db.commit()
    db.refresh(item)
    return item


def test_no_photo_description_once_the_daily_ai_limit_is_reached(db, photographed_item, monkeypatch, caplog,
                                                                  fresh_vision):
    client = CountingVision()
    vision.set_vision(client)
    monkeypatch.setattr(get_settings(), "ai_daily_call_limit", 10)
    monkeypatch.setattr(vision.llm_module, "calls_today", lambda db: 10)
    with caplog.at_level(logging.INFO):
        vision.describe_photos(db, photographed_item, None)
    assert client.calls == 0
    assert photographed_item.ai_description is None and photographed_item.ai_photos_key is None
    assert "Daily AI limit reached" in caplog.text

    monkeypatch.setattr(vision.llm_module, "calls_today", lambda db: 9)  # one call left
    vision.describe_photos(db, photographed_item, None)
    assert client.calls == 1


def test_no_photo_description_without_a_vision_client(db, photographed_item, fresh_vision):
    vision.set_vision(None)
    vision.describe_photos(db, photographed_item, None)
    assert photographed_item.ai_photos_key is None  # nothing described, so it will be tried again later


# ---------------- choosing the embedder ----------------


@pytest.fixture
def fresh_embedder():
    embeddings.set_embedder(None)
    yield
    embeddings.set_embedder(None)


def test_the_hashing_backend_is_used_when_chosen_and_loaded_once(monkeypatch, fresh_embedder):
    monkeypatch.setattr(get_settings(), "embedding_backend", "hashing")
    first = embeddings.get_embedder()
    assert isinstance(first, embeddings.HashingEmbedder)
    assert embeddings.get_embedder() is first


def test_the_local_model_backend_returns_unit_length_vectors(monkeypatch, tmp_path, fresh_embedder):
    created = {}

    class FakeTextEmbedding:
        def __init__(self, model_name, cache_dir):
            created.update(model=model_name, cache_dir=cache_dir)

        def embed(self, texts):
            return (np.array([3.0, 4.0]) * (n + 1) for n, _ in enumerate(texts))

    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=FakeTextEmbedding))
    monkeypatch.setattr(get_settings(), "embedding_backend", "local")
    monkeypatch.setattr(get_settings(), "embedding_model", "BAAI/bge-small-en-v1.5")
    monkeypatch.setattr(get_settings(), "embedding_cache_dir", tmp_path / "models")
    embedder = embeddings.get_embedder()
    assert isinstance(embedder, embeddings.LocalModelEmbedder)
    assert embedder.name == "BAAI/bge-small-en-v1.5"
    assert created == {"model": "BAAI/bge-small-en-v1.5", "cache_dir": str(tmp_path / "models")}
    vectors = embedder.embed(["black bag", "blue bottle"])
    assert vectors == [pytest.approx([0.6, 0.8]), pytest.approx([0.6, 0.8])]


def test_if_the_local_model_cannot_load_matching_falls_back_to_hashing(monkeypatch, caplog, fresh_embedder):
    class BrokenTextEmbedding:
        def __init__(self, model_name, cache_dir):
            raise OSError("no internet to download the model")

    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=BrokenTextEmbedding))
    monkeypatch.setattr(get_settings(), "embedding_backend", "local")
    with caplog.at_level(logging.ERROR):
        embedder = embeddings.get_embedder()
    assert isinstance(embedder, embeddings.HashingEmbedder)
    assert "using the hashing fallback" in caplog.text
    # And it really works: similar texts get similar vectors.
    bag, bag2, bottle = embedder.embed(["black leather bag", "black bag", "blue water bottle"])
    assert np.dot(bag, bag2) > np.dot(bag, bottle)


# ---------------- the OCR engine for ID cards ----------------


@pytest.fixture
def fresh_ocr():
    idcard.reset_ocr()
    yield
    idcard.reset_ocr()


def test_ocr_is_off_when_switched_off_in_the_settings(monkeypatch, fresh_ocr):
    monkeypatch.setattr(get_settings(), "id_card_ocr", False)
    assert idcard.get_ocr() is None


def test_the_rapidocr_engine_returns_the_text_of_each_line(monkeypatch, tmp_path, fresh_ocr):
    seen = []

    class FakeRapidOCR:
        def __call__(self, path):
            seen.append(path)
            box = [[0, 0], [1, 0], [1, 1], [0, 1]]
            return [(box, "INDIAN INSTITUTE OF TECHNOLOGY KANPUR", 0.98), (box, "Roll No: 220345", 0.91)], 0.2

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", types.SimpleNamespace(RapidOCR=FakeRapidOCR))
    monkeypatch.setattr(get_settings(), "id_card_ocr", True)
    engine = idcard.get_ocr()
    assert isinstance(engine, idcard.RapidOcrEngine) and idcard.get_ocr() is engine
    card = tmp_path / "card.jpg"
    assert engine.read(card) == ["INDIAN INSTITUTE OF TECHNOLOGY KANPUR", "Roll No: 220345"]
    assert seen == [str(card)]


def test_a_blank_photo_gives_no_text(monkeypatch, fresh_ocr):
    class NothingFound:
        def __call__(self, path):
            return None, 0.1

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", types.SimpleNamespace(RapidOCR=NothingFound))
    monkeypatch.setattr(get_settings(), "id_card_ocr", True)
    assert idcard.get_ocr().read("blank.jpg") == []


def test_if_ocr_cannot_load_it_is_not_retried_on_every_photo(monkeypatch, caplog, fresh_ocr):
    attempts = []

    class Broken:
        def __init__(self):
            attempts.append(1)
            raise ImportError("onnxruntime is not installed")

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", types.SimpleNamespace(RapidOCR=Broken))
    monkeypatch.setattr(get_settings(), "id_card_ocr", True)
    with caplog.at_level(logging.WARNING):
        assert idcard.get_ocr() is None
        assert idcard.get_ocr() is None
    assert len(attempts) == 1
    assert "ID card OCR unavailable" in caplog.text


@pytest.fixture
def found_card(db, make_user, monkeypatch):
    """A found ID card with one photo, not yet handed in."""
    monkeypatch.setattr(get_settings(), "id_card_categories", "id-cards-documents")
    finder = make_user("card.finder@iitk.ac.in", full_name="Finder", roll_number="F0001")
    cards = db.scalar(select(Category).where(Category.slug == "id-cards-documents"))
    office = db.scalar(select(Office).where(Office.is_active).order_by(Office.id))
    item = Item(kind=ItemKind.FOUND, status=ItemStatus.AWAITING_DROPOFF, title="Student ID card",
                description="Found near the gate", category_id=cards.id, location_text="Main gate",
                happened_on=today(), reporter_id=finder.id, dropoff_office_id=office.id)
    db.add(item)
    db.flush()
    data = jpeg_bytes()
    db.add(ItemPhoto(item_id=item.id, stored_name=storage.save(data, "jpg"), content_type="image/jpeg",
                     size_bytes=len(data), uploaded_by_id=finder.id))
    db.commit()
    db.refresh(item)
    return item


def test_only_found_id_cards_are_scanned(db, photographed_item, fresh_ocr):
    idcard.set_ocr(object())  # would fail if it were used
    assert idcard.scan(db, photographed_item) == {
        "scanned": False, "why": "only found ID cards / documents with photos are read"}


def test_a_photo_that_cannot_be_read_does_not_stop_the_scan(db, found_card, caplog, fresh_ocr):
    class FailingEngine:
        def read(self, path):
            raise OSError("truncated JPEG")

    idcard.set_ocr(FailingEngine())
    with caplog.at_level(logging.WARNING):
        result = idcard.scan(db, found_card)
    assert result["scanned"] is True and result["identifiers_found"] == 0 and result["new_alerts"] == 0
    assert found_card.ocr_photos_key == idcard.photos_key(found_card)  # not read again
    assert "OCR failed for photo" in caplog.text


def test_without_ocr_the_scan_says_why(db, found_card, fresh_ocr):
    idcard.set_ocr(None)
    assert idcard.scan(db, found_card) == {"scanned": False, "why": "text reading is not available on this server"}
    assert found_card.ocr_photos_key is None  # will be tried again when OCR is available


# ---------------- match reasons and candidates (services/ai/semantic.py) ----------------


def pair(colour_lost="Black", colour_found="black", same_category=True):
    bags, keys = Category(id=1, name="Bags"), Category(id=2, name="Keys")
    lost = Item(kind=ItemKind.LOST, category_id=1, category=bags, colour=colour_lost, happened_on=date(2026, 11, 1))
    found_category = bags if same_category else keys
    found = Item(kind=ItemKind.FOUND, category_id=found_category.id, category=found_category, colour=colour_found,
                 happened_on=date(2026, 11, 3))
    return lost, found


@pytest.mark.parametrize("similarity, opening", [
    (0.80, "Very similar description"), (0.60, "Very similar description"),
    (0.45, "Similar description"), (0.35, "Similar description"), (0.20, "Some details in common"),
])
def test_the_match_reason_says_how_close_the_descriptions_are(similarity, opening):
    lost, found = pair()
    reason = semantic._reason(lost, found, similarity)
    assert reason.startswith(opening + ", ")


def test_the_match_reason_lists_what_the_two_reports_share():
    lost, found = pair(colour_lost=" Black ", colour_found="black")
    assert semantic._reason(lost, found, 0.7) == (
        "Very similar description, same category (Bags), same colour (black), found 2 days after it was lost")


def test_the_match_reason_leaves_out_what_differs():
    lost, found = pair(colour_lost="Blue", colour_found="Black", same_category=False)
    assert semantic._reason(lost, found, 0.2) == "Some details in common, found 2 days after it was lost"


def test_items_no_longer_active_or_not_analysed_get_no_candidates(db, photographed_item):
    assert photographed_item.embedding is None
    assert semantic.candidates_for(db, photographed_item) == []  # not analysed yet
    photographed_item.embedding = [1.0] + [0.0] * 383
    photographed_item.status = ItemStatus.RECOVERED  # the owner got it back
    assert semantic.candidates_for(db, photographed_item) == []


def test_a_job_for_a_deleted_item_is_simply_skipped(db):
    job = AiJob(kind=semantic.JOB_KIND, item_id=987654321)  # no such item any more
    semantic.analyse_item(db, job)  # no error: the report was deleted after it was queued
