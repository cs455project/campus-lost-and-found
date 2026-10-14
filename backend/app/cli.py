"""Command-line tools for the person running the server (run from backend/).

Make someone the admin:
    python -m app.cli make-admin someone@iitk.ac.in

Create sample items to try the Found/Lost pages and page numbers:
    python -m app.cli demo-items --count 30

Give a verification score to claims sent before scores existed:
    python -m app.cli rescore-claims

Timed jobs (reminders, claim expiry, clean-up) are run by the worker:
    python -m app.worker

There's no API endpoint for make-admin on purpose: nobody can become admin
through the website. Only someone with access to the server can do it.
"""

import argparse
import random
from datetime import timedelta

from sqlalchemy import select

from app.core.clock import local, now, today
from app.core.database import SessionLocal
from app.core.errors import AppError
from app.models import (
    Category,
    Claim,
    CustodyEvent,
    CustodyEventType,
    Item,
    ItemKind,
    ItemStatus,
    Office,
    User,
    UserRole,
)
from app.services import verification_score
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


def rescore_claims(only_ids: list[int] | None = None) -> int:
    """Score every claim that has no score yet (sent before US-22 existed).
    R uses the date the claim was sent; H uses the claimant's count today.
    Claims that already have a score are left alone, so it's safe to re-run."""
    with SessionLocal() as db, db.begin():
        query = select(Claim).where(Claim.score.is_(None))
        if only_ids is not None:  # tests: touch only their own claims
            query = query.where(Claim.id.in_(only_ids))
        unscored = list(db.scalars(query))
        for claim in unscored:
            verification_score.score_new_claim(
                claim, claim.item.happened_on, local(claim.created_at).date(), claim.claimant.verified_returns
            )
    print(f"Scored {len(unscored)} claim(s).")
    return len(unscored)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    admin_cmd = commands.add_parser("make-admin", help="give a user the admin role")
    admin_cmd.add_argument("email")

    demo_cmd = commands.add_parser("demo-items", help="create sample items")
    demo_cmd.add_argument("--count", type=int, default=30)

    commands.add_parser("rescore-claims", help="give a verification score to older claims")

    args = parser.parse_args()
    try:
        if args.command == "make-admin":
            make_admin(args.email)
        elif args.command == "demo-items":
            demo_items(args.count)
        elif args.command == "rescore-claims":
            rescore_claims()
    except AppError as error:
        raise SystemExit(f"Error: {error.message}")


if __name__ == "__main__":
    main()
