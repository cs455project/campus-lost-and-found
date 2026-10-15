"""Tests for onboarding, profile editing, and role permissions."""

from app.models import UserRole

STUDENT = "riya@iitk.ac.in"
PROFILE = {
    "full_name": "  Riya   Sharma ",
    "roll_number": "220981",
    "phone": "98765 43210",
    "hall": "Hall 5",
    "room": "B-204",
}


def test_onboarding_saves_and_tidies_the_profile(sign_in):
    client = sign_in(STUDENT)
    response = client.post("/users/me/onboarding", json=PROFILE)
    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Riya Sharma"
    assert body["phone"] == "+919876543210"
    assert body["is_onboarded"] is True


def test_students_must_give_a_roll_number(sign_in):
    client = sign_in(STUDENT)
    response = client.post("/users/me/onboarding", json={**PROFILE, "roll_number": ""})
    assert response.status_code == 400


def test_invalid_phone_is_rejected(sign_in):
    client = sign_in(STUDENT)
    response = client.post("/users/me/onboarding", json={**PROFILE, "phone": "12345"})
    assert response.status_code == 422


def test_roll_number_must_be_unique(sign_in):
    sign_in(STUDENT).post("/users/me/onboarding", json=PROFILE)
    other = sign_in("aman@iitk.ac.in")
    response = other.post("/users/me/onboarding", json={**PROFILE, "full_name": "Aman"})
    assert response.status_code == 409


def test_onboarding_only_once(sign_in):
    client = sign_in(STUDENT)
    client.post("/users/me/onboarding", json=PROFILE)
    again = client.post("/users/me/onboarding", json={**PROFILE, "full_name": "Someone Else"})
    assert again.status_code == 400


def test_phone_hall_room_editable_but_name_and_roll_locked(sign_in):
    client = sign_in(STUDENT)
    client.post("/users/me/onboarding", json=PROFILE)

    updated = client.patch("/users/me", json={"room": "C-101"})
    assert updated.status_code == 200
    assert updated.json()["room"] == "C-101"
    assert updated.json()["hall"] == "Hall 5"  # untouched fields stay

    assert client.patch("/users/me", json={"full_name": "Fake"}).status_code == 422
    assert client.patch("/users/me", json={"roll_number": "999999"}).status_code == 422


def test_staff_onboard_without_roll_number(sign_in, make_user):
    make_user("guard1@iitk.ac.in", role=UserRole.GUARD)
    client = sign_in("guard1@iitk.ac.in")
    response = client.post("/users/me/onboarding", json={"full_name": "Ramesh Kumar", "phone": "9876500001"})
    assert response.status_code == 200
    assert response.json()["is_onboarded"] is True


def test_mobile_number_is_required_for_everyone(sign_in, make_user):
    student = sign_in(STUDENT)
    no_phone = {k: v for k, v in PROFILE.items() if k != "phone"}
    assert student.post("/users/me/onboarding", json=no_phone).status_code == 422
    assert student.post("/users/me/onboarding", json={**PROFILE, "phone": "  "}).status_code == 422

    make_user("guard9@iitk.ac.in", role=UserRole.GUARD)
    guard = sign_in("guard9@iitk.ac.in")
    assert guard.post("/users/me/onboarding", json={"full_name": "Ramesh Kumar"}).status_code == 422


def test_mobile_number_can_be_changed_but_not_removed(sign_in):
    client = sign_in(STUDENT)
    client.post("/users/me/onboarding", json=PROFILE)
    assert client.patch("/users/me", json={"phone": "9123456780"}).json()["phone"] == "+919123456780"
    assert client.patch("/users/me", json={"phone": ""}).status_code == 422


def test_reference_lists_need_sign_in(make_client, sign_in):
    assert make_client().get("/offices").status_code == 401
    assert sign_in(STUDENT).get("/categories").status_code == 200


# ---------------- Admin and guard management ----------------

def signed_in_admin(sign_in, make_user):
    make_user("admin@iitk.ac.in", role=UserRole.ADMIN, full_name="Admin")
    return sign_in("admin@iitk.ac.in")


