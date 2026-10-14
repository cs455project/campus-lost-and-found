"""Item categories, used by the filter on the Found and Lost pages
(Electronics, Cycle, Keys, ...)."""

from sqlalchemy import Boolean, Integer, String, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    # URL-friendly version of the name, e.g. "id-cards-documents",
    # so filter links can read /found?category=electronics.
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    # Controls the order in the filter dropdown; "Other" goes last.
    sort_order: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    # Retire a category without deleting items that use it.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # BR-02 / FR-CUS-08: found items in this category must go to the SIS
    # Guard (e.g. bicycles). The finder can never keep them for a direct
    # handover. See services/items.py, custody_track_for().
    custody_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    def __repr__(self) -> str:
        return f"<Category {self.id} {self.name!r}>"
