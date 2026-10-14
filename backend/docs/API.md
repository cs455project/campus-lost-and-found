# API reference

Generated from the code by `python -m scripts.export_api_docs` (do not edit by hand).
Interactive version: run the API and open http://127.0.0.1:8000/docs.

**Conventions**

- Auth: a session cookie (`lf_session`) set by `POST /auth/verify-code`. No passwords.
- Errors are JSON `{"detail": "message"}`; validation errors (422) list each field.
- 401 not signed in, 403 not allowed (role or state), 404 doesn't exist *or you may not see it*,
  409 conflicts with current state (e.g. another claim is already approved), 429 too many code requests.
- State changes on an item or its claims lock the item row, so simultaneous requests are served
  one after the other; the loser gets a clean 403/409 (see `app/services/locks.py`).

## Contents

- [health](#health)
- [auth](#auth)
- [users](#users)
- [reference](#reference)
- [items](#items)
- [staff](#staff)
- [claims](#claims)
- [admin](#admin)

## health

### `GET /health`

Health

**Who:** Anyone

**Returns:** `200` no body

## auth

### `POST /auth/request-code`

Step 1: email a 6-digit code. In development it's printed in the
terminal running the API.

**Who:** Anyone

**Body** (`RequestCodeIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `email` | string | yes |  |

**Returns:** `202` CodeSentOut

### `POST /auth/verify-code`

Step 2: check the code. On success, set the session cookie and
return the user. If `is_onboarded` is false, show the onboarding form next.

**Who:** Anyone

**Body** (`VerifyCodeIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `email` | string | yes |  |
| `code` | string | yes |  |

**Returns:** `200` UserOut

### `GET /auth/me`

Who is signed in? 401 if nobody.

**Who:** Signed-in user

**Returns:** `200` UserOut

### `POST /auth/logout`

Sign out: delete the session in the database and remove the cookie.
Safe to call even when not signed in.

**Who:** Signed-in user

**Returns:** `204` no body

## users

### `POST /users/me/onboarding`

First-time profile. Name and roll number can't be changed afterwards.

**Who:** Signed-in user

**Body** (`OnboardingIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `phone` | string | null | no |  |
| `hall` | string | null | no |  |
| `room` | string | null | no |  |
| `full_name` | string | yes | minLength=1; maxLength=120 |
| `roll_number` | string | null | no |  |

**Returns:** `200` UserOut

### `PATCH /users/me`

Change phone, hall or room. Send only the fields you want to change.

**Who:** Signed-in user

**Body** (`ProfileUpdateIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `phone` | string | null | no |  |
| `hall` | string | null | no |  |
| `room` | string | null | no |  |

**Returns:** `200` UserOut

## reference

### `GET /offices`

Open offices, main office first.

**Who:** Signed-in user

**Returns:** `200` list[OfficeOut]

### `GET /categories`

List Categories

**Who:** Signed-in user

**Returns:** `200` list[CategoryOut]

## items

### `GET /items`

The Found page (kind=FOUND) or the Lost page (kind=LOST).

**Who:** Signed-in user

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `kind` | query | ItemKind | yes |
| `category` | query | string | null | no |
| `q` | query | string | null | no |
| `page` | query | integer | no |
| `page_size` | query | integer | no |

**Returns:** `200` ItemPage

### `GET /items/returned`

The Returned Items page, most recent return first. Staff also get
who collected each item and who handed it over. Records are deleted
60 days after the return.

**Who:** Signed-in user

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `category` | query | string | null | no |
| `page` | query | integer | no |
| `page_size` | query | integer | no |

**Returns:** `200` ReturnedPage

### `GET /items/mine`

Everything I've reported, in any status.

**Who:** Signed-in user

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `page` | query | integer | no |
| `page_size` | query | integer | no |

**Returns:** `200` ItemPage

### `POST /items/lost`

Report Lost

**Who:** Signed-in user

**Body** (`LostItemIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string | yes | minLength=1; maxLength=120 |
| `description` | string | yes | minLength=1; maxLength=1000 |
| `category_id` | integer | yes |  |
| `colour` | string | null | no |  |
| `brand` | string | null | no |  |
| `location_text` | string | yes | minLength=1; maxLength=200 |
| `happened_on` | string | yes |  |

**Returns:** `201` ItemDetailOut

### `POST /items/found`

The item stays hidden from the Found page until a guard receives it.

**Who:** Signed-in user

**Body** (`FoundItemIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string | yes | minLength=1; maxLength=120 |
| `description` | string | yes | minLength=1; maxLength=1000 |
| `category_id` | integer | yes |  |
| `colour` | string | null | no |  |
| `brand` | string | null | no |  |
| `location_text` | string | yes | minLength=1; maxLength=200 |
| `happened_on` | string | yes |  |
| `dropoff_office_id` | integer | yes |  |
| `for_lost_id` | integer | null | no |  |

**Returns:** `201` ItemDetailOut

### `GET /items/{item_id}`

Item Detail

**Who:** Signed-in user

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `item_id` | path | integer | yes |

**Returns:** `200` ItemDetailOut

### `PUT /items/{item_id}`

The reporter corrects their report (name, category, colour, brand,
description, place, date). Send all fields, as in the report form.
Allowed while a lost report is open, or a found item isn't handed in yet.

**Who:** Signed-in user

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `item_id` | path | integer | yes |

**Body** (`LostItemIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string | yes | minLength=1; maxLength=120 |
| `description` | string | yes | minLength=1; maxLength=1000 |
| `category_id` | integer | yes |  |
| `colour` | string | null | no |  |
| `brand` | string | null | no |  |
| `location_text` | string | yes | minLength=1; maxLength=200 |
| `happened_on` | string | yes |  |

**Returns:** `200` ItemDetailOut

### `DELETE /items/{item_id}`

The finder withdraws a found report they haven't handed in yet.

**Who:** Signed-in user

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `item_id` | path | integer | yes |

**Returns:** `204` no body

### `GET /items/{item_id}/similar`

"Similar items found": found items that may match a lost report, or
lost reports that may match a found item. Only for the person who
reported the item, and staff. The ranking comes from the matcher
(services/matching.py); with the placeholder it is always empty.

**Who:** Signed-in user

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `item_id` | path | integer | yes |

**Returns:** `200` SimilarOut

### `PUT /items/{item_id}/dropoff-office`

The finder picks a different office to hand the item in at (before hand-in).

**Who:** Signed-in user

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `item_id` | path | integer | yes |

**Body** (`DropoffOfficeIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `office_id` | integer | yes |  |

**Returns:** `200` ItemDetailOut

### `POST /items/{item_id}/close`

Close my lost report: RECOVERED (got it back) or CLOSED (stopped looking).

**Who:** Signed-in user

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `item_id` | path | integer | yes |

**Body** (`CloseLostIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `outcome` | RECOVERED / CLOSED | yes |  |

**Returns:** `200` ItemDetailOut

### `POST /items/{item_id}/photos`

Add one photo (JPEG, PNG or WebP). Call once per photo.

**Who:** Signed-in user

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `item_id` | path | integer | yes |

**Body:** multipart form with one image file `file` (JPEG, PNG or WebP).

**Returns:** `201` ItemDetailOut

### `DELETE /items/{item_id}/photos/{photo_id}`

Remove a photo (e.g. to replace it with a better one).

**Who:** Signed-in user

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `item_id` | path | integer | yes |
| `photo_id` | path | integer | yes |

**Returns:** `200` ItemDetailOut

### `GET /items/{item_id}/photos/{photo_id}`

Photos are served through the API (not as public files), so the same
"who may see this item" rule applies to its photos.

**Who:** Signed-in user

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `item_id` | path | integer | yes |
| `photo_id` | path | integer | yes |

**Returns:** `200` no body

## staff

### `GET /staff/items`

Found items for the staff desk.
waiting = reported, not yet handed in; held = at an office; closed = returned, disposed or donated.

**Who:** Guard or Admin

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `stage` | query | waiting / held / closed | no |
| `office_id` | query | integer | null | no |
| `q` | query | string | null | no |
| `page` | query | integer | no |
| `page_size` | query | integer | no |

**Returns:** `200` StaffItemPage

### `POST /staff/items/walk-in`

Register an item someone handed in without an online report.

**Who:** Guard or Admin

**Body** (`WalkInIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string | yes | minLength=1; maxLength=120 |
| `description` | string | yes | minLength=1; maxLength=1000 |
| `category_id` | integer | yes |  |
| `colour` | string | null | no |  |
| `brand` | string | null | no |  |
| `location_text` | string | yes | minLength=1; maxLength=200 |
| `happened_on` | string | yes |  |
| `office_id` | integer | yes |  |
| `handed_in_by` | string | null | no |  |

**Returns:** `201` ItemDetailOut

### `POST /staff/items/{item_id}/receive`

Receive Item

**Who:** Guard or Admin

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `item_id` | path | integer | yes |

**Body** (`ReceiveIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `office_id` | integer | yes |  |
| `condition_note` | string | null | no |  |

**Returns:** `200` ItemDetailOut

### `POST /staff/items/{item_id}/transfer`

Transfer Item

**Who:** Guard or Admin

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `item_id` | path | integer | yes |

**Body** (`TransferIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `to_office_id` | integer | yes |  |
| `note` | string | null | no |  |

**Returns:** `200` ItemDetailOut

### `POST /staff/items/{item_id}/dispose`

Mark an unclaimed item as DISPOSED or DONATED. A note is required.

**Who:** Guard or Admin

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `item_id` | path | integer | yes |

**Body** (`DisposeIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `outcome` | DISPOSED / DONATED | yes |  |
| `note` | string | yes | minLength=1; maxLength=300 |

**Returns:** `200` ItemDetailOut

## claims

### `POST /items/{item_id}/claims`

"This is mine": start a claim on a found item held at an office.
`details` (up to 5) are separate identifying details a guard will check.

**Who:** Signed-in user

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `item_id` | path | integer | yes |

**Body** (`ClaimIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `description` | string | yes | minLength=1; maxLength=2000 |
| `details` | list[string] | no | maxItems=5 |

**Returns:** `201` ClaimOut

### `GET /claims/mine`

My Claims

**Who:** Signed-in user

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `page` | query | integer | no |
| `page_size` | query | integer | no |

**Returns:** `200` ClaimPage

### `GET /claims/{claim_id}`

Only the claimant and staff can open a claim; for others it's 404.

**Who:** Signed-in user

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `claim_id` | path | integer | yes |

**Returns:** `200` ClaimOut

### `POST /claims/{claim_id}/proofs`

Add one proof image (max 3), before a guard starts reviewing.

**Who:** Signed-in user

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `claim_id` | path | integer | yes |

**Body:** multipart form with one image file `file` (JPEG, PNG or WebP).

**Returns:** `201` ClaimOut

### `GET /claims/{claim_id}/proofs/{proof_id}`

Proof images are private: only the claimant and staff can open them.

**Who:** Signed-in user

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `claim_id` | path | integer | yes |
| `proof_id` | path | integer | yes |

**Returns:** `200` no body

### `POST /claims/{claim_id}/withdraw`

Withdraw Claim

**Who:** Signed-in user

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `claim_id` | path | integer | yes |

**Returns:** `200` ClaimOut

### `GET /staff/claims`

review = new or being reviewed; visit = waiting for an office visit;
collect = approved, waiting for collection; closed = finished.
sort=score puts the highest verification score first (advisory only).

**Who:** Guard or Admin

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `stage` | query | review / visit / collect / closed | no |
| `sort` | query | oldest / score | no |
| `page` | query | integer | no |
| `page_size` | query | integer | no |

**Returns:** `200` ClaimPage

### `POST /staff/claims/{claim_id}/start-review`

Start Review

**Who:** Guard or Admin

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `claim_id` | path | integer | yes |

**Returns:** `200` ClaimOut

### `POST /staff/claims/{claim_id}/request-visit`

The proof isn't enough: ask the claimant to come to the office. The
note tells them what to bring or explain.

**Who:** Guard or Admin

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `claim_id` | path | integer | yes |

**Body** (`RequiredNoteIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `note` | string | yes | minLength=1; maxLength=500 |

**Returns:** `200` ClaimOut

### `POST /staff/claims/{claim_id}/resume`

After the office visit: write down what the claimant showed or said.

**Who:** Guard or Admin

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `claim_id` | path | integer | yes |

**Body** (`RequiredNoteIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `note` | string | yes | minLength=1; maxLength=500 |

**Returns:** `200` ClaimOut

### `POST /staff/claims/{claim_id}/approve`

Approve

**Who:** Guard or Admin

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `claim_id` | path | integer | yes |

**Body** (`OptionalNoteIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `note` | string | null | no |  |

**Returns:** `200` ClaimOut

### `POST /staff/claims/{claim_id}/reject`

A reason is required: the claimant is told why.

**Who:** Guard or Admin

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `claim_id` | path | integer | yes |

**Body** (`RequiredNoteIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `note` | string | yes | minLength=1; maxLength=500 |

**Returns:** `200` ClaimOut

### `POST /staff/claims/{claim_id}/hand-over`

The approved claimant is at the desk and their ID card matches their
name and roll number. The return time is taken from the server clock.

**Who:** Guard or Admin

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `claim_id` | path | integer | yes |

**Body** (`HandOverIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `note` | string | null | no |  |
| `id_checked` | true | yes |  |

**Returns:** `200` ClaimOut

### `PUT /staff/claims/{claim_id}/details/{detail_id}`

Record whether one identifying detail matches the item (MATCH /
NO_MATCH), or clear the check (null). Only while under review.

**Who:** Guard or Admin

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `claim_id` | path | integer | yes |
| `detail_id` | path | integer | yes |

**Body** (`DetailVerdictIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `verdict` | DetailVerdict | null | yes |  |

**Returns:** `200` ClaimOut

### `POST /staff/claims/{claim_id}/details`

Record a detail the claimant told a guard in person, with its verdict.

**Who:** Guard or Admin

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `claim_id` | path | integer | yes |

**Body** (`DeskDetailIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `text` | string | yes |  |
| `verdict` | DetailVerdict | yes |  |

**Returns:** `201` ClaimOut

## admin

### `GET /admin/staff`

Every guard and admin, admins first.

**Who:** Admin

**Returns:** `200` list[UserOut]

### `POST /admin/staff`

Make someone a guard or an admin (creates the account if they've never signed in).

**Who:** Admin

**Body** (`StaffAddIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `email` | string | yes |  |
| `role` | GUARD / ADMIN | no |  |
| `office_id` | integer | null | no |  |

**Returns:** `201` UserOut

### `PATCH /admin/staff/{user_id}`

Change role (guard <-> admin) and/or usual office. Not your own role.

**Who:** Admin

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `user_id` | path | integer | yes |

**Body** (`StaffUpdateIn`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `role` | GUARD / ADMIN | null | no |  |
| `office_id` | integer | null | no |  |

**Returns:** `200` UserOut

### `DELETE /admin/staff/{user_id}`

Turn a guard or admin back into a student. Takes effect immediately. Not yourself.

**Who:** Admin

**Parameters**

| Name | In | Type | Required |
|---|---|---|---|
| `user_id` | path | integer | yes |

**Returns:** `200` UserOut

## Response shapes

### CategoryOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `name` | string | yes |  |
| `slug` | string | yes |  |
| `custody_required` | boolean | no |  |

### ClaimDetailOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `text` | string | yes |  |
| `source` | DetailSource | yes |  |
| `verdict` | DetailVerdict | null | yes |  |
| `checked_by_name` | string | null | yes |  |
| `checked_at` | string | null | yes |  |

### ClaimEventOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `from_status` | ClaimStatus | null | yes |  |
| `to_status` | ClaimStatus | yes |  |
| `actor_name` | string | null | yes |  |
| `note` | string | null | yes |  |
| `created_at` | string | yes |  |

### ClaimItemBrief

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `ref` | string | yes |  |
| `title` | string | yes |  |
| `status` | string | yes |  |
| `category` | CategoryOut | yes |  |
| `cover_photo_id` | integer | null | yes |  |
| `current_office` | OfficeOut | null | yes |  |

### ClaimOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `ref` | string | yes |  |
| `status` | ClaimStatus | yes |  |
| `description` | string | yes |  |
| `item` | ClaimItemBrief | yes |  |
| `proof_ids` | list[integer] | yes |  |
| `details` | list[ClaimDetailOut] | yes |  |
| `staff_message` | string | null | yes |  |
| `created_at` | string | yes |  |
| `approved_at` | string | null | yes |  |
| `collect_by` | string | null | yes |  |
| `collected_at` | string | null | yes |  |
| `events` | list[ClaimEventOut] | yes |  |
| `is_mine` | boolean | yes |  |
| `can_withdraw` | boolean | yes |  |
| `can_add_proof` | boolean | yes |  |
| `on_hold` | boolean | yes |  |
| `claimant` | ClaimantOut | null | yes |  |
| `other_claims` | list[OtherClaimOut] | yes |  |
| `can_start_review` | boolean | yes |  |
| `can_request_info` | boolean | yes |  |
| `can_resume_review` | boolean | yes |  |
| `can_approve` | boolean | yes |  |
| `can_reject` | boolean | yes |  |
| `can_hand_over` | boolean | yes |  |
| `can_check_details` | boolean | yes |  |
| `score` | ScoreOut | null | yes |  |

### ClaimPage

| Field | Type | Required | Notes |
|---|---|---|---|
| `items` | list[ClaimRowOut] | yes |  |
| `total` | integer | yes |  |
| `page` | integer | yes |  |
| `page_size` | integer | yes |  |
| `total_pages` | integer | yes |  |

### ClaimRowOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `ref` | string | yes |  |
| `status` | ClaimStatus | yes |  |
| `item` | ClaimItemBrief | yes |  |
| `claimant_name` | string | null | yes |  |
| `claimant_roll` | string | null | yes |  |
| `created_at` | string | yes |  |
| `updated_at` | string | yes |  |
| `collect_by` | string | null | yes |  |
| `on_hold` | boolean | yes |  |
| `score` | number | null | yes |  |

### ClaimantOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `full_name` | string | null | yes |  |
| `email` | string | yes |  |
| `roll_number` | string | null | yes |  |
| `phone` | string | null | yes |  |

### CodeSentOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `message` | string | yes |  |
| `expires_in_minutes` | integer | yes |  |

### CustodyEventOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `event_type` | CustodyEventType | yes |  |
| `from_office` | OfficeBrief | null | yes |  |
| `to_office` | OfficeBrief | null | yes |  |
| `actor_name` | string | null | yes |  |
| `note` | string | null | yes |  |
| `created_at` | string | yes |  |

### ItemCardOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `ref` | string | yes |  |
| `kind` | ItemKind | yes |  |
| `status` | ItemStatus | yes |  |
| `title` | string | yes |  |
| `description` | string | yes |  |
| `category` | CategoryOut | yes |  |
| `colour` | string | null | yes |  |
| `brand` | string | null | yes |  |
| `location_text` | string | yes |  |
| `happened_on` | string | yes |  |
| `created_at` | string | yes |  |
| `current_office` | OfficeBrief | null | yes |  |
| `cover_photo_id` | integer | null | yes |  |

### ItemClaimRow

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `ref` | string | yes |  |
| `status` | string | yes |  |
| `claimant_name` | string | null | yes |  |
| `created_at` | string | yes |  |

### ItemDetailOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `ref` | string | yes |  |
| `kind` | ItemKind | yes |  |
| `status` | ItemStatus | yes |  |
| `title` | string | yes |  |
| `description` | string | yes |  |
| `category` | CategoryOut | yes |  |
| `colour` | string | null | yes |  |
| `brand` | string | null | yes |  |
| `location_text` | string | yes |  |
| `happened_on` | string | yes |  |
| `created_at` | string | yes |  |
| `current_office` | OfficeBrief | null | yes |  |
| `cover_photo_id` | integer | null | yes |  |
| `photo_ids` | list[integer] | yes |  |
| `dropoff_office` | OfficeOut | null | yes |  |
| `current_office_full` | OfficeOut | null | yes |  |
| `received_at` | string | null | yes |  |
| `is_mine` | boolean | yes |  |
| `can_add_photos` | boolean | yes |  |
| `can_remove_photos` | boolean | yes |  |
| `can_edit` | boolean | yes |  |
| `can_close` | boolean | yes |  |
| `can_change_dropoff` | boolean | yes |  |
| `can_cancel` | boolean | yes |  |
| `answers_lost_id` | integer | null | yes |  |
| `answers_lost_ref` | string | null | yes |  |
| `returned_at` | string | null | yes |  |
| `can_see_similar` | boolean | yes |  |
| `can_claim` | boolean | yes |  |
| `my_claim` | MyClaimBrief | null | yes |  |
| `reporter` | PersonBrief | null | yes |  |
| `received_by_name` | string | null | yes |  |
| `custody_events` | list[CustodyEventOut] | yes |  |
| `can_receive` | boolean | yes |  |
| `can_transfer` | boolean | yes |  |
| `can_dispose` | boolean | yes |  |
| `claims` | list[ItemClaimRow] | yes |  |

### ItemPage

| Field | Type | Required | Notes |
|---|---|---|---|
| `items` | list[ItemCardOut] | yes |  |
| `total` | integer | yes |  |
| `page` | integer | yes |  |
| `page_size` | integer | yes |  |
| `total_pages` | integer | yes |  |

### MyClaimBrief

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `ref` | string | yes |  |
| `status` | string | yes |  |

### OfficeBrief

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `name` | string | yes |  |

### OfficeOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `name` | string | yes |  |
| `location_description` | string | yes |  |
| `opening_hours` | string | yes |  |
| `contact_phone` | string | null | yes |  |
| `is_main` | boolean | yes |  |

### OtherClaimOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `ref` | string | yes |  |
| `status` | ClaimStatus | yes |  |
| `claimant_name` | string | null | yes |  |
| `created_at` | string | yes |  |

### PersonBrief

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `full_name` | string | null | yes |  |
| `email` | string | yes |  |
| `roll_number` | string | null | yes |  |

### ReturnedItemOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `ref` | string | yes |  |
| `kind` | ItemKind | yes |  |
| `status` | ItemStatus | yes |  |
| `title` | string | yes |  |
| `description` | string | yes |  |
| `category` | CategoryOut | yes |  |
| `colour` | string | null | yes |  |
| `brand` | string | null | yes |  |
| `location_text` | string | yes |  |
| `happened_on` | string | yes |  |
| `created_at` | string | yes |  |
| `current_office` | OfficeBrief | null | yes |  |
| `cover_photo_id` | integer | null | yes |  |
| `returned_at` | string | yes |  |
| `returned_from` | OfficeBrief | null | yes |  |
| `staff` | ReturnedStaffOut | null | yes |  |

### ReturnedPage

| Field | Type | Required | Notes |
|---|---|---|---|
| `items` | list[ReturnedItemOut] | yes |  |
| `total` | integer | yes |  |
| `page` | integer | yes |  |
| `page_size` | integer | yes |  |
| `total_pages` | integer | yes |  |

### ReturnedStaffOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `claim_id` | integer | yes |  |
| `claim_ref` | string | yes |  |
| `collected_by` | string | null | yes |  |
| `collected_by_roll` | string | null | yes |  |
| `handed_over_by` | string | null | yes |  |
| `note` | string | null | yes |  |

### ScoreOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `score` | number | yes |  |
| `m` | number | yes |  |
| `r` | number | yes |  |
| `h` | number | yes |  |
| `matched` | integer | yes |  |
| `checked` | integer | yes |  |
| `total` | integer | yes |  |
| `weights` | object | yes |  |
| `scored_at` | string | null | yes |  |

### SimilarItemOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `item` | ItemCardOut | yes |  |
| `score` | number | yes |  |
| `reason` | string | null | yes |  |

### SimilarOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `enabled` | boolean | yes |  |
| `matches` | list[SimilarItemOut] | yes |  |

### StaffItemOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `ref` | string | yes |  |
| `kind` | ItemKind | yes |  |
| `status` | ItemStatus | yes |  |
| `title` | string | yes |  |
| `description` | string | yes |  |
| `category` | CategoryOut | yes |  |
| `colour` | string | null | yes |  |
| `brand` | string | null | yes |  |
| `location_text` | string | yes |  |
| `happened_on` | string | yes |  |
| `created_at` | string | yes |  |
| `current_office` | OfficeBrief | null | yes |  |
| `cover_photo_id` | integer | null | yes |  |
| `dropoff_office` | OfficeBrief | null | yes |  |
| `reporter` | PersonBrief | yes |  |
| `received_at` | string | null | yes |  |
| `updated_at` | string | yes |  |

### StaffItemPage

| Field | Type | Required | Notes |
|---|---|---|---|
| `items` | list[StaffItemOut] | yes |  |
| `total` | integer | yes |  |
| `page` | integer | yes |  |
| `page_size` | integer | yes |  |
| `total_pages` | integer | yes |  |

### UserOut

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes |  |
| `email` | string | yes |  |
| `full_name` | string | null | yes |  |
| `roll_number` | string | null | yes |  |
| `phone` | string | null | yes |  |
| `hall` | string | null | yes |  |
| `room` | string | null | yes |  |
| `role` | UserRole | yes |  |
| `office` | OfficeBrief | null | yes |  |
| `is_onboarded` | boolean | yes |  |

