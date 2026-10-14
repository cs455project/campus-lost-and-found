# Verification score (US-22, SCRUM-51)

    C = 50·M + 20·R + 30·H        (0 to 100)

The score helps guards decide **which claim to look at first**. It never
approves, rejects or hands over anything; a guard does that after checking
the item and the student ID card (BR-04, FR-SCORE-04).

Code: `app/services/verification_score.py`. Tests: `tests/test_verification_score.py`.

## The three parts

| Part | Meaning | How it is worked out | Range |
|---|---|---|---|
| **M** | Identifying details matched | details a guard marked **Matches** ÷ all details on the claim. Unchecked details count as not matched. No details gives 0. | 0 to 1 |
| **R** | Report recency | `1 − d / 30`, floored at 0, where `d` = days from the date the item was found to the date the claim was sent (campus time). Same day gives 1; 15 days gives 0.5; 30 or more gives 0. | 0 to 1 |
| **H** | History of verified returns | `min(n, 3) / 3`, where `n` = found items the claimant handed in that a guard later returned to their verified owner. | 0 to 1 |

Weights 50/20/30 come from the D1 SRS (FR-SCORE-01). They are fixed constants
in code, not settings, so no configuration, prompt or model can change them
(FR-SCORE-03).

### Why these definitions

The SRS gives the formula but not exact definitions of M, R and H. These are
the team's choices, made so each part is measurable from data the system
already has and cannot be gamed by the claimant:

- **M** needs a human check. The claimant lists up to 5 separate details on
  the claim form (FR-CLM-02); the guard compares each with the item they hold.
  Guards can also record details the claimant explains in person
  (`source = DESK`).
- **R** rewards claiming soon after the find: real owners usually notice the
  loss quickly. The 30-day window matches how long most items wait at the
  office before being disposed of.
- **H** rewards a record of honest behaviour (handing in found items) rather
  than a record of claiming, because counting past claims would reward people
  who claim often. Walk-in items are reported by the guard, so they never
  count. The count is stored on the user (`users.verified_returns`) because
  returned records are purged after 60 days.

If the team prefers different definitions, only `recency_factor`,
`history_factor` and the two constants need to change; the tests show what
each is expected to return.

## When it is computed

| Event | What happens |
|---|---|
| Claim sent | R and H are computed and **frozen**; M = 0 (nothing checked yet). |
| Guard marks a detail Matches / Doesn't match / clears it | M is recomputed; C updates. Claim status does **not** change. |
| Guard records a detail told in person | M is recomputed. |
| Item handed over | The finder's `verified_returns` goes up by 1 (atomic SQL `+ 1`). |

Factors are stored rounded to 4 decimals and C is computed from the stored
factors, so `50·score_m + 20·score_r + 30·score_h` reproduces `score` exactly
from the database row.

Claims created before this feature have no score. Give them one with:

    python -m app.cli rescore-claims

## Who sees it

- **Staff:** the claim page shows C with the M/R/H breakdown; the Claims desk
  shows it on each row and can sort by it (`GET /staff/claims?sort=score`).
- **Claimants:** never. They see their own details, but not the guard's
  verdicts or the score (FR-CLM-12, NFR-PRIV-02/04). Other claimants cannot
  open the claim at all (404).

## Independence from AI matching

`verification_score.py` imports nothing from `services/matching.py`, and the
matcher imports nothing from claims or the score. A test parses both files'
imports to keep it that way (FR-SCORE-02, FR-AI-11..13).
