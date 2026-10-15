"""Staff management: the admin's extra power.

"Staff" are users whose role is GUARD or ADMIN. An admin can:
  - add someone as a guard or as another admin
  - change a staff member's role (guard <-> admin) and usual office
  - remove someone from staff (they become a student again)

Two safety rules:
  - An admin can't change their OWN role or remove THEMSELVES. So the
    admin doing the change always stays an admin, which means there is
    always at least one admin: nobody can lock everyone out.
  - Changes take effect immediately: the role is read from the database
    on every request, even for someone who is signed in right now.
"""

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.core.errors import bad_request, conflict, not_found
from app.models import Office, User, UserRole
from app.services.auth import check_staff_email, normalize_email

STAFF_ROLES = (UserRole.GUARD, UserRole.ADMIN)


def list_staff(db: Session) -> list[User]:
    """Admins first, then guards, each sorted by name."""
    admins_first = case((User.role == UserRole.ADMIN, 0), else_=1)
    return list(db.scalars(
        select(User).where(User.role.in_(STAFF_ROLES)).order_by(admins_first, User.full_name, User.email)
    ))


def _check_office(db: Session, office_id: int | None) -> None:
    if office_id is not None:
        office = db.get(Office, office_id)
        if office is None or not office.is_active:
            raise bad_request("That office doesn't exist or is closed.")


def add_staff(db: Session, raw_email: str, role: UserRole, office_id: int | None) -> User:
    """Make someone a guard or an admin. If they've never signed in, their
    account is created now, with that role from their first sign-in."""
    email = normalize_email(raw_email)
    check_staff_email(email)  # @iitk.ac.in or @gmail.com
    _check_office(db, office_id)

    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email)
        db.add(user)
    elif user.is_staff:
        raise conflict(f"{email} is already {user.role.lower()}. Change their role in the list instead.")

    user.role = role
    user.office_id = office_id
    db.commit()
    return user


def _get_staff(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or not user.is_staff:
        raise not_found("No staff member with that id.")
    return user


def update_staff(db: Session, admin: User, user_id: int, changes: dict) -> User:
    """Change role and/or usual office. `changes` holds only the fields sent."""
    member = _get_staff(db, user_id)
    if "role" in changes and changes["role"] != member.role:
        if member.id == admin.id:
            raise bad_request("You can't change your own role. Ask another admin.")
        member.role = changes["role"]
    if "office_id" in changes:
        _check_office(db, changes["office_id"])
        member.office_id = changes["office_id"]
    db.commit()
    return member


def remove_staff(db: Session, admin: User, user_id: int) -> User:
    """Turn a guard or admin back into a student."""
    member = _get_staff(db, user_id)
    if member.id == admin.id:
        raise bad_request("You can't remove yourself. Ask another admin.")
    member.role = UserRole.STUDENT
    member.office_id = None
    db.commit()
    return member
