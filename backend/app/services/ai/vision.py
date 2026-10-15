"""Phase C: the AI looks at an item's photos and describes the item.

Gemini (free tier) gets up to 3 photos, shrunk to at most 768 px, plus the
report's own text, and answers in JSON: what the object is, its colour,
brand, distinctive marks, and a short description. This becomes the
item's "AI description": shown on the item page and added to the text that
matching uses, so a photo with a visible "Wildcraft" logo helps matching
even if the finder only typed "bag".

Protecting privacy and the free quota:
  - Photos of ID cards and documents (AI_SKIP_PHOTO_CATEGORIES) are NEVER
    sent: Google may use free-tier data to improve its products.
  - If the model says a photo shows personal information (a face, a name,
    an ID number), its description is thrown away.
  - Each set of photos is described once (items.ai_photos_key): editing the
    text of a report doesn't call the AI again; adding or removing a photo does.
  - Every call is logged in agent_runs and counts towards AI_DAILY_CALL_LIMIT.
"""

import base64
import io
import json
import logging
import re

from PIL import Image
from sqlalchemy.orm import Session

from app.core.clock import now
from app.core.config import get_settings
from app.models import AgentRun, AiJob, Item
from app.services import storage
from app.services.ai import llm as llm_module

log = logging.getLogger(__name__)

MAX_PHOTOS = 3
MAX_SIDE = 768  # pixels; smaller images use fewer tokens of the free quota
MAX_DESCRIPTION = 600

PROMPT = """You help a university lost-and-found office. Look at the photo(s) of ONE item and
describe the item itself, factually, for matching lost and found reports.

The student's own report (may be incomplete or wrong; the photos win):
{report}

Answer with ONLY a JSON object, no other text:
{{
  "object": "what the item is, e.g. backpack, water bottle, earphones",
  "colour": "main colour(s)",
  "brand": "brand or model if clearly visible, else null",
  "marks": ["distinctive features: stickers, scratches, damage, keychains, text on it"],
  "description": "1-2 factual sentences combining the above",
  "contains_personal_info": false
}}
Set "contains_personal_info" to true if any photo shows a person's face, a name, a phone number,
an email address, an ID card, a document, or a screen with personal data. Never copy any such
personal text into your answer. Do not guess what is not visible."""


class VisionClient:
    """Gemini through its OpenAI-compatible API (images as base64 data URLs)."""

    def __init__(self, base_url: str, api_key: str, model: str):
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=60, max_retries=1)

    def describe(self, images: list[bytes], report: dict) -> dict:
        content = [{"type": "text", "text": PROMPT.format(report=json.dumps(report, ensure_ascii=False))}]
        for data in images:
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(data).decode()}})
        response = self._client.chat.completions.create(
            model=self.model, messages=[{"role": "user", "content": content}], temperature=0.1)
        return parse_json(response.choices[0].message.content or "")


def parse_json(text: str) -> dict:
    """The model's answer as a dict, tolerating ```json fences or extra words."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("The model did not answer with JSON")
    return json.loads(match.group(0))


_client: VisionClient | None = None
_override = False


def get_vision() -> VisionClient | None:
    """Gemini, if a key is set and photo descriptions are switched on."""
    global _client
    if _override:
        return _client
    settings = get_settings()
    if not (settings.ai_enabled and settings.ai_photo_descriptions and settings.gemini_api_key
            and settings.gemini_model):
        return None
    if _client is None:
        _client = VisionClient(settings.llm_fallback_base_url, settings.gemini_api_key, settings.gemini_model)
    return _client


def set_vision(client) -> None:
    """Tests: plug in a fake (or None). Call reset_vision() afterwards."""
    global _client, _override
    _client, _override = client, True


def reset_vision() -> None:
    global _client, _override
    _client, _override = None, False


def shrink(path) -> bytes:
    """The photo as a JPEG of at most MAX_SIDE pixels on its longest side."""
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=80)
        return out.getvalue()


def _skipped_category(item: Item) -> bool:
    slugs = {s.strip() for s in get_settings().ai_skip_photo_categories.split(",") if s.strip()}
    return bool(item.category and item.category.slug in slugs)


def describe_photos(db: Session, item: Item, job: AiJob | None) -> None:
    """Describe the item's photos if they changed since the last description.
    Never raises: if anything fails, the item simply has no AI description."""
    photos = sorted(item.photos, key=lambda photo: photo.id)[:MAX_PHOTOS]
    if not photos or _skipped_category(item):
        # No photos (any more), or a category we never send: no description.
        item.ai_description, item.ai_attributes, item.ai_photos_key = None, None, None
        return
    key = ",".join(str(photo.id) for photo in photos)
    if key == item.ai_photos_key:
        return  # these exact photos were already described
    client = get_vision()
    if client is None:
        return
    settings = get_settings()
    if llm_module.calls_today(db) >= settings.ai_daily_call_limit:
        log.info("Daily AI limit reached: no photo description for item %s", item.id)
        return

    from app.services.ai.agent import describe  # the report without personal data

    started, report = now(), describe(item)
    report.pop("id", None)
    try:
        images = [shrink(storage.path_for(photo.stored_name)) for photo in photos]
        answer = client.describe(images, report)
        if answer.get("contains_personal_info") is True:
            item.ai_description, item.ai_attributes = None, {"personal_info": True}
            result = "photo shows personal information: description discarded"
        else:
            marks = [str(m)[:80] for m in (answer.get("marks") or []) if m][:6]
            item.ai_attributes = {"object": answer.get("object"), "colour": answer.get("colour"),
                                  "brand": answer.get("brand"), "marks": marks}
            text = " ".join(str(answer.get("description") or "").split())
            item.ai_description = text[:MAX_DESCRIPTION] or None
            result = (item.ai_description or "")[:200]
        item.ai_photos_key = key
        outcome, error = "ok", None
    except Exception as exc:
        log.warning("Photo description failed for item %s: %s", item.id, exc)
        outcome, error, result = "error", f"{type(exc).__name__}: {exc}"[:500], None
    db.add(AgentRun(job_id=job.id if job else None, item_id=item.id, outcome=outcome,
                    provider="gemini", model=getattr(client, "model", None), llm_calls=1,
                    steps=[{"tool": "describe_photos", "args": {"photos": len(photos)}, "result": result}],
                    error=error, started_at=started, finished_at=now()))
