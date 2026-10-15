"""Command-line tools for the person running the server (run from backend/).

Make someone the admin:
    python -m app.cli make-admin someone@iitk.ac.in

Create sample items to try the Found/Lost pages and page numbers:
    python -m app.cli demo-items --count 30

Timed jobs (reminders, claim expiry, clean-up) are run by the worker:
    python -m app.worker

Queue every active item for AI matching (after first setting up the AI,
or after changing EMBEDDING_BACKEND / EMBEDDING_MODEL):
    python -m app.cli ai-reindex

Check that the AI keys in .env work (one tiny test call per provider):
    python -m app.cli ai-check

Run the AI agent on one report and print every step it took (for testing):
    python -m app.cli ai-run 42

Read the text on a photo of an ID card, as Phase D does (offline, nothing
is sent anywhere), and show the roll numbers / emails it would use:
    python -m app.cli ocr-check card.jpg
    python -m app.cli ocr-check 549        (the photos of item 549)

Explain what the AI did with one item, and why an email was / wasn't sent:
    python -m app.cli item-check FND-0735

Show the last emails the portal sent (who, what, sent or failed):
    python -m app.cli email-log
    python -m app.cli email-log --ref FND-0735
    python -m app.cli email-log --to someone@iitk.ac.in

Send sample emails (sign-in code, ID card found, match found) to check the
email settings and see the design:
    python -m app.cli email-test you@gmail.com

There's no API endpoint for make-admin on purpose: nobody can become admin
through the website. Only someone with access to the server can do it.
"""

import argparse
import random
from datetime import timedelta

from sqlalchemy import select

from app.core.clock import now, today
from app.core.database import SessionLocal
from app.core.errors import AppError
from app.models import (
    Category,
    CustodyEvent,
    CustodyEventType,
    Item,
    ItemKind,
    ItemStatus,
    Office,
    User,
    UserRole,
)
from app.services.auth import check_staff_email, normalize_email


def make_admin(raw_email: str) -> None:
    email = normalize_email(raw_email)
    check_staff_email(email)  # @iitk.ac.in or @gmail.com
    with SessionLocal() as db, db.begin():
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email)
            db.add(user)
        user.role = UserRole.ADMIN
    print(f"{email} is now the admin. They can sign in with that email.")


DEMO_ITEMS = [
    ("Black backpack", "bags", "Black", "Wildcraft", "Lecture Hall Complex, L7", "Laptop sleeve inside, keychain on the zip."),
    ("Blue water bottle", "water-bottles", "Blue", "Milton", "Sports complex, badminton court", "Steel bottle with a dent near the cap."),
    ("Scientific calculator", "electronics", "Grey", "Casio", "Library, second floor", "fx-991ES with initials scratched on the back."),
    ("Bicycle keys on a ring", "keys", "Silver", None, "Hall 5 cycle stand", "Two small keys and a bottle-opener ring."),
    ("Student ID card", "id-cards-documents", None, None, "Near the main gate", "IITK ID card in a clear plastic holder."),
    ("Wireless earphones", "electronics", "White", "boAt", "Computer Centre", "Earbuds in a white charging case."),
    ("Brown leather wallet", "wallets-purses", "Brown", None, "Shopping complex", "Bi-fold wallet with a few cards."),
    ("Grey hoodie", "clothing", "Grey", "H&M", "Open Air Theatre", "Plain hoodie, size M."),
    ("Reading glasses", "spectacles", "Black", None, "Faculty Building corridor", "Black frame in a blue hard case."),
    ("Cricket bat", "sports-equipment", None, "SG", "Main ground", "Willow bat with a black grip."),
    ("Engineering drawing kit", "books-stationery", None, None, "Lecture Hall Complex, L16", "Mini drafter and a set of pencils."),
    ("Ladies' watch", "jewellery-watches", "Gold", "Titan", "Hall 4 common room", "Thin gold strap."),
]


