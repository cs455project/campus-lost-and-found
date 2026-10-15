"""Phase D: find the owner of a found ID card and email them.

  1. OCR: the text on the found item's photos is read ON OUR SERVER with a
     small offline model (RapidOCR, ONNX, ~14 MB, no PyTorch). The photo is
     never sent to any AI service.
  2. Detection: plain code (regular expressions) looks for
       - a roll number next to a "Roll" label (e.g. "Roll No: 220345"), and
       - an email address on an allowed domain (OWNER_EMAIL_DOMAINS).
     Names are ignored: they are not unique.
  3. Who gets the email: the registered student with that roll number or
     email, otherwise {roll}@iitk.ac.in (ROLL_EMAIL_PATTERN). Never the
     finder. Stored in owner_alerts, one row per person per item.
  4. When: only once a guard holds the item at a security office (the item
     is re-analysed when it's received), and only once per person.
  5. If the owner already has an open lost report for it, the two are linked
     as a certain match (score 1.0).

When does a scan happen?
  Only for FOUND items in an ID card category (ID_CARD_CATEGORIES, default
  "ID Cards & Documents") that have photos. Lost reports and every other
  category are never read. Each set of photos is read once.

Why OCR and not the AI photo description (Phase C)? Photos in this category
are never sent to Gemini (free-tier data may be read by Google), and any AI
description that shows personal details is thrown away. So the roll number
or email only ever comes from this local OCR, and no LLM ever sees it.
"""

import logging
import re
from dataclasses import dataclass
from itertools import pairwise

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.clock import now
from app.core.config import get_settings
from app.models import (
    Item,
    ItemKind,
    ItemStatus,
    MatchNotification,
    MatchSuggestion,
    OwnerAlert,
    User,
)
from app.services import email as email_service
from app.services import storage

log = logging.getLogger(__name__)

MAX_PHOTOS = 3
AT_OFFICE = (ItemStatus.IN_CUSTODY, ItemStatus.CLAIM_PENDING)
LINK_METHOD = "idcard"  # match_suggestions.method for "the card names this report's owner"


# ---------------- 1. OCR ----------------


class RapidOcrEngine:
    """RapidOCR with its bundled ONNX models (downloaded with pip, offline)."""

    name = "rapidocr"

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR

        self._ocr = RapidOCR()

    def read(self, path) -> list[str]:
        result, _elapsed = self._ocr(str(path))
        return [text for _box, text, _confidence in (result or [])]


_engine = None
_override = False
_load_failed = False


def get_ocr():
    """The OCR engine, or None when switched off or not installed."""
    global _engine, _load_failed
    if _override:
        return _engine
    if not get_settings().id_card_ocr or _load_failed:
        return None
    if _engine is None:
        try:
            _engine = RapidOcrEngine()
        except Exception as error:  # not installed, or the model failed to load
            log.warning("ID card OCR unavailable (%s). Run: pip install -r requirements.txt", error)
            _load_failed = True
            return None
    return _engine


def set_ocr(engine) -> None:
    """Tests: plug in a fake engine (or None). Call reset_ocr() afterwards."""
    global _engine, _override
    _engine, _override = engine, True


def reset_ocr() -> None:
    global _engine, _override, _load_failed
    _engine, _override, _load_failed = None, False, False


# ---------------- 2. Detection ----------------


@dataclass(frozen=True)
class Identifier:
    kind: str  # "ROLL" or "EMAIL"
    value: str

    @property
    def hint(self) -> str:
        """Masked, safe to show to guards and to put in an email."""
        if self.kind == "ROLL":
            return f"roll no. ending {self.value[-2:]}"
        local, _, domain = self.value.partition("@")
        return f"{local[:2]}•••@{domain}"


# "Roll No: 220345", "RollNo.220345", "ROLL NUMBER - 21104001", "Roll 220345".
# OCR often reads 0 as O and 1 as l or I, so those are accepted and fixed.
_ROLL = re.compile(r"roll\s*(?:no|number|num|n0)?\s*[.:\-#]*\s*([0-9OoIl|]{5,10})(?![0-9A-Za-z])", re.IGNORECASE)
_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", re.IGNORECASE)
_OCR_DIGITS = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"})


def _allowed_domains() -> list[str]:
    return [d.strip().lower() for d in get_settings().owner_email_domains.split(",") if d.strip()]


def _domain_allowed(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1]
    return any(domain == d or domain.endswith("." + d) for d in _allowed_domains())


