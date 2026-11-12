# TraceBack — AI Engineering Log

Nov 6, 2026 · @Soumyaranjan

**Across the project the team logged 41 significant AI-assisted decisions: 12 accepted as proposed, 19 modified and 10 rejected. Deliverable 3 adds 15 entries (27–41); in each, the AI’s output was checked by running tests, profilers or audits before it was kept, and 10 of the 15 were changed or rejected after that check.**

TraceBack is the IIT Kanpur Lost & Found portal. Two kinds of AI are involved. Inside the product, Groq (`openai/gpt-oss-20b`) runs the matching agent and Google Gemini (`gemini-2.5-flash`) is its fallback and describes photos, both on free-tier API keys with a cap of 500 calls a day. During development, the team used an LLM coding assistant (Claude, by Anthropic) to write code, tests, test scripts and report drafts. This log covers the second kind: what we asked, what the AI produced, what was wrong or risky, and what we kept.

## How AI output was checked

- **Nothing merged on the AI’s word.** Every AI-written change went through the full pytest suite, and the behaviour it claimed was measured: load with the load generator and py-spy, races with the concurrency suite, security with the attack script and the audits, recovery with the failure suite.
- **Tests first for fixes.** For each defect, a test or scenario that reproduces it was run before the fix and after it.
- **Numbers from files, not memory.** Every figure in the reports was taken from a result file in `testing/results/` or a fetched source, then cross-checked between reports.
- **The model stays at the edge.** The product rule from Deliverable 2 still holds: the LLM proposes rankings and reasons; code owns queries, dates, thresholds, emails and every status change.

## Deliverable 3 entries (27–41)

Author of entries 27–41: Soumyaranjan Sahu, working with the coding assistant on testing, deployment and hardening.

| # | Prompt (summary) | AI output | Our evaluation, issues found | Change made, final artifact | Decision |
| --- | --- | --- | --- | --- | --- |
| 27 | Find out why the API stops answering at 25 users | A load generator and a py-spy profile showing every worker thread blocked on the database pool; proposed capping the requests in flight | Confirmed by re-running every load step after each change: 40 threads waited on 15 connections while their holders waited for a thread; enlarging the pool alone would only move the limit | `core/capacity.py` caps requests in flight at 30 per process, with the pool sized to match; 0 errors at 100 users (D-01) | Accepted |
| 28 | Stop uploads from blocking the event loop | Make the upload endpoints plain `def` so they run in threads | Correct for speed, but concurrency test CON-10 then let 6 parallel uploads pass the 3-photo limit | Item row locked while photos are counted; CON-10 passes 40 of 40 (D-25) | Modified |
| 29 | Write tests for simultaneous requests | Script that releases requests together with a thread barrier, one connection each, repeated per race | Each race repeated 10 and then 30 times on fresh items, so a race had many chances to overlap; results read from the database, not just the HTTP answers | `testing/concurrency_tests.py`, 14 checks; it found D-02 to D-07 | Accepted |
| 30 | Fix the claim and custody races | Lock the item row, then the claim row, in every action, and re-read before checking the rules | Checked the lock order for deadlocks (none in the PostgreSQL log over 40 trials) and that the existing 189 tests still pass | `claims._lock`, `custody._lock`; CON-04 to CON-09 pass | Accepted |
| 31 | Tidy the new test files | Ran an auto-fixer over the tests folder | It also removed deliberate `# noqa: E402` comments from `conftest.py`, which must import after setting environment variables | Fixer’s change to `conftest.py` discarded; file rebuilt from the original plus the new fixture | Rejected |
| 32 | Raise coverage with meaningful tests, no exclusions | 235 tests for the CLI, worker, AI clients, email and API edges | Some test data broke database rules (found items without a drop-off office, an invalid agent outcome, a second main office): the data was wrong, not the app | Test data fixed to respect every constraint; 98% line coverage with no excluded file | Modified |
| 33 | Refuse decompression bombs | Read the image size and refuse anything over 60 megapixels | Broke existing tests that upload placeholder bytes Pillow can’t read | Unreadable headers accepted as before (they get no thumbnail); real bombs refused (D-08) | Modified |
| 34 | Review the system-test screenshots for defects | Reported that the sidebar background stops short on long pages | A full-page screenshot draws a sticky sidebar only once; in a real browser, scrolled, the sidebar stays full height | Finding withdrawn; no change to the app | Rejected |
| 35 | Upgrade Pillow to clear 39 advisories | Pin `pillow==13.0.0` | `pip` reported that fastembed 0.9.0 requires Pillow below 13, which would have broken the Docker build on the server | fastembed 0.9.1 as well; full dependency resolution checked; tests and audits clean (D-12) | Modified |
| 36 | Remove GPS data from uploaded photos | Clean JPEGs segment by segment instead of re-encoding them, keeping only the rotation flag | Verified pixel-identical output, rotation kept, XMP and trailing second images removed, damaged files refused | `storage.strip_metadata`; ADV-17 stopped (D-13) | Accepted |
| 37 | Fix the “PLACEHOLDER” office text in emails | Option considered: fill in plausible directions for each office | Invented directions would send students to the wrong place | No seeded directions; emails omit missing ones; `set-office` command for the real data (D-16) | Rejected |
| 38 | Write failure and recovery tests | Script that stops the database, kills processes and swaps in a fake mail server | First run gave false results: killing uvicorn’s parent left its worker processes serving, and two endpoint paths were wrong | Processes killed by process group, paths fixed; the corrected run found 4 real defects | Modified |
| 39 | Fix the 4 recovery defects | 503 handler, report and AI job in one commit, interrupted jobs re-run with a `started_at` column, unsent codes removed | Each verified by a new test that fails on the old code (9 of 10 failed there) and by the full failure suite | `9fd794f`; FR-01 to FR-10 all pass (D-19 to D-22) | Accepted |
| 40 | Estimate the monthly AWS cost | US$16.35 a month for the instance | That is the Mumbai price; the server runs in Sydney, where the same instance costs US$19.27 | Costing uses fetched Sydney prices: US$24.84 a month in all | Modified |
| 41 | Draft the test reports from the result files | Report text and charts | Cross-checks found figures from runs with different settings side by side (1 and 2 API processes) and test counts out of date after later fixes | Each chart labelled with its run; all counts re-taken from the final result files | Modified |

