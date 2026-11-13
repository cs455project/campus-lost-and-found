# TraceBack — Requirements to Jira to GitHub to Testing Traceability Matrix

Nov 6, 2026 · @Soumyaranjan

**All 22 functional requirement groups delivered in TraceBack and all 8 non-functional requirements trace from a requirement to its Jira story, its pull request, the code that implements it and the tests that check it, and every one passes on the final code.** Deliverable 3 changes are shown as commits on the `d3/integration` branch.

TraceBack is the IIT Kanpur Lost & Found portal. Requirement IDs come from Deliverable 1 and the Deliverable 2 report (section 11). Jira keys are from the SCRUM project; the Jira site is currently suspended (free-plan member limit), so the keys are taken from the branch names and commit messages, which carry them. Pull requests are in github.com/cs455project/campus-lost-and-found.

**How to read a row:** automated tests are pytest test functions in `backend/tests/` (459 in total); SYS, ADV, CON and FR are the system, adversarial, concurrency and failure scenarios in the Test Reports, Concurrency and Failure and Recovery reports.

## Functional requirements

| Requirement | Jira story | GitHub | Implementation | Automated tests (examples) | System and D3 tests | Result |
| --- | --- | --- | --- | --- | --- | --- |
| FR-AUTH-01–04: sign in with an IITK email and a one-time code | SCRUM-10 | PR #11; `c2b51ad`, `9fd794f` | `services/auth.py`, `routers/auth.py` | `test_full_sign_in_creates_a_student_account`, `test_expired_code_is_refused`, `test_not_signed_in_gets_401`, `test_a_code_that_could_not_be_sent_does_not_make_the_student_wait` | SYS-02, ADV-01–05, ADV-23, CON-01–03, FR-08 | Pass |
| FR-PROF-01–03: onboarding and profile | SCRUM-35 | PR #12 | `services/users.py` | `test_phone_hall_room_editable_but_name_and_roll_locked`, `test_mobile_number_is_required_for_everyone` | SYS-02, ADV-08 | Pass |
| FR-SRCH-01: browse lost and found listings | SCRUM-39 | PR #13 | `routers/items.py`, `ItemBrowser` | `test_found_item_hidden_until_received`, `test_pagination` | SYS-06, SYS-13 | Pass |
| FR-REP-01, 03–08: report a lost item | SCRUM-16 | PR #22; `9fd794f` | `services/items.py` | `test_report_lost_item`, `test_report_rejects_bad_values`, `test_a_new_report_is_saved_with_its_ai_job_in_one_commit` | SYS-03, ADV-10, FR-04 | Pass |
| FR-REP-02–08: report a found item with photos | SCRUM-36 | PR #23; `423b3bf`, `a541035` | `services/items.py`, `services/storage.py` | `test_report_found_item_waits_for_dropoff`, `test_fake_image_is_rejected`, `test_jpeg_loses_gps_and_xmp_but_keeps_its_picture_and_rotation`, `test_photo_with_too_many_pixels_is_refused` | SYS-04, ADV-15–18, CON-10 | Pass |
| FR-REP-09: edit my report | SCRUM-38 | PR #24 | `update_report` | `test_only_the_reporter_can_edit`, `test_no_edits_after_closing_or_handing_in` | ADV-09 | Pass |
| FR-REP-10–12: close or withdraw my report | SCRUM-37 | PR #24 | `close_lost`, `cancel_found` | `test_close_lost_report`, `test_finder_can_cancel_before_handing_in` | ADV-09 | Pass |
| FR-SRCH-02–06: search and filter | SCRUM-40 | PR #25 | `build_search_query`, `ItemBrowser` | `test_search_matches_word_forms`, `test_category_filter_and_counts` | SYS-06, ADV-12, ADV-13 | Pass |
| FR-CUS-01–02: hand a found item to an SIS guard | SCRUM-17 | PR #17 | drop-off office in `services/items.py` | `test_finder_can_change_the_office_before_handing_in`, `test_dropoff_reminder_after_24_hours_once` | SYS-04, SYS-05 | Pass |
| FR-CUS-03–05: guard receives and holds the item | SCRUM-45 | PR #18; `c2b51ad` | `services/custody.py` | `test_receive_records_who_where_and_when`, `test_receive_only_once_and_only_open_offices` | SYS-05, CON-04, CON-11 | Pass |
| FR-CLM-01–05: claim an item with private details | SCRUM-41 | PR #26; `c2b51ad`, `a541035` | `services/claims.py` | `test_submit_claim_marks_item_claim_pending`, `test_proof_images_are_private`, `test_claim_proofs_are_cleaned_too` | SYS-07, ADV-09, ADV-11, CON-05 | Pass |
| FR-CON-01–04: one approved claim per item | SCRUM-44 | PR #26; `c2b51ad` | row locks and partial unique index | `test_database_allows_only_one_approved_claim`, `test_only_one_open_claim_per_person` | CON-06, CON-07 | Pass |
| FR-CLM-07, 08: guard reviews and verifies claims | SCRUM-46 | PR #27; `c2b51ad` | staff claim endpoints | `test_students_cannot_review`, `test_cannot_skip_steps` | SYS-08, ADV-06, CON-07 | Pass |
| FR-CUS-07: final hand-over and Returned page | SCRUM-47 | PR #28; `c2b51ad` | `hand_over`, `services/returned.py` | `test_hand_over_requires_id_check`, `test_hand_over_returns_item_and_closes_other_claims` | SYS-09, CON-08, CON-09 | Pass |
| FR-AI-01–07: ranked AI match candidates | SCRUM-49 | PR #14; `9fd794f` | `services/ai/semantic.py`, `queue.py` | `test_lost_report_gets_the_matching_found_item`, `test_only_the_top_k_are_kept`, `test_an_interrupted_job_is_run_again` | SYS-06, CON-13, FR-05, FR-06 | Pass |
| FR-AI-08, 09: matching keeps working when the AI fails | SCRUM-50 | PR #15; `a9aba2b` | `services/ai/agent.py`, `llm.py` | `test_provider_failure_falls_back_to_rule_based_matching`, `test_agent_cannot_save_ids_it_was_not_shown`, `test_when_the_agent_is_asked_follows_the_settings` | FR-07 | Pass |
| FR-AI-11–18: AI never decides; ID-card owner alerts | SCRUM-52 | PR #16; `b2b4cf1` | guardrails, `services/ai/idcard.py` | `test_the_agent_never_sees_the_identifiers`, `test_report_text_never_includes_personal_details` | ADV-09 | Pass |
| FR-ADM-01, 03, 04: admin adds and manages staff | SCRUM-53 | PR #19 | `services/admin.py` | `test_admin_adds_another_admin_who_can_manage_staff`, `test_admin_can_add_a_gmail_guard_who_can_then_sign_in` | SYS-10, ADV-07 | Pass |
| FR-ADM-02, 05–08: roles and their limits | SCRUM-54 | PR #19 | `services/admin.py`, `core/deps.py` | `test_guards_cannot_manage_staff`, `test_admin_cannot_demote_or_remove_themselves` | SYS-11, ADV-06, ADV-07 | Pass |
| FR-LIFE-01–03: drop-off, collection and review reminders | SCRUM-19 | PR #20; `a9aba2b` | `services/jobs.py`, `worker.py` | `test_dropoff_reminder_after_24_hours_once`, `test_collect_reminder_after_3_days_once`, `test_approval_email_never_shows_placeholder_text` | SYS-12, CON-12 | Pass |
| FR-LIFE-04, 05: 60-day retention and proof clean-up | SCRUM-55 | PR #20 | `services/jobs.py` | `test_returned_items_deleted_after_60_days`, `test_one_failing_job_does_not_stop_the_others` | SYS-12 | Pass |
| FR-AUD-01–03: audit logs and operator tools | SCRUM-57 | PR #21; `a9aba2b` | `email_logs`, `agent_runs`, `cli.py` | `test_every_attempt_is_logged_without_the_sign_in_code`, `test_a_temporary_failure_is_retried_and_the_email_goes_out` | FR-08, FR-09 | Pass |

