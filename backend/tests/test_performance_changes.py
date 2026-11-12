"""Tests for the changes made after the load test (testing/perf):
small photo copies for lists, the pixel limit, fewer queries per request,
and the limit on requests worked on at once."""

import asyncio
import io

from PIL import Image
from sqlalchemy import event

from app.core.capacity import ConcurrencyLimit
from app.core.config import get_settings
from app.core.database import engine
from tests.test_items import found_body, lost_body, person, place, upload  # noqa: F401  (fixtures)


def real_jpeg(width=2000, height=1500, exif_gps=False):
    image = Image.new("RGB", (width, height), (200, 40, 40))
    out = io.BytesIO()
    if exif_gps:
        exif = Image.Exif()
        exif[0x8825] = {2: (26.0, 30.0, 0.0), 1: "N"}  # GPSInfo: a location
        image.save(out, "JPEG", exif=exif)
    else:
        image.save(out, "JPEG")
    return out.getvalue()


def test_thumbnail_is_small_webp_without_exif(person, place):
    owner = person()
    item_id = owner.post("/items/lost", json=lost_body(place)).json()["id"]
    original = real_jpeg(exif_gps=True)
    photo_id = upload(owner, item_id, original).json()["photo_ids"][0]

    thumb = owner.get(f"/items/{item_id}/photos/{photo_id}", params={"size": "thumb"})
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/webp"
    assert len(thumb.content) < len(original) / 4
    with Image.open(io.BytesIO(thumb.content)) as image:
        assert max(image.size) == 640
        assert not image.getexif()  # no EXIF, so no GPS location

    # The full photo shows the same picture, but its metadata was removed on
    # upload (tests/test_photo_privacy.py).
    full = owner.get(f"/items/{item_id}/photos/{photo_id}").content
    with Image.open(io.BytesIO(full)) as stored, Image.open(io.BytesIO(original)) as sent:
        assert stored.tobytes() == sent.tobytes()
        assert not stored.getexif()


def test_thumbnail_follows_the_same_permission_rules(person, place):
    finder, other = person(), person()
    item_id = finder.post("/items/found", json=found_body(place)).json()["id"]
    photo_id = upload(finder, item_id, real_jpeg()).json()["photo_ids"][0]
    # Not handed in yet: hidden from other students, thumbnail too.
    assert other.get(f"/items/{item_id}/photos/{photo_id}", params={"size": "thumb"}).status_code == 404
    assert other.get(f"/items/{item_id}/photos/{photo_id + 999}").status_code == 404


def test_old_photos_get_a_thumbnail_when_first_shown(person, place):
    owner = person()
    item_id = owner.post("/items/lost", json=lost_body(place)).json()["id"]
    photo_id = upload(owner, item_id, real_jpeg()).json()["photo_ids"][0]
    for thumb in (get_settings().upload_dir / "thumbs").iterdir():
        thumb.unlink()  # as if uploaded before thumbnails existed
    response = owner.get(f"/items/{item_id}/photos/{photo_id}", params={"size": "thumb"})
    assert response.headers["content-type"] == "image/webp"
    assert len(list((get_settings().upload_dir / "thumbs").iterdir())) == 1


def test_unreadable_photo_falls_back_to_the_original(person, place):
    owner = person()
    item_id = owner.post("/items/lost", json=lost_body(place)).json()["id"]
    data = b"\xff\xd8\xff\xe0" + b"\x00" * 200  # starts like a JPEG, but isn't one
    photo_id = upload(owner, item_id, data).json()["photo_ids"][0]
    response = owner.get(f"/items/{item_id}/photos/{photo_id}", params={"size": "thumb"})
    assert response.status_code == 200 and response.content == data


def test_photo_with_too_many_pixels_is_refused(person, place, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_photo_megapixels", 2)
    owner = person()
    item_id = owner.post("/items/lost", json=lost_body(place)).json()["id"]
    response = upload(owner, item_id, real_jpeg(2000, 1500))  # 3 megapixels
    assert response.status_code == 400
    assert "megapixels" in response.json()["detail"]


def test_signed_in_request_uses_one_query_for_session_and_user(person, place):
    owner = person()
    statements = []

    def count(conn, cursor, statement, *args):
        if not statement.startswith(("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")):
            statements.append(statement)  # savepoints come from the test setup

    event.listen(engine, "before_cursor_execute", count)
    try:
        assert owner.get("/auth/me").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(statements) == 1


def test_requests_over_the_limit_wait_then_get_server_busy(monkeypatch):
    monkeypatch.setattr(get_settings(), "api_max_in_flight", 1)
    monkeypatch.setattr(get_settings(), "api_queue_seconds", 0.05)
    started, release = asyncio.Event(), asyncio.Event()
    sent = []

    async def slow_app(scope, receive, send):
        started.set()
        await release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        sent.append(message)

    async def main():
        limit = ConcurrencyLimit(slow_app)
        scope = {"type": "http", "path": "/items"}
        first = asyncio.create_task(limit(scope, None, send))
        await started.wait()
        await limit(scope, None, send)  # second request: no slot within 0.05 s
        assert sent[0]["status"] == 503
        release.set()
        await first
        assert sent[-2]["status"] == 200
        await limit({"type": "http", "path": "/health"}, None, send)  # /health never queues

    asyncio.run(main())
