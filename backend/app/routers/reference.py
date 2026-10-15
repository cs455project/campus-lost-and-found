"""Read-only lists the frontend needs for dropdowns: offices and categories.
Any signed-in user can read them."""

from fastapi import APIRouter
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.models import Category, Office
from app.schemas.reference import CategoryOut, OfficeOut

router = APIRouter(tags=["reference"])


@router.get("/offices", response_model=list[OfficeOut])
def list_offices(user: CurrentUser, db: DbSession):
    """Open offices, main office first."""
    return db.scalars(
        select(Office).where(Office.is_active).order_by(Office.is_main.desc(), Office.name)
    ).all()


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(user: CurrentUser, db: DbSession):
    return db.scalars(
        select(Category).where(Category.is_active).order_by(Category.sort_order, Category.name)
    ).all()