def demo_items(count: int) -> None:
    """Create sample items: found items already at an office (so they show on
    the Found page) and open lost reports. Reported by a demo account."""
    with SessionLocal() as db, db.begin():
        reporter = db.scalar(select(User).where(User.email == "demo.reporter@iitk.ac.in"))
        if reporter is None:
            reporter = User(email="demo.reporter@iitk.ac.in", full_name="Demo Reporter", roll_number="DEMO0001")
            db.add(reporter)
            db.flush()
        categories = {c.slug: c.id for c in db.scalars(select(Category))}
        offices = list(db.scalars(select(Office).where(Office.is_active)))
        if not categories or not offices:
            raise SystemExit("Error: run `python -m app.seed` first.")

        for n in range(count):
            title, slug, colour, brand, place, description = DEMO_ITEMS[n % len(DEMO_ITEMS)]
            kind = ItemKind.FOUND if n % 3 else ItemKind.LOST
            office = random.choice(offices)
            item = Item(
                kind=kind,
                title=title,
                description=description,
                category_id=categories[slug],
                colour=colour,
                brand=brand,
                location_text=place,
                happened_on=today() - timedelta(days=random.randint(0, 20)),
                reporter_id=reporter.id,
            )
            if kind == ItemKind.FOUND:
                item.status = ItemStatus.IN_CUSTODY
                item.dropoff_office_id = office.id
                item.current_office_id = office.id
                item.received_at = now()
                item.custody_events.append(
                    CustodyEvent(event_type=CustodyEventType.REGISTERED, to_office=office, note="Demo item")
                )
            else:
                item.status = ItemStatus.OPEN
            db.add(item)
    print(f"Created {count} demo items (about two thirds found, one third lost).")
    reindex()


def reindex() -> None:
    """Queue every active report for AI matching (services/ai). The API's
    background runner (or the worker) then works through the queue."""
    from app.services.ai import queue
    from app.services.ai.semantic import ACTIVE, JOB_KIND

    with SessionLocal() as db:
        count = 0
        for kind, statuses in ACTIVE.items():
            for item in db.scalars(select(Item).where(Item.kind == kind, Item.status.in_(statuses))):
                queue.enqueue(db, JOB_KIND, item.id)
                item.ai_status = "pending"
                count += 1
        db.commit()
    print(f"Queued {count} items for AI matching. With the API running, they are processed "
          "in the background within a minute or two (or run: python -m app.worker --once).")


def ai_check() -> None:
    """Make one tiny tool-calling request to each configured LLM provider."""
    from app.core.config import get_settings
    from app.services.ai.llm import LLMClient, configured_providers

    settings = get_settings()
    if not settings.ai_enabled:
        print("AI_ENABLED=false in .env: the agent is switched off (rule-based matching only).")
        return
    providers = configured_providers()
    if not providers:
        print("No AI keys found. Add LLM_API_KEY (Groq) and/or GEMINI_API_KEY to backend/.env.")
        return
    ping = [{"type": "function", "function": {
        "name": "ping", "description": "Reply to the check.",
        "parameters": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}}}]
    messages = [{"role": "user", "content": "This is a connection test. Call the ping tool with ok=true."}]
    for provider in providers:
        try:
            message, _, model = LLMClient([provider]).chat(messages, ping)
            called = [c["function"]["name"] for c in message.get("tool_calls", [])]
            status = "OK, tool calling works" if "ping" in called else "OK, but it did not call the tool"
            print(f"{provider.name:7} {model:35} {status}")
        except Exception as error:
            print(f"{provider.name:7} {provider.model:35} FAILED: {str(error)[:200]}")


