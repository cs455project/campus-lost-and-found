"""Admin-only endpoints: managing staff (guards and other admins).
Every endpoint here requires AdminUser, so guards and students get 403.
The rules (e.g. you can't remove yourself) live in services/admin.py."""

from fastapi import APIRouter, status

from app.core.deps import AdminUser, DbSession
from app.schemas.admin import StaffAddIn, StaffUpdateIn
from app.schemas.user import UserOut
from app.services import admin as admin_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/staff", response_model=list[UserOut])
def list_staff(admin: AdminUser, db: DbSession):
    """Every guard and admin, admins first."""
    return admin_service.list_staff(db)


@router.post("/staff", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def add_staff(data: StaffAddIn, admin: AdminUser, db: DbSession):
    """Make someone a guard or an admin (creates the account if they've never signed in)."""
    return admin_service.add_staff(db, data.email, data.role, data.office_id)


@router.patch("/staff/{user_id}", response_model=UserOut)
def update_staff(user_id: int, data: StaffUpdateIn, admin: AdminUser, db: DbSession):
    """Change role (guard <-> admin) and/or usual office. Not your own role."""
    # exclude_unset: only the fields actually sent, so "office_id": null
    # (clear it) is different from not mentioning office_id at all.
    changes = data.model_dump(exclude_unset=True)
    if changes.get("role") is None:
        changes.pop("role", None)
    return admin_service.update_staff(db, admin, user_id, changes)


@router.delete("/staff/{user_id}", response_model=UserOut)
def remove_staff(user_id: int, admin: AdminUser, db: DbSession):
    """Turn a guard or admin back into a student. Takes effect immediately. Not yourself."""
    return admin_service.remove_staff(db, admin, user_id)
