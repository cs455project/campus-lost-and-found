"""Item endpoints: report, list, view, photos, and small updates.

Every endpoint needs a signed-in user who has completed their profile
(OnboardedUser). The rules themselves live in services/items.py.
"""

from typing import Annotated

from fastapi import APIRouter, File, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.core.deps import DbSession, OnboardedUser
from app.core.errors import forbidden, not_found
from app.models import ItemKind, ItemPhoto
from app.schemas.item import (
    CloseLostIn,
    DropoffOfficeIn,
    FoundItemIn,
    ItemDetailOut,
    ItemPage,
    LostItemIn,
    ReturnedPage,
    SimilarOut,
)
from app.services import items as items_service
from app.services import matching, returned, storage

router = APIRouter(prefix="/items", tags=["items"])

# Query parameters, with limits FastAPI checks before our code runs.
# ge = "greater than or equal", le = "less than or equal".
PageNumber = Annotated[int, Query(ge=1, description="Page to show, starting at 1")]
PageSize = Annotated[int, Query(ge=1, le=48, description="Items per page (max 48)")]


@router.get("", response_model=ItemPage)
def list_items(
    user: OnboardedUser,
    db: DbSession,
    kind: ItemKind,
    category: Annotated[str | None, Query(max_length=60, description="Category slug, e.g. electronics")] = None,
    q: Annotated[str | None, Query(max_length=100, description="Words to search for")] = None,
    page: PageNumber = 1,
    page_size: PageSize = 12,
):
    """The Found page (kind=FOUND) or the Lost page (kind=LOST)."""
    search = q.strip() if q else None
    return items_service.list_public(db, kind, category, search or None, page, page_size)


@router.get("/returned", response_model=ReturnedPage)
def returned_items(
    user: OnboardedUser,
    db: DbSession,
    category: Annotated[str | None, Query(max_length=60)] = None,
    page: PageNumber = 1,
    page_size: PageSize = 12,
):
    """The Returned Items page, most recent return first. Staff also get
    who collected each item and who handed it over. Records are deleted
    60 days after the return."""
    return returned.list_returned(db, user, category, page, page_size)


@router.get("/mine", response_model=ItemPage)
def my_items(user: OnboardedUser, db: DbSession, page: PageNumber = 1, page_size: PageSize = 12):
    """Everything I've reported, in any status."""
    return items_service.list_mine(db, user, page, page_size)


@router.post("/lost", response_model=ItemDetailOut, status_code=status.HTTP_201_CREATED)
def report_lost(data: LostItemIn, user: OnboardedUser, db: DbSession):
    item = items_service.report_lost(db, user, data)
    return items_service.detail_for(db, item, user)


@router.post("/found", response_model=ItemDetailOut, status_code=status.HTTP_201_CREATED)
def report_found(data: FoundItemIn, user: OnboardedUser, db: DbSession):
    """The item stays hidden from the Found page until a guard receives it."""
    item = items_service.report_found(db, user, data)
    return items_service.detail_for(db, item, user)


@router.get("/{item_id}", response_model=ItemDetailOut)
def item_detail(item_id: int, user: OnboardedUser, db: DbSession):
    item = items_service.get_visible_item(db, item_id, user)
    return items_service.detail_for(db, item, user)


@router.get("/{item_id}/similar", response_model=SimilarOut)
def similar_items(item_id: int, user: OnboardedUser, db: DbSession):
    """"Similar items found": found items that may match a lost report, or
    lost reports that may match a found item. Only for the person who
    reported the item, and staff. The ranking comes from the matcher
    (services/matching.py); with the placeholder it is always empty."""
    item = items_service.get_visible_item(db, item_id, user)
    if not matching.can_see_similar(item, user):
        raise forbidden("Suggestions are shown to the person who reported the item, while it's still open.")
    return matching.similar_items(db, item)


@router.put("/{item_id}", response_model=ItemDetailOut)
def edit_report(item_id: int, data: LostItemIn, user: OnboardedUser, db: DbSession):
    """The reporter corrects their report (name, category, colour, brand,
    description, place, date). Send all fields, as in the report form.
    Allowed while a lost report is open, or a found item isn't handed in yet."""
    item = items_service.get_visible_item(db, item_id, user)
    items_service.update_report(db, item, user, data)
    return items_service.detail_for(db, item, user)


@router.put("/{item_id}/dropoff-office", response_model=ItemDetailOut)
def change_dropoff_office(item_id: int, data: DropoffOfficeIn, user: OnboardedUser, db: DbSession):
    """The finder picks a different office to hand the item in at (before hand-in)."""
    item = items_service.get_visible_item(db, item_id, user)
    items_service.change_dropoff(db, item, user, data.office_id)
    return items_service.detail_for(db, item, user)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_found_report(item_id: int, user: OnboardedUser, db: DbSession):
    """The finder withdraws a found report they haven't handed in yet."""
    item = items_service.get_visible_item(db, item_id, user)
    items_service.cancel_found(db, item, user)


@router.post("/{item_id}/close", response_model=ItemDetailOut)
def close_lost(item_id: int, data: CloseLostIn, user: OnboardedUser, db: DbSession):
    """Close my lost report: RECOVERED (got it back) or CLOSED (stopped looking)."""
    item = items_service.get_visible_item(db, item_id, user)
    items_service.close_lost(db, item, user, data)
    return items_service.detail_for(db, item, user)


@router.post("/{item_id}/photos", response_model=ItemDetailOut, status_code=status.HTTP_201_CREATED)
async def upload_photo(item_id: int, user: OnboardedUser, db: DbSession, file: UploadFile = File()):
    """Add one photo (JPEG, PNG or WebP). Call once per photo."""
    item = items_service.get_visible_item(db, item_id, user)
    data, content_type, extension = await storage.read_image(file)
    items_service.add_photo(db, item, user, data, content_type, extension)
    return items_service.detail_for(db, item, user)


@router.delete("/{item_id}/photos/{photo_id}", response_model=ItemDetailOut)
def delete_photo(item_id: int, photo_id: int, user: OnboardedUser, db: DbSession):
    """Remove a photo (e.g. to replace it with a better one)."""
    item = items_service.get_visible_item(db, item_id, user)
    items_service.remove_photo(db, item, user, photo_id)
    return items_service.detail_for(db, item, user)


@router.get("/{item_id}/photos/{photo_id}")
def get_photo(item_id: int, photo_id: int, user: OnboardedUser, db: DbSession):
    """Photos are served through the API (not as public files), so the same
    "who may see this item" rule applies to its photos."""
    item = items_service.get_visible_item(db, item_id, user)
    photo = db.get(ItemPhoto, photo_id)
    if photo is None or photo.item_id != item.id or not items_service.photos_visible(item, user):
        raise not_found("No photo with that number.")
    path = storage.path_for(photo.stored_name)
    if not path.exists():
        raise not_found("The photo file is missing.")
    # private: the browser may cache it, but shared caches must not.
    return FileResponse(path, media_type=photo.content_type, headers={"Cache-Control": "private, max-age=3600"})