def test_students_cannot_use_admin_endpoints(sign_in):
    client = sign_in(STUDENT)
    client.post("/users/me/onboarding", json=PROFILE)
    assert client.get("/admin/staff").status_code == 403


def test_not_onboarded_admin_must_finish_profile_first(sign_in, make_user):
    make_user("admin@iitk.ac.in", role=UserRole.ADMIN)  # no name yet
    client = sign_in("admin@iitk.ac.in")
    response = client.get("/admin/staff")
    assert response.status_code == 403
    assert "profile" in response.json()["detail"]


def test_admin_assigns_and_removes_a_guard(sign_in, make_user):
    admin = signed_in_admin(sign_in, make_user)

    # The guard has never signed in: their account is created now.
    created = admin.post("/admin/staff", json={"email": "guard1@iitk.ac.in"})
    assert created.status_code == 201
    guard_id = created.json()["id"]
    assert created.json()["role"] == "GUARD"
    # The list has admins first, then guards.
    assert [m["role"] for m in admin.get("/admin/staff").json()] == ["ADMIN", "GUARD"]
    assert admin.get("/admin/staff").json()[1]["id"] == guard_id

    # The guard signs in and is a guard from the start.
    guard = sign_in("guard1@iitk.ac.in")
    guard.post("/users/me/onboarding", json={"full_name": "Ramesh Kumar", "phone": "9876500001"})
    assert guard.get("/auth/me").json()["role"] == "GUARD"
    # Guards don't have the admin's extra power.
    assert guard.get("/admin/staff").status_code == 403

    # Removing takes effect immediately, even while they're signed in.
    removed = admin.delete(f"/admin/staff/{guard_id}")
    assert removed.json()["role"] == "STUDENT"
    assert guard.get("/auth/me").json()["role"] == "STUDENT"


def test_admin_sets_a_guards_usual_office(sign_in, make_user, db):
    from sqlalchemy import select

    from app.models import Office

    office = Office(name="Test Office", location_description="x", opening_hours="x")
    db.add(office)
    db.commit()

    admin = signed_in_admin(sign_in, make_user)
    guard_id = admin.post("/admin/staff", json={"email": "guard1@iitk.ac.in"}).json()["id"]
    response = admin.patch(f"/admin/staff/{guard_id}", json={"office_id": office.id})
    assert response.json()["office"] == {"id": office.id, "name": "Test Office"}

    bad = admin.patch(f"/admin/staff/{guard_id}", json={"office_id": 999999})
    assert bad.status_code == 400
    assert db.scalar(select(Office).where(Office.id == office.id)) is not None


def test_existing_staff_cannot_be_added_again(sign_in, make_user):
    admin = signed_in_admin(sign_in, make_user)
    response = admin.post("/admin/staff", json={"email": "admin@iitk.ac.in"})
    assert response.status_code == 409


# ---------------- Admins managing admins ----------------


def test_admin_adds_another_admin_who_can_manage_staff(sign_in, make_user):
    admin = signed_in_admin(sign_in, make_user)
    created = admin.post("/admin/staff", json={"email": "second.admin@gmail.com", "role": "ADMIN"})
    assert created.status_code == 201 and created.json()["role"] == "ADMIN"

    second = sign_in("second.admin@gmail.com")
    second.post("/users/me/onboarding", json={"full_name": "Second Admin", "phone": "9876500002"})
    assert second.get("/admin/staff").status_code == 200
    # ...and has every guard power too.
    assert second.get("/staff/items").status_code == 200


def test_admin_promotes_and_demotes(sign_in, make_user):
    admin = signed_in_admin(sign_in, make_user)
    member_id = admin.post("/admin/staff", json={"email": "guard1@iitk.ac.in"}).json()["id"]
    assert admin.patch(f"/admin/staff/{member_id}", json={"role": "ADMIN"}).json()["role"] == "ADMIN"
    assert admin.patch(f"/admin/staff/{member_id}", json={"role": "GUARD"}).json()["role"] == "GUARD"
    # Changing only the office leaves the role alone.
    assert admin.patch(f"/admin/staff/{member_id}", json={"office_id": None}).json()["role"] == "GUARD"