def find_identifiers(lines: list[str]) -> list[Identifier]:
    """Roll numbers (labelled) and allowed-domain emails in OCR text."""
    found: list[Identifier] = []

    def add(kind: str, value: str) -> None:
        ident = Identifier(kind, value)
        if ident not in found:
            found.append(ident)

    lines = [" ".join(str(line).split()) for line in lines if str(line).strip()]
    # A label and its number can end up on two OCR lines ("Roll No:" / "220345").
    pairs = lines + [f"{a} {b}" for a, b in pairwise(lines)]
    for text in pairs:
        for match in _ROLL.finditer(text):
            digits = match.group(1).translate(_OCR_DIGITS)
            if digits.isdigit() and 6 <= len(digits) <= 9:
                add("ROLL", digits)
    for text in lines:
        squeezed = re.sub(r"\s*@\s*", "@", text)  # "name @ iitk.ac.in"
        for match in _EMAIL.finditer(squeezed):
            email = match.group(0).lower().rstrip(".")
            # OCR can glue the label on: "Emailrahulk22@iitk.ac.in", "E-mail:..."
            email = re.sub(r"^(?:e-?mail|mail)(?:id)?[.:\-]*(?=[a-z0-9])", "", email)
            if _domain_allowed(email):
                add("EMAIL", email)
    return found


# ---------------- 3. Who gets the email ----------------


def _registered(db: Session, ident: Identifier) -> User | None:
    if ident.kind == "ROLL":
        return db.scalar(select(User).where(func.lower(User.roll_number) == ident.value.lower()))
    return db.scalar(select(User).where(User.email == ident.value))


def recipients(db: Session, identifiers: list[Identifier]) -> list[tuple[Identifier, str, User | None]]:
    """Who to email, in this order (the first rule that gives anyone wins,
    so the same person never gets two emails for one card):
      1. a registered user with that roll number or email -> their account email
      2. an email address printed on the card            -> that address
      3. a roll number                                   -> {roll}@iitk.ac.in
    """
    chosen: list[tuple[Identifier, str, User | None]] = []
    for ident in identifiers:
        user = _registered(db, ident)
        if user is not None and user.email not in {target for _, target, _ in chosen}:
            chosen.append((ident, user.email, user))
    if chosen:
        return chosen
    emails = [(ident, ident.value, None) for ident in identifiers if ident.kind == "EMAIL"]
    if emails:
        return emails
    pattern = get_settings().roll_email_pattern
    if not pattern:
        return []
    return [(ident, pattern.format(roll=ident.value).lower(), None)
            for ident in identifiers if ident.kind == "ROLL"]


# ---------------- The scan ----------------


def _photos(item: Item):
    return sorted(item.photos, key=lambda photo: photo.id)[:MAX_PHOTOS]


def photos_key(item: Item) -> str:
    return ",".join(str(photo.id) for photo in _photos(item))


def should_scan(item: Item) -> bool:
    """Only found items in an ID card / documents category, with photos."""
    return item.kind == ItemKind.FOUND and item.has_private_photos and bool(item.photos)


def scan(db: Session, item: Item) -> dict:
    """Read the photos and store an owner alert per person to email.
    Returns counts only (for logs; never the identifiers themselves)."""
    if not should_scan(item):
        return {"scanned": False, "why": "only found ID cards / documents with photos are read"}
    key = photos_key(item)
    if item.ocr_photos_key == key:
        return {"scanned": True, "already_scanned": True, "owner_alerts": _alert_count(db, item)}
    engine = get_ocr()
    if engine is None:
        return {"scanned": False, "why": "text reading is not available on this server"}

    lines: list[str] = []
    for photo in _photos(item):
        try:
            lines += engine.read(storage.path_for(photo.stored_name))
        except Exception as error:  # a broken image must not stop the analysis
            log.warning("OCR failed for photo %s of item %s: %s", photo.id, item.id, error)
    identifiers = find_identifiers(lines)
    finder_email = item.reporter.email if item.reporter else None
    added = 0
    for ident, target, user in recipients(db, identifiers):
        if target == finder_email:
            continue  # never email the finder about what they found
        inserted = db.execute(
            insert(OwnerAlert).values(item_id=item.id, id_type=ident.kind, id_hint=ident.hint,
                                      target_email=target, user_id=user.id if user else None)
            .on_conflict_do_nothing(index_elements=["item_id", "target_email"])
            .returning(OwnerAlert.id)
        ).scalar()
        added += inserted is not None
    item.ocr_photos_key = key
    db.flush()
    return {"scanned": True, "photos": len(_photos(item)), "identifiers_found": len(identifiers),
            "owner_alerts": _alert_count(db, item), "new_alerts": added}


