# Deliverable 2: Dev Sachan's work

Stories owned (D1, Table 19): US-09, US-13, US-14, US-15, US-16, US-22.
Shared epics: EPIC-04 (claims and transactional handover), EPIC-07 (verification).

## 1. What was built

| Story | Jira | What exists now | Main files |
|---|---|---|---|
| US-13 Request Guard custody | SCRUM-17 | `categories.custody_required` (on for bicycles). `items.custody_track_for()` is the single place that decides the track; every found report goes to AWAITING_DROPOFF (D1's AWAITING_DEPOSIT) until a guard receives it. | `models/category.py`, `services/items.py`, `seed.py`, migration `0f3606302be1` |
| US-14 Receive into custody | SCRUM-45 | Receive, transfer and dispose lock the item row and re-check its status, so two guards can't both receive one item and a claim can't land on an item being disposed of. Finder's cancel/edit/change-office lock too. | `services/locks.py`, `services/custody.py`, `services/items.py` |
| US-09 Submit claim | SCRUM-41 | Claims carry up to 5 identifying details (trimmed, non-blank, ≤200 chars, no duplicates). Private: the claimant sees their own text; other claimants get 404; verdicts are staff only. | `models/claim.py` (`ClaimDetail`), `schemas/claim.py`, migration `e559c3518d9e` |
| US-15 Guard claim review | SCRUM-46 | Guard marks each detail Matches / Doesn't match while reviewing; can record details told in person at the office (FR-CUS-06). Every claim action locks the item first, so approve/reject/withdraw can't interleave. | `services/claims.py`, `routers/claims.py` |
| US-16 Guard handover | SCRUM-47 | Hand-over is serialised: a double click or second guard gets 403, one RETURNED custody event. Hand-over adds 1 to the finder's `verified_returns`. Expiry re-checks under the lock. | `services/claims.py` |
| US-22 Verification score | SCRUM-51 | C = 50M + 20R + 30H, fixed weights, staff only, recomputed on every detail check, never changes claim status. Desk can sort by it. Definitions in `backend/docs/VERIFICATION_SCORE.md`. | `services/verification_score.py`, migration `525b13390e3e`, `cli.py rescore-claims` |
| (demo) | SCRUM-47 | `python -m scripts.demo_core_workflow` runs the whole transactional flow on the real API and database, including two guards approving at the same instant. | `backend/scripts/demo_core_workflow.py` |

Frontend: claim form (details list), claim page (score panel, detail checks, desk-detail form; claimant view hides verdicts and score), Claims desk (sort by score, score chip). Checked in Chromium at 1280 px and 390 px.

## 2. Test results

Run from `backend/`: `pytest` → **187 passed** (124 existing + 63 new). Re-run 5 times, no flaky failures.

| File | Tests | Covers |
|---|---|---|
| `tests/test_custody_track.py` | 6 | FR-CUS-01, 02, 08; BR-02 |
| `tests/test_concurrency.py` | 8 | FR-CON-01..04, NFR-CON-01/03/04, FR-CUS-04, FR-CLM-10 |
| `tests/test_claim_details.py` | 13 | FR-CLM-02, 03, 11, 12; FR-CUS-06; NFR-PRIV-01/02; NFR-SEC-05 |
| `tests/test_verification_score.py` | 36 | FR-SCORE-01..04, BR-04, FR-AI-11..13 |

`test_concurrency.py` uses real parallel database connections (threads released together by a barrier). As a check that the tests can fail, the locks were switched off temporarily: 6 of 8 tests failed; with locks on, all 8 pass.

Migrations: `alembic downgrade 2c9f12cd6dff` then `alembic upgrade head` both succeed; `alembic check` reports no drift.

## 3. Requirements traceability (rows for this work)

| Requirement | Story | Use case | Component | Jira | Code | Test | Result |
|---|---|---|---|---|---|---|---|
| FR-CUS-01, FR-CUS-02 | US-13 | UC-13 | Report Service | SCRUM-17 | `items.report_found`, `custody_track_for` | `test_custody_track::test_other_categories_use_guard_track_too` | Pass |
| FR-CUS-08, BR-02 | US-13 | UC-13 | Report Service | SCRUM-17 | `categories.custody_required` | `test_bicycle_report_is_always_on_guard_track`, `test_finder_cannot_skip_the_guard_track` | Pass |
| FR-CUS-03, FR-CUS-04 | US-14 | UC-14 | Custody Service | SCRUM-45 | `custody.receive_item` | `test_two_guards_receiving_the_same_item`; existing `test_staff.py` | Pass |
| FR-CUS-05 | US-14 | UC-14 | Custody + Claim Service | SCRUM-45 | `claims.can_claim` (only held items) | `test_awaiting_item_is_not_claimable_until_a_guard_receives_it` | Pass |
| FR-CLM-01, FR-CLM-04 | US-09 | UC-09 | Claim Service | SCRUM-41 | `claims.submit` | `test_claims.py`; `test_same_person_submitting_twice_at_once` | Pass |
| FR-CLM-02 | US-09 | UC-09 | Claim Service | SCRUM-41 | `ClaimDetail`, `ClaimIn.details` | `test_claim_stores_details_for_the_claimant`, `test_invalid_details_are_refused` | Pass |
| FR-CLM-03, NFR-PRIV-01 | US-09 | UC-09 | Claim Service | SCRUM-41 | `detail_for` | `test_details_never_appear_on_the_item_page` | Pass |
| FR-CLM-11, NFR-PRIV-02 | US-09 | UC-09 | Claim Service | SCRUM-41 | `claims.get_claim` | `test_competing_claimant_cannot_open_another_claim` | Pass |
| FR-CLM-07, FR-CLM-08 | US-15 | UC-15 | Claim Service | SCRUM-46 | `check_detail`, `approve`, `reject` | `test_guard_checks_details_only_during_review`, `test_students_cannot_check_details` | Pass |
| FR-CLM-12 | US-15 | UC-15 | Claim Service | SCRUM-46 | verdicts staff-only in `detail_for` | `test_guard_checks_details_only_during_review` | Pass |
| FR-CUS-06 | US-15 | UC-15 | Claim Service | SCRUM-46 | `add_desk_detail` | `test_guard_records_a_detail_told_in_person` | Pass |
| FR-CUS-07, FR-CLM-09, FR-CLM-10 | US-16 | UC-16 | Claim + Custody Service | SCRUM-47 | `claims.hand_over` | `test_double_hand_over_returns_item_once`; demo step 7–8 | Pass |
| FR-CON-01, FR-CON-04, NFR-CON-01 | US-12 (shared) | UC-12 | Claim Service + PostgreSQL | SCRUM-44 | `approve` + `uq_claims_one_approved_per_item` | `test_two_guards_approving_two_claims_on_one_item` (×3) | Pass |
| FR-CON-02, FR-CON-03, NFR-CON-03 | US-12, US-15 | UC-12 | `services/locks.py` | SCRUM-46 | `lock_item`, `lock_claim` | `test_approve_and_reject_at_once_leave_a_consistent_history`, `test_claim_and_disposal_at_once` | Pass |
| FR-SCORE-01 | US-22 | UC-22 | Verification Score Logic | SCRUM-51 | `verification_score.compute` | `test_compute`, `test_score_is_reproducible_from_stored_factors` | Pass |
| FR-SCORE-02, FR-SCORE-03 | US-22 | UC-22 | Verification Score Logic | SCRUM-51 | constants; no matching imports | `test_score_does_not_use_the_ai_matcher`, `test_ai_matcher_cannot_reach_the_score_or_claims` | Pass |
| FR-SCORE-04, BR-04 | US-22 | UC-21/22 | Claim Service | SCRUM-51 | score never calls `_move` | `test_guard_checks_raise_m_but_never_change_the_status`, `test_low_score_can_still_be_approved_by_a_human` | Pass |

## 4. Prompt / AI Engineering Log entries

Format follows D1 Table 21. Tool: Claude (Anthropic), used as a coding assistant on the team's repository. Every output was run against the test suite and read before committing.

| ID | Activity | AI use / output | Evaluation, changes, disposition |
|---|---|---|---|
| AI-LOG-D2-DEV-01 | Gap analysis | Given the D1 report and the repo, AI mapped US-09/13/14/15/16/22 to existing code. Found claims and custody already implemented; missing were the score (US-22), separate identifying details (FR-CLM-02), and locking on custody and most claim actions (only approve locked). | Checked by reading `services/claims.py` and `services/custody.py`. Accepted. Direct finder track (US-10/11) left to its owner. |
| AI-LOG-D2-DEV-02 | Defining M, R, H | SRS gives C = 50M + 20R + 30H but not exact definitions. AI proposed M = matched/total details (unchecked = not matched), R = 1 − days/30, H = min(returns, 3)/3, with H counting items the claimant **handed in** that were returned. | Alternative considered: H from the claimant's past successful claims. Rejected because it rewards frequent claiming. **Team should confirm these definitions**; they are isolated in two functions. |
| AI-LOG-D2-DEV-03 | Concurrency design | AI proposed one lock helper (item row first, then claim) used by every custody and claim action, with IntegrityError → 409 as second line. | Accepted (matches ADR-003). Verified by mutation: with locks disabled 6/8 concurrency tests fail. |
| AI-LOG-D2-DEV-04 | Migrations | Alembic autogenerate produced duplicate CHECK constraints for the new enum columns and missed hand-written CHECKs. | AI output corrected by hand to the team's existing style (String columns + named CHECK); `alembic check` clean; downgrade/upgrade tested. |
| AI-LOG-D2-DEV-05 | Test review | AI's first CLI test called `rescore-claims` on the whole database, which would score real development data. | Caught in review; function now takes `only_ids` and the test touches only its own rows. |
| AI-LOG-D2-DEV-06 | Demo script | First version: when the low-score claim won the approval race, the demo handed the item to the wrong claimant. | Changed so the guard rejects the mistaken approval and approves the right claim, showing the human stays in control (BR-04). |
| AI-LOG-D2-DEV-07 | Frontend | AI added the details list, score panel and desk sort; screenshots taken at 1280 and 390 px. | Fixed list indentation found in the screenshots. Accepted. |

Risks applied from D1 Table 22: fake precision (weights fixed by the SRS, not invented; window and cap documented as team choices), unsafe AI authority (score cannot change status; tested), sensitive-data leakage (details and verdicts hidden from claimants and rivals; tested).

## 5. Jira and GitHub

### Branches, commits, PRs

Each commit message starts with its Jira key, so Jira's GitHub integration links it automatically.

| Order | Branch | Commit / PR title | Suggested reviewer |
|---|---|---|---|
| 1 | `feature/SCRUM-17-guard-custody-track` | SCRUM-17 enforce SIS Guard custody track for bicycles | Rohit Yadav (Report Service) |
| 2 | `feature/SCRUM-45-custody-locking` | SCRUM-45 lock item row when receiving, moving or disposing | Ankit Agarwal (schema/concurrency) |
| 3 | `feature/SCRUM-46-47-claim-locking` | SCRUM-46 SCRUM-47 serialise claim review and hand-over per item | Ankit Kumar Dubey (US-12 owner) |
| 4 | `feature/SCRUM-41-claim-details` | SCRUM-41 identifying details on claims, checked by a guard | Bhanu Pratap (RBAC/privacy) |
| 5 | `feature/SCRUM-51-verification-score` | SCRUM-51 deterministic verification score C = 50M + 20R + 30H | Ankit Kumar Dubey (AI boundary) |
| 6 | `feature/SCRUM-51-score-frontend` | SCRUM-41 SCRUM-51 frontend: identifying details, score panel, desk sort | Soumya Sahu |
| 7 | `feature/SCRUM-47-demo-and-docs` | SCRUM-47 core workflow demo; docs | any teammate |

Branches are stacked: open PR 1 against `main`, merge it, then PR 2 against `main`, and so on. Each PR then shows only its own commit.

### PR description template

```
SCRUM-XX <title>

Requirements: <FR/NFR IDs from the commit message>
What changed: <2–3 lines>
Migration: <revision id, or none>
Testing: pytest (187 passed); <new test file>; <manual check>
Reviewer checklist:
- [ ] Every state change on an item/claim calls lock_item / lock_claim first
- [ ] Students can't see verdicts, score or other people's claims
- [ ] Migration upgrade and downgrade both run
```

### Peer review evidence

Ask the reviewer above to leave at least one comment and an Approve on GitHub before merging. Screenshot the PR's "Conversation" and "Files changed" tabs for the report. Do not record a review that hasn't happened.

### Jira

Move SCRUM-17, 41, 45, 46, 47, 51 to Done after their PR merges. Open each issue's Development panel and screenshot it to show the linked branch, commits and PR.

## 6. Hand-offs to teammates

- **Ankit Kumar Dubey (US-10/11 direct track):** when adding the direct-handover track, call `items.custody_track_for(category)` and refuse DIRECT when `custody_required` is true. Use `lock_claim` in the finder's accept/reject.
- **Ankit Agarwal (US-17/18 appointments):** lock the slot row (same pattern as `services/locks.py`) plus a unique index on active reservations. Lock order: item, then claim, then slot.
- **Everyone:** after pulling, run `alembic upgrade head`, `python -m app.seed` (sets the bicycle rule on existing databases) and `python -m app.cli rescore-claims`.