def test_admin_cannot_demote_or_remove_themselves(sign_in, make_user):
    """So there is always at least one admin."""
    admin = signed_in_admin(sign_in, make_user)
    me = admin.get("/auth/me").json()["id"]
    assert admin.patch(f"/admin/staff/{me}", json={"role": "GUARD"}).status_code == 400
    assert admin.delete(f"/admin/staff/{me}").status_code == 400
    assert admin.get("/auth/me").json()["role"] == "ADMIN"


def test_one_admin_can_remove_another(sign_in, make_user):
    admin = signed_in_admin(sign_in, make_user)
    other_id = admin.post("/admin/staff", json={"email": "other@iitk.ac.in", "role": "ADMIN"}).json()["id"]
    removed = admin.delete(f"/admin/staff/{other_id}")
    assert removed.json()["role"] == "STUDENT"


def test_guards_cannot_manage_staff(sign_in, make_user):
    make_user("guard9@iitk.ac.in", role=UserRole.GUARD, full_name="Guard Nine")
    guard = sign_in("guard9@iitk.ac.in")
    assert guard.get("/admin/staff").status_code == 403
    assert guard.post("/admin/staff", json={"email": "x@iitk.ac.in", "role": "ADMIN"}).status_code == 403


def test_removed_guard_adds_roll_number_but_keeps_name(sign_in, make_user):
    make_user("admin@iitk.ac.in", role=UserRole.ADMIN, full_name="Admin")
    admin = sign_in("admin@iitk.ac.in")
    guard_id = admin.post("/admin/staff", json={"email": "guard1@iitk.ac.in"}).json()["id"]
    guard = sign_in("guard1@iitk.ac.in")
    guard.post("/users/me/onboarding", json={"full_name": "Ramesh Kumar", "phone": "9876500001"})

    admin.delete(f"/admin/staff/{guard_id}")
    me = guard.get("/auth/me").json()
    assert me["role"] == "STUDENT" and me["is_onboarded"] is False  # needs a roll number now

    done = guard.post("/users/me/onboarding", json={"full_name": "Someone Else", "roll_number": "230555", "phone": "9876500003"})
    assert done.json()["full_name"] == "Ramesh Kumar"  # name stays locked
    assert done.json()["is_onboarded"] is True


# ---------------- Staff may use @gmail.com ----------------


def test_admin_can_add_a_gmail_guard_who_can_then_sign_in(sign_in, make_user):
    admin = signed_in_admin(sign_in, make_user)
    created = admin.post("/admin/staff", json={"email": "Ramesh.Guard@Gmail.com"})
    assert created.status_code == 201, created.text
    assert created.json()["email"] == "ramesh.guard@gmail.com"
    guard = sign_in("ramesh.guard@gmail.com")
    assert guard.get("/auth/me").json()["role"] == "GUARD"


def test_unknown_gmail_cannot_sign_in(make_client):
    # Nobody becomes staff by signing in with Gmail: the admin must add them first.
    response = make_client().post("/auth/request-code", json={"email": "someone@gmail.com"})
    assert response.status_code == 400
    assert "@iitk.ac.in" in response.json()["detail"]


def test_removed_gmail_guard_can_no_longer_sign_in(sign_in, make_user, make_client):
    admin = signed_in_admin(sign_in, make_user)
    guard_id = admin.post("/admin/staff", json={"email": "old.guard@gmail.com"}).json()["id"]
    assert admin.delete(f"/admin/staff/{guard_id}").status_code == 200
    response = make_client().post("/auth/request-code", json={"email": "old.guard@gmail.com"})
    assert response.status_code == 400


def test_other_domains_cannot_be_guards(sign_in, make_user):
    admin = signed_in_admin(sign_in, make_user)
    response = admin.post("/admin/staff", json={"email": "guard@yahoo.com"})
    assert response.status_code == 400