## Deliverable 2 entries (1–26), in brief

The full entries are in the Deliverable 2 report (section 7).

| # | Member | Topic | Decision |
| --- | --- | --- | --- |
| 1 | Rohit Yadav | Semantic top-k matching design | Accepted |
| 2 | Rohit Yadav | LLM agent on every report, changed to ambiguous-only with a daily limit | Modified |
| 3 | Rohit Yadav | Free LLM providers (a provider listed as free needed a top-up) | Rejected |
| 4 | Rohit Yadav | Agent’s match reasons (a hallucinated date gap) | Modified |
| 5 | Rohit Yadav | Sending item photos to Gemini (ID cards excluded) | Modified |
| 6 | Rohit Yadav | Agent tool to find any item’s owner | Rejected |
| 7 | Team | Roll number in the AI description | Rejected |
| 8 | Rohit Yadav | Offline OCR engine for ID cards | Accepted |
| 9 | Rohit Yadav | Who to email from a found ID card | Modified |
| 10 | Rohit Yadav | Pinning the OpenAI SDK | Modified |
| 11 | Rohit Yadav | Whether a Gemini subscription raises API limits | Accepted |
| 12 | Rohit Yadav | Handling Groq rate limits | Rejected |
| 13 | Soumyaranjan Sahu | Real, branded emails with an email log | Accepted |
| 14 | Team | The TraceBack logo | Accepted |
| 15 | Rohit Yadav | Splitting the code into story PRs | Modified |
| 16 | Soumyaranjan Sahu | Diagnosing “the owner got no email” | Accepted |
| 17 | Ankit Agarwal | Storing sign-in codes and limiting guesses | Modified |
| 18 | Ankit Agarwal | Keeping users signed in (JWT in localStorage) | Rejected |
| 19 | Ankit Kumar Dubey | Two guards approving the same item | Modified |
| 20 | Ankit Kumar Dubey | Claim state transitions | Modified |
| 21 | Bhanu Pratap | Text search | Rejected |
| 22 | Bhanu Pratap | Validating uploaded photos | Modified |
| 23 | Dev Sachan | Modelling custody history | Modified |
| 24 | Dev Sachan | Staff dashboard stages | Accepted |
| 25 | Soumyaranjan Sahu | Reminder emails from the worker | Modified |
| 26 | Soumyaranjan Sahu | Inviting staff by email | Rejected |

## Lessons from Deliverable 3

- **An AI’s test is still a test to check.** The first failure-test run reported passes and failures that were the script’s own mistakes (entry 38); checking the tests found them before they reached a report.
- **Speed fixes create races.** Moving uploads to threads (entry 28) broke a limit that only a concurrency test could see; performance and concurrency suites belong together.
- **Plausible is not correct.** A price for the wrong region (40), a defect that was a screenshot artefact (34) and a dependency conflict hidden behind a one-line upgrade (35) all looked right until measured.
- **Prefer leaving data out to inventing it.** Missing office directions are better than made-up ones (37).
