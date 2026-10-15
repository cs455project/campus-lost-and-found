"""Fill the database with the starting categories and offices.

Run from the backend folder with:
    python -m app.seed

It is safe to run more than once: rows that already exist (matched by
name) are left alone, so you never get duplicates. This property is
called "idempotent".

The office names below are PLACEHOLDERS. Replace them with your real
security offices before the demo.
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

# TODO(team): replace with the real offices, directions and hours.
OFFICES = [
    {
        "name": "Main Security Office",
        "location_description": "PLACEHOLDER: update with real directions",
        "opening_hours": "24 hours",
        "is_main": True,
    },
    *[
        {
            "name": f"Security Sub-office {letter}",
            "location_description": "PLACEHOLDER: update with real directions",
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


def main() -> None:
    # "with" closes the session at the end; begin() commits if everything
    # succeeds, or rolls back (undoes) all of it if anything fails.
    with SessionLocal() as db, db.begin():
        categories = seed_categories(db)
        offices = seed_offices(db)
    print(f"Added {categories} categories and {offices} offices.")


if __name__ == "__main__":
    main()
