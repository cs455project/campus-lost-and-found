# TraceBack — Timeline and Gantt Chart

Nov 6, 2026 · @Soumyaranjan

**TraceBack went from proposal to a tested, deployed system in 11 weeks (19 Aug to 6 Nov 2026): planning and requirements through September, the whole system built and merged in a four-day implementation sprint (12–15 Oct), deployment to AWS on 3 Nov, and testing, hardening and the final reports on 5–6 Nov.** Every date below comes from Jira, the git history, file and server timestamps, or the test logs.

## Gantt chart

&#91;embedded content: Jira sprints (Deliverable 2 report, section 8), git history of cs455project/campus-lost-and-found and branch d3/integration, server and test logs\]

## Phases and milestones

| Phase | Dates | Owner | Output | Evidence |
| --- | --- | --- | --- | --- |
| Sprint 0: proposal | 19–29 Aug | Whole team | Deliverable 0, the project proposal, submitted 29 Aug | `Deliverable_0/` in the repository (commit of 29 Aug) |
| Sprint 0: requirements | 19 Aug – 2 Sep | Whole team | Deliverable 1: requirements, user stories, literature review; Sprint 0 closed with 4 of 4 items done | Jira sprint report (D2 report, figure 26) |
| Backlog and Sprint 1 plan | 3 Sep – 11 Oct | Whole team, led by Rohit Yadav | 28 user stories with epics, estimates and assignees; Sprint 1 planned with 10 foundation items (40 story points) | Jira backlog (D2 report, figure 25) |
| Implementation sprint | 12–15 Oct | All six members, one module each | Schema and sign-in (12 Oct), reports, custody and claims (13 Oct), the AI layer (14 Oct), 18 story pull requests (15 Oct) | 13 database migrations dated 12–14 Oct; PRs #11–#28 |
| Reviews and merge | 15–16 Oct | Every member reviewed 6 PRs; Rohit Yadav merged | All 18 PRs merged into `main`, 0 conflicts, 189 tests passing | Merge commits on `main`, 15 Oct 22:12 to 16 Oct 02:24 |
| Deliverable 2 | due 15 Oct | Whole team | Report, API documentation, OpenAPI file, AI Engineering Log (26 entries) | `Deliverable_2/` |
| AWS deployment | 30 Oct – 4 Nov | Soumyaranjan Sahu | Docker Compose and Caddy setup, one-command deploy script; site live over HTTPS on 3 Nov; real email checked on 4 Nov | `deploy/` folder; server deploy log |
| Deliverable 3 testing | 5–6 Nov | Soumyaranjan Sahu | Unit, integration, system, adversarial, concurrency, load and failure tests; 270 new automated tests | `testing/results/` (JUnit file of 6 Nov) |
| Fixes and re-tests | 5–6 Nov | Soumyaranjan Sahu | 25 defects fixed, every suite green on the final code | 14 commits on `d3/integration` |
| Deliverable 3 reports | 6 Nov | Soumyaranjan Sahu | The reports in this document | This document |

## What the schedule shows

- **Planning paid off.** Stories were split by module before coding started, so six people built and merged 18 pull requests in four days with no merge conflicts.
- **AI assistance compressed the build.** About 13,300 lines of product code would take roughly 36 person-months by a classic estimate (Costing report); the team did it in one sprint with an AI coding assistant, checking and correcting its output (AI Engineering Log).
- **Testing came late and found real problems.** The load, concurrency and failure tests in November found 25 defects, including one critical; all were fixed before the reports. For future work, running these suites from the first sprint would catch such problems earlier.