def ai_run(item_id: int) -> None:
    """Run the matching agent on one item now (whatever AGENT_MATCHING says)
    and print its decisions: filters, results seen, scores, reasons."""
    import json

    from app.core.config import get_settings
    from app.models import AgentRun, MatchSuggestion
    from app.services.ai.semantic import item_text

    settings = get_settings()
    settings.agent_matching = "always"  # this command is for testing the agent
    from app.services.ai import agent
    from app.services.ai.embeddings import get_embedder

    with SessionLocal() as db:
        item = db.get(Item, item_id)
        if item is None:
            raise SystemExit(f"Error: no item {item_id}.")
        print(f"{item.ref} ({item.kind.value}): {item.title}")
        embedder = get_embedder()
        item.embedding = embedder.embed([item_text(item)])[0]
        item.embedding_model = embedder.name
        db.flush()
        from app.services.ai import idcard

        emails: list = []
        if idcard.should_scan(item):
            item.ocr_photos_key = None  # testing command: always read the photos again
            print("Phase D (ID card OCR):", idcard.scan(db, item))
        idcard.send_due_alerts(db, item, emails)
        emails += agent.match_with_agent(db, item, None)
        idcard.relink(db, item)
        db.commit()
        for send in emails:
            send()
        run = db.scalars(select(AgentRun).where(AgentRun.item_id == item_id)
                         .order_by(AgentRun.id.desc()).limit(1)).first()
        if run is None:
            print("The agent did not run (no AI keys, or nothing to compare with). Rule-based matches were saved.")
        else:
            print(f"\nOutcome: {run.outcome}   provider: {run.provider}   model: {run.model}   "
                  f"LLM calls: {run.llm_calls}")
            if run.error:
                print(f"Error: {run.error}")
            for number, step in enumerate(run.steps, start=1):
                print(f"\n{number}. {step['tool']}  {json.dumps(step['args'], ensure_ascii=False)}")
                print(f"   -> {step['result']}")
            if run.summary:
                print(f"\nSummary: {run.summary}")
        alerts = idcard.alerts_for(db, item)
        if alerts:
            print("\nOwner alerts (Phase D, ID card):")
            for alert in alerts:
                who = "registered user" if alert.user_id else "not registered"
                print(f"  {alert.id_type:5} {alert.id_hint:28} -> {alert.target_email} ({who}): {alert.status}")
        print("\nSaved matches:")
        for row in db.scalars(select(MatchSuggestion).where(MatchSuggestion.item_id == item_id)
                              .order_by(MatchSuggestion.score.desc())):
            other = db.get(Item, row.candidate_id)
            print(f"  {row.score:.2f} [{row.method}] {other.ref} {other.title}: {row.reason}")


def ocr_check(target: str) -> None:
    """Phase D on an image file, or on the photos of an item (by number):
    the text read, and who would be emailed."""
    from pathlib import Path

    from app.services import storage
    from app.services.ai import idcard

    if target.isdigit() or target.upper().startswith(("FND-", "LST-")):
        with SessionLocal() as db:
            item = db.get(Item, parse_item(target))
            if item is None:
                raise SystemExit(f"Error: no item {target}.")
            paths = [storage.path_for(p.stored_name) for p in sorted(item.photos, key=lambda p: p.id)]
            print(f"{item.ref} ({item.kind.value}, {item.category.name}): {item.title}, {len(paths)} photo(s)")
            if not idcard.should_scan(item):
                print("Note: Phase D does not read this item (only FOUND items in the ID card category).")
    else:
        paths = [Path(target)]
    for path in paths:
        if not Path(path).is_file():
            raise SystemExit(f"Error: no file {path}")
    engine = idcard.get_ocr()
    if engine is None:
        raise SystemExit("OCR is not available: run  pip install -r requirements.txt  (and ID_CARD_OCR=true).")
    lines = []
    for path in paths:
        lines += engine.read(path)
    print("Text read from the photo:" if lines else "No text could be read from the photo (blurry, dark or too small?).")
    for line in lines:
        print(f"  {line}")
    found = idcard.find_identifiers(lines)
    print("\nOwner identifiers found:" if found else "\nNo roll number (with a 'Roll' label) or allowed email found.")
    for ident in found:
        print(f"  {ident.kind:5} {ident.value}")
    with SessionLocal() as db:
        chosen = idcard.recipients(db, found)
    if found:
        print("\nWould email:" if chosen else "\nWould email nobody (ROLL_EMAIL_PATTERN is empty).")
    for ident, target, user in chosen:
        why = "registered user" if user else ("email on the card" if ident.kind == "EMAIL" else "roll number")
        print(f"  {target}   ({why})")