## Non-functional requirements

| Requirement | Source | GitHub | Implementation | Automated tests (examples) | System and D3 tests | Result |
| --- | --- | --- | --- | --- | --- | --- |
| NFR-SEC: only the right people see and change data; injection, XSS and malicious uploads are refused | D1; D2 plan | PRs #11, #19; `0e64287`, `a541035` | role dependencies, input schemas, `storage.py`, `core/headers.py` | `test_every_api_response_carries_the_security_headers`, `test_api_docs_can_be_switched_off` | ADV-01–25 (23 attacks stopped, 2 audits clean) | Pass |
| NFR-PRIV: no personal data to AI services; shared photos carry no location | D2 (NFR-PRIV) | PR #16; `a541035`, `b2b4cf1` | agent guardrails, `storage.strip_metadata` | `test_the_agent_never_sees_the_identifiers`, `test_uploaded_item_photos_reach_other_students_without_gps` | ADV-17 | Pass |
| NFR-PERF: 100 daily users and 250+ transactions a day; pages under 3 s, API p95 under 500 ms | D2 plan for D3 | `423b3bf` | `core/capacity.py`, thumbnails, fewer queries | `test_requests_over_the_limit_wait_then_get_server_busy`, `test_signed_in_request_uses_one_query_for_session_and_user` | SYS-15; load tests (ramp, campus, spike); CON-14 | Pass |
| NFR-CONC: parallel actions never break the rules | D2 (FR-CON); D2 plan | PR #26; `c2b51ad` | row locks, advisory lock, `SKIP LOCKED` | `test_database_allows_only_one_approved_claim` | CON-01–14, 40 trials each | Pass |
| NFR-REL: recovers from database, process and outside-service failures | D2 plan for D3 | `9fd794f` | 503 handler, job re-run, restart policy | `test_an_unreachable_database_gives_a_clear_503_not_a_500`, `test_an_interrupted_job_is_run_again` | FR-01–10 | Pass |
| NFR-AI-1: AI calls within the daily limit; clear matches use no AI | D2 (11.3) | PR #15 | `AGENT_MATCHING=ambiguous`, `AI_DAILY_CALL_LIMIT` | `test_daily_limit_stops_ai_calls`, `test_no_photo_description_once_the_daily_ai_limit_is_reached` | FR-07 | Pass |
| NFR-AI-2: pages never fail when the AI fails | D2 (11.3) | PR #15 | rule-based fallback | `test_provider_failure_falls_back_to_rule_based_matching` | FR-07 | Pass |
| NFR-DEP: runs on the AWS free plan, deployed with one command | D2 plan for D3 | `c37f6d3` | `deploy/` (Docker Compose, Caddy, `upload.ps1`) | – (configuration) | Deployment checks; FR-10 | Pass |

