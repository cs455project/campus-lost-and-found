"""Fill the database with the starting categories and offices.

Run from the backend folder with:
    python -m app.seed

It is safe to run more than once: rows that already exist (matched by
name) are left alone, so you never get duplicates. This property is
called "idempotent".

Office directions are not seeded: they are set on the server with
    python -m app.cli set-office "Main Security Office" --where "..."
so no made-up directions ever reach a student. Until then, emails and pages
show the office name and opening hours only.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import Category, Office

# (name, slug) in the order they should appear in the filter.
CATEGORIES = [
    ("Electronics", "electronics"),
    ("Cycle", "cycle"),
    ("ID Cards & Documents", "id-cards-documents"),
    ("Keys", "keys"),
    ("Wallets & Purses", "wallets-purses"),
    ("Bags", "bags"),
    ("Water Bottles", "water-bottles"),
    ("Clothing", "clothing"),
    ("Books & Stationery", "books-stationery"),
    ("Jewellery & Watches", "jewellery-watches"),
    ("Spectacles", "spectacles"),
    ("Sports Equipment", "sports-equipment"),
    ("Other", "other"),
]

OFFICES = [
    {
        "name": "Main Security Office",
        "location_description": "",
        "opening_hours": "24 hours",
        "is_main": True,
    },
    *[
        {
            "name": f"Security Sub-office {letter}",
            "location_description": "",
            "opening_hours": "8:00 AM - 8:00 PM",
            "is_main": False,
        }
        for letter in "ABCD"
    ],
]


def seed_categories(db: Session) -> int:
    existing = set(db.scalars(select(Category.name)))
    added = 0
    for position, (name, slug) in enumerate(CATEGORIES, start=1):
        if name not in existing:
            db.add(Category(name=name, slug=slug, sort_order=position * 10))
            added += 1
    return added


def seed_offices(db: Session) -> int:
    existing = set(db.scalars(select(Office.name)))
    added = 0
    for data in OFFICES:
        if data["name"] not in existing:
            db.add(Office(**data))
            added += 1
    return added


def clear_placeholder_directions(db: Session) -> int:
    """Earlier versions seeded "PLACEHOLDER: update with real directions",
    which reached a student in an email. Blank it wherever it is still set."""
    offices = list(db.scalars(select(Office).where(Office.location_description.ilike("PLACEHOLDER%"))))
    for office in offices:
        office.location_description = ""
    return len(offices)


def main() -> None:
    # "with" closes the session at the end; begin() commits if everything
    # succeeds, or rolls back (undoes) all of it if anything fails.
    with SessionLocal() as db, db.begin():
        categories = seed_categories(db)
        offices = seed_offices(db)
        cleared = clear_placeholder_directions(db)
    print(f"Added {categories} categories and {offices} offices.")
    if cleared:
        print(f"Cleared placeholder directions from {cleared} offices.")


if __name__ == "__main__":
    main()
