"""Claim endpoints.

Students:  claim an item ("This is mine"), add proof images, see and
           withdraw their own claims.
Staff:     the Claims desk: review, ask for an office visit, approve,
           reject, and hand the item over after the ID-card check.

The rules (who may do what, and when) live in services/claims.py.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, File, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.core.deps import DbSession, OnboardedUser, StaffUser
from app.core.errors import not_found
from app.models import ClaimProof
from app.schemas.claim import ClaimIn, ClaimOut, ClaimPage, HandOverIn, OptionalNoteIn, RequiredNoteIn
from app.services import claims
from app.services import items as items_service
from app.services import storage

router = APIRouter(tags=["claims"])

PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=50)]


# ---------------- Students (and anyone signed in) ----------------


@router.post("/items/{item_id}/claims", response_model=ClaimOut, status_code=status.HTTP_201_CREATED)
def submit_claim(item_id: int, data: ClaimIn, user: OnboardedUser, db: DbSession):
    """"This is mine": start a claim on a found item held at an office."""
    item = items_service.get_visible_item(db, item_id, user)
    claim = claims.submit(db, item, user, data.description)
    return claims.detail_for(db, claim, user)


@router.get("/claims/mine", response_model=ClaimPage)
def my_claims(user: OnboardedUser, db: DbSession, page: PageNumber = 1, page_size: PageSize = 20):
    return claims.list_mine(db, user, page, page_size)


@router.get("/claims/{claim_id}", response_model=ClaimOut)
def claim_detail(claim_id: int, user: OnboardedUser, db: DbSession):
    """Only the claimant and staff can open a claim; for others it's 404."""
    claim = claims.get_claim(db, claim_id, user)
    return claims.detail_for(db, claim, user)


@router.post("/claims/{claim_id}/proofs", response_model=ClaimOut, status_code=status.HTTP_201_CREATED)
async def add_proof(claim_id: int, user: OnboardedUser, db: DbSession, file: UploadFile = File()):
    """Add one proof image (max 3), before a guard starts reviewing."""
    claim = claims.get_claim(db, claim_id, user)
    data, content_type, extension = await storage.read_image(file)
    claims.add_proof(db, claim, user, data, content_type, extension)
    return claims.detail_for(db, claim, user)


@router.get("/claims/{claim_id}/proofs/{proof_id}")
def get_proof(claim_id: int, proof_id: int, user: OnboardedUser, db: DbSession):
    """Proof images are private: only the claimant and staff can open them."""
    claim = claims.get_claim(db, claim_id, user)
    proof = db.get(ClaimProof, proof_id)
    if proof is None or proof.claim_id != claim.id:
        raise not_found("No proof image with that number.")
    path = storage.path_for(proof.stored_name, folder="proofs")
    if not path.exists():
        raise not_found("The image file is missing.")
    return FileResponse(path, media_type=proof.content_type, headers={"Cache-Control": "private, max-age=600"})


@router.post("/claims/{claim_id}/withdraw", response_model=ClaimOut)
def withdraw_claim(claim_id: int, user: OnboardedUser, db: DbSession):
    claim = claims.get_claim(db, claim_id, user)
    claims.withdraw(db, claim, user)
    return claims.detail_for(db, claim, user)


# ---------------- Staff: the Claims desk ----------------


@router.get("/staff/claims", response_model=ClaimPage)
def staff_claims(
    user: StaffUser,
    db: DbSession,
    stage: Literal["review", "visit", "collect", "closed"] = "review",
    page: PageNumber = 1,
    page_size: PageSize = 20,
):
    """review = new or being reviewed; visit = waiting for an office visit;
    collect = approved, waiting for collection; closed = finished."""
    return claims.list_for_staff(db, stage, page, page_size)


@router.post("/staff/claims/{claim_id}/start-review", response_model=ClaimOut)
def start_review(claim_id: int, user: StaffUser, db: DbSession):
    claim = claims.get_claim(db, claim_id, user)
    claims.start_review(db, claim, user)
    return claims.detail_for(db, claim, user)


@router.post("/staff/claims/{claim_id}/request-visit", response_model=ClaimOut)
def request_visit(claim_id: int, data: RequiredNoteIn, user: StaffUser, db: DbSession):
    """The proof isn't enough: ask the claimant to come to the office. The
    note tells them what to bring or explain."""
    claim = claims.get_claim(db, claim_id, user)
    claims.request_office_visit(db, claim, user, data.note)
    return claims.detail_for(db, claim, user)


@router.post("/staff/claims/{claim_id}/resume", response_model=ClaimOut)
def resume_review(claim_id: int, data: RequiredNoteIn, user: StaffUser, db: DbSession):
    """After the office visit: write down what the claimant showed or said."""
    claim = claims.get_claim(db, claim_id, user)
    claims.resume_review(db, claim, user, data.note)
    return claims.detail_for(db, claim, user)


@router.post("/staff/claims/{claim_id}/approve", response_model=ClaimOut)
def approve(claim_id: int, data: OptionalNoteIn, user: StaffUser, db: DbSession):
    claim = claims.get_claim(db, claim_id, user)
    claims.approve(db, claim, user, data.note)
    return claims.detail_for(db, claim, user)


@router.post("/staff/claims/{claim_id}/reject", response_model=ClaimOut)
def reject(claim_id: int, data: RequiredNoteIn, user: StaffUser, db: DbSession):
    """A reason is required: the claimant is told why."""
    claim = claims.get_claim(db, claim_id, user)
    claims.reject(db, claim, user, data.note)
    return claims.detail_for(db, claim, user)


@router.post("/staff/claims/{claim_id}/hand-over", response_model=ClaimOut)
def hand_over(claim_id: int, data: HandOverIn, user: StaffUser, db: DbSession):
    """The approved claimant is at the desk and their ID card matches their
    name and roll number. The return time is taken from the server clock."""
    claim = claims.get_claim(db, claim_id, user)
    claims.hand_over(db, claim, user, data.note)
    return claims.detail_for(db, claim, user)