## Requirements not delivered

| Requirement | Jira | Decision | Reason |
| --- | --- | --- | --- |
| FR-DIR-01–03: direct hand-over between finder and owner | SCRUM-42, 43 | Dropped by design (D2, RC-05) | Every hand-over goes through an SIS guard, which is safer for valuables |
| FR-CHAT-01–03: chat between finder and claimant | SCRUM-56 | Dropped (D2, RC-16) | Chat would leak the details used to verify ownership and invite social engineering |
| FR-SCORE-01–04: verification score for guards | SCRUM-51 | Descoped in Deliverable 3 | The guard’s review with the claimant’s private details and an ID check covers verification; the score was advisory only |
| FR-APT-01–05: hand-over time slots | SCRUM-18, 48 | Descoped in Deliverable 3 | Office hours, the 7-day collection window and “request an office visit” cover collection |

## Coverage of the chain

- Every delivered functional requirement has at least one automated test and one system-level or Deliverable 3 test; the deployment requirement is checked by the deployment checklist and the power-cut test (FR-10).
- Every story pull request (#11–#28) appears in the matrix, and every Deliverable 3 commit is linked to the requirements it changed.
- The 459 automated tests, 15 system scenarios, 23 attacks, 14 concurrency checks and 10 failure scenarios all pass on the final code (Test Reports, Concurrency, Failure and Recovery reports).