def _alert_count(db: Session, item: Item) -> int:
    return db.scalar(select(func.count()).select_from(OwnerAlert).where(OwnerAlert.item_id == item.id)) or 0


def alerts_for(db: Session, item: Item) -> list[OwnerAlert]:
    return list(db.scalars(select(OwnerAlert).where(OwnerAlert.item_id == item.id).order_by(OwnerAlert.id)))


# ---------------- 5. Link to the owner's own lost report ----------------


def _link(db: Session, lost: Item, found: Item, ident_kind: str) -> None:
    what = "roll number" if ident_kind == "ROLL" else "email address"
    for item_id, candidate_id, reason in (
        (lost.id, found.id, f"The {what} on this item belongs to you"),
        (found.id, lost.id, f"The {what} on the card belongs to this report's owner"),
    ):
        db.execute(
            insert(MatchSuggestion).values(item_id=item_id, candidate_id=candidate_id, score=1.0,
                                           reason=reason, method=LINK_METHOD)
            .on_conflict_do_update(index_elements=["item_id", "candidate_id"],
                                   set_={"score": 1.0, "reason": reason, "method": LINK_METHOD})
        )


def _open_lost_reports(db: Session, user_id: int) -> list[Item]:
    return list(db.scalars(select(Item).where(
        Item.reporter_id == user_id, Item.kind == ItemKind.LOST, Item.status == ItemStatus.OPEN)))


def relink(db: Session, item: Item) -> None:
    """Re-add the certain matches after matching replaced the item's list.
    Works from either side: a found card, or the owner's lost report."""
    if item.kind == ItemKind.FOUND:
        for alert in alerts_for(db, item):
            if alert.user_id:
                for lost in _open_lost_reports(db, alert.user_id):
                    if lost.category_id == item.category_id or lost.has_private_photos:
                        _link(db, lost, item, alert.id_type)
    elif item.status == ItemStatus.OPEN:
        rows = db.execute(select(OwnerAlert, Item).join(Item, Item.id == OwnerAlert.item_id).where(
            OwnerAlert.user_id == item.reporter_id,
            Item.status.in_([ItemStatus.AWAITING_DROPOFF, *AT_OFFICE])))
        for alert, found in rows:
            if found.category_id == item.category_id or item.has_private_photos:
                _link(db, item, found, alert.id_type)
    db.flush()


# ---------------- 4. Sending ----------------


def send_due_alerts(db: Session, item: Item, after_commit: list) -> int:
    """Queue the emails for pending alerts once the item is at an office.
    The emails go out after the caller commits."""
    if item.kind != ItemKind.FOUND or item.status not in AT_OFFICE:
        return 0
    queued = 0
    for alert in alerts_for(db, item):
        if alert.status != "pending":
            continue
        alert.status, alert.sent_at = "sent", now()
        if alert.user_id:
            # The owner hears about this item once: no second "may be yours" email.
            for lost in _open_lost_reports(db, alert.user_id):
                db.execute(insert(MatchNotification).values(lost_item_id=lost.id, found_item_id=item.id)
                           .on_conflict_do_nothing(index_elements=["lost_item_id", "found_item_id"]))
        mail = _email(item, alert)
        after_commit.append(lambda to=alert.target_email, m=mail: email_service.send_quietly(to, m))
        queued += 1
    db.flush()
    return queued


def _email(item: Item, alert: OwnerAlert) -> email_service.Email:
    office = item.current_office
    name = get_settings().portal_name
    shows = (f"your roll number ({alert.id_hint})" if alert.id_type == "ROLL"
             else f"your email address ({alert.id_hint})")
    details = [("Item", f"{item.title} ({item.ref})")]
    if office:
        details += [("Held at", office.name), ("Where", office.location_description),
                    ("Open", office.opening_hours)]
    return email_service.Email(
        kind="id_card", ref=item.ref,
        subject=f"Your ID card may have been found ({item.ref})",
        heading="An item with your details has been handed in",
        paragraphs=[f"An item handed in to IIT Kanpur security shows {shows}. "
                    "It is most likely your ID card or a document of yours."],
        details=details,
        steps=[f"Sign in to {name} with your IITK email.",
               f"Open {item.ref} and press \"This is mine\".",
               "A guard reviews your claim and tells you when to collect it.",
               "Collect it at the office with another photo ID."],
        button=(f"Open {item.ref}", email_service.item_link(item.id)),
        note="Not yours? Ignore this email. The details were read automatically from the photo "
             f"on the {name} server; the photo was not shared with anyone.",
    )