def email_test(to: str) -> None:
    """Send one of each main email, with made-up details."""
    from app.core.config import get_settings
    from app.services import email as email_service

    settings = get_settings()
    E = email_service.Email
    samples = [
        ("sign-in code", lambda: email_service.send_login_code(to, "482913", settings.login_code_minutes)),
        ("ID card found", lambda: email_service.send(to, E(
            subject="Your ID card may have been found (FND-0000) [TEST]",
            heading="An item with your details has been handed in",
            paragraphs=["An item handed in to IIT Kanpur security shows your roll number "
                        "(roll no. ending 15). It is most likely your ID card or a document of yours."],
            details=[("Item", "ID card (FND-0000)"), ("Held at", "Main Gate Security Office"),
                     ("Where", "Next to the main gate"), ("Open", "24 hours")],
            steps=[f"Sign in to {settings.portal_name} with your IITK email.",
                   "Open FND-0000 and press \"This is mine\".",
                   "A guard reviews your claim and tells you when to collect it.",
                   "Collect it at the office with another photo ID."],
            button=("Open FND-0000", email_service.item_link(0)),
            note="This is a test email: no real item exists."))),
    ]
    print(f"EMAIL_BACKEND={settings.email_backend}" + (f", sending as {settings.smtp_user}" if
                                                       settings.email_backend == "smtp" else
                                                       " (nothing is sent; set EMAIL_BACKEND=smtp to really send)"))
    for name, send in samples:
        try:
            send()
            print(f"  {name}: OK")
        except email_service.EmailNotSent as error:
            raise SystemExit(f"  {name}: NOT SENT. {error}\n"
                             "  Check SMTP_USER and SMTP_PASSWORD (a Gmail App Password) in backend/.env.")


def parse_item(value: str) -> int:
    """Accept 735, 0735, FND-0735 or LST-0012."""
    digits = value.strip().upper().removeprefix("FND-").removeprefix("LST-").lstrip("#")
    if not digits.isdigit():
        raise SystemExit(f"Error: '{value}' is not an item number (e.g. 735 or FND-0735).")
    return int(digits)


def email_log(last: int, ref: str | None, to: str | None) -> None:
    from app.models import EmailLog

    query = select(EmailLog).order_by(EmailLog.id.desc()).limit(last)
    if ref:
        query = query.where(EmailLog.ref == f"FND-{parse_item(ref):04d}")
    if to:
        query = query.where(EmailLog.to_email == to.strip().lower())
    with SessionLocal() as db:
        rows = list(db.scalars(query))
    if not rows:
        print("No emails logged" + (" for that filter." if ref or to else " yet."))
        return
    for row in reversed(rows):
        when = row.created_at.astimezone().strftime("%d %b %H:%M:%S")
        print(f"{when}  {row.status.upper():7} {row.kind:10} {row.to_email:32} {row.subject}")
        if row.error:
            print(f"{'':27}error: {row.error}")


def item_check(value: str) -> None:
    """Everything the AI did with one item, and the reason for any email
    that was or wasn't sent."""
    from app.core.config import get_settings
    from app.models import EmailLog, ItemStatus
    from app.services.ai import idcard

    settings = get_settings()
    item_id = parse_item(value)
    with SessionLocal() as db:
        item = db.get(Item, item_id)
        if item is None:
            raise SystemExit(f"Error: no item {value}.")
        print(f"{item.ref}: {item.title}")
        print(f"  kind / status : {item.kind.value} / {item.status.value}"
              + (f" at {item.current_office.name}" if item.current_office else ""))
        print(f"  category      : {item.category.name}" + ("  (ID card category: read by OCR)"
                                                         if item.has_private_photos else ""))
        print(f"  photos        : {len(item.photos)}")
        print(f"  AI analysis   : {item.ai_status or 'never run'}"
              + (f" ({item.ai_updated_at.astimezone():%d %b %H:%M})" if item.ai_updated_at else ""))
        print(f"  email backend : {settings.email_backend}")

        alerts = idcard.alerts_for(db, item)
        logs = list(db.scalars(select(EmailLog).where(EmailLog.ref == item.ref).order_by(EmailLog.id)))
        if idcard.should_scan(item):
            print(f"  OCR           : {'read' if item.ocr_photos_key else 'NOT read yet'}")
        for alert in alerts:
            print(f"  owner alert   : {alert.id_hint} -> {alert.target_email}: {alert.status}"
                  + (f" ({alert.sent_at.astimezone():%d %b %H:%M})" if alert.sent_at else ""))
        for row in logs:
            print(f"  email log     : {row.status.upper()} to {row.to_email}, {row.created_at.astimezone():%d %b %H:%M}"
                  + (f" ({row.error})" if row.error else ""))

        # The verdict, in the order the checks happen.
        held = item.status in (ItemStatus.IN_CUSTODY, ItemStatus.CLAIM_PENDING)
        if item.kind != ItemKind.FOUND:
            why = "This is a LOST report: owner emails are only for FOUND items."
        elif not item.has_private_photos:
            why = "Not in the ID card category, so its photos are not read for an owner (match emails only)."
        elif not item.photos:
            why = "No photo: add a photo of the card so it can be read."
        elif item.ai_status == "pending":
            why = "The AI is still working on it: wait a few seconds (is the API running? AI_WORKER_IN_API=true?)."
        elif item.ai_status == "failed":
            why = "The AI analysis failed: run  python -m app.cli ai-run " + item.ref + "  to see the error."
        elif not item.ocr_photos_key:
            why = "The photo was not read: OCR may be missing (pip install -r requirements.txt) or ID_CARD_OCR=false."
        elif not alerts:
            why = ("The photo was read but no roll number (next to a 'Roll' label) or IITK email was found. "
                   "See what was read:  python -m app.cli ocr-check " + item.ref)
        elif not held:
            why = ("Owner found, but the email waits until a guard RECEIVES the item at an office "
                   f"(status is {item.status.value}).")
        elif any(a.status == "sent" for a in alerts) and not logs:
            why = ("Marked as sent before email logging existed: check the uvicorn terminal from that time, "
                   "or the owner's inbox / spam.")
        elif logs and logs[-1].status == "failed":
            why = "Sending FAILED: see the error above (App Password? EMAIL_BACKEND?)."
        elif logs and logs[-1].status == "printed":
            why = "EMAIL_BACKEND=console at the time: the email was only printed in the terminal, not sent."
        else:
            why = "The email was sent. If it hasn't arrived, check Spam / Quarantine."
        print(f"\n=> {why}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    admin_cmd = commands.add_parser("make-admin", help="give a user the admin role")
    admin_cmd.add_argument("email")

    demo_cmd = commands.add_parser("demo-items", help="create sample items")
    demo_cmd.add_argument("--count", type=int, default=30)

    commands.add_parser("ai-reindex", help="queue every active item for AI matching")
    commands.add_parser("ai-check", help="test the AI provider keys in .env")
    run_cmd = commands.add_parser("ai-run", help="run the AI agent on one item and print its steps")
    run_cmd.add_argument("item_id", help="e.g. 735 or FND-0735")
    log_cmd = commands.add_parser("email-log", help="show the last emails sent (who, what, sent or failed)")
    log_cmd.add_argument("--last", type=int, default=20)
    log_cmd.add_argument("--ref", help="only emails about this item, e.g. FND-0735")
    log_cmd.add_argument("--to", help="only emails to this address")
    check_cmd = commands.add_parser("item-check", help="what the AI did with an item, and why an email was sent or not")
    check_cmd.add_argument("item", help="e.g. 735 or FND-0735")
    mail_cmd = commands.add_parser("email-test", help="send sample emails to check the email settings")
    mail_cmd.add_argument("to")
    ocr_cmd = commands.add_parser("ocr-check", help="read an ID card photo and show the owner it finds")
    ocr_cmd.add_argument("path", help="an image file, or an item number")

    args = parser.parse_args()
    try:
        if args.command == "make-admin":
            make_admin(args.email)
        elif args.command == "demo-items":
            demo_items(args.count)
        elif args.command == "ai-reindex":
            reindex()
        elif args.command == "ai-check":
            ai_check()
        elif args.command == "ai-run":
            ai_run(parse_item(args.item_id))
        elif args.command == "email-log":
            email_log(args.last, args.ref, args.to)
        elif args.command == "item-check":
            item_check(args.item)
        elif args.command == "email-test":
            email_test(args.to)
        elif args.command == "ocr-check":
            ocr_check(args.path)
    except AppError as error:
        raise SystemExit(f"Error: {error.message}")


if __name__ == "__main__":
    main()
