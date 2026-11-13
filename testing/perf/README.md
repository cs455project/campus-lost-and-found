# Performance and load tests

Simulated students and guards use a copy of TraceBack with a year's worth of
data, while we measure response times, throughput, errors, CPU and memory.
The full write-up is the "Performance / Load Test" tab of the reports doc
(`testing/reports/TraceBack_Performance_Load_Test_Report.pdf`).

Never run these against the live site: they create thousands of requests a
second and add test data.

## Files

| File | What it does |
|---|---|
| `seed_load.py` | Fills the test database: 5,000 students, 5 guards, 10,000 items, 3,200 photos (each a 2.9 MB, 12-megapixel phone-size JPEG), 1,500 claims, and a signed-in session for every user |
| `loadgen.py` | The virtual users. Each one repeats page views like a person in the browser (Found page and its photos, search, item page, my claims, report a lost item with a photo, claim an item); guards open their staff pages |
| `reset.sh` / `stop_stack.sh` | Fresh database, fresh API, website and worker, freshly seeded data |
| `run_suite.sh <name>` | All three tests below, every step on a fresh start |
| `phone_photo.jpg` | The test photo |
| `results/` | One JSON file per test and stage, the run logs, the profiler output (`prof_baseline.txt`), and the functional and concurrency reruns after the changes (`after_*`) |
| `optimisations.patch` | Every code change, as a diff against the code before the optimisation |

## The three tests

| Test | Users | Pause between pages | Question it answers |
|---|---|---|---|
| ramp | 1, 5, 10, 25, 50, 100 | none | How much can the server do flat out, and where does it break? |
| campus | 50, 100, 200, 400 | about 5 s (random) | How many people browsing at the same moment does it serve well? |
| spike | 50 at once, 30 s | none | Does it come back by itself after a rush? |

## Running

Linux or macOS (WSL on Windows), with PostgreSQL running and the backend
virtualenv and frontend build from `testing/README.md`:

```bash
pip install aiohttp psutil
cd testing/perf
bash run_suite.sh baseline                     # the original code, about 25 minutes
# the measured stages (each after applying that stage's changes):
bash run_suite.sh s1_code ramp
THUMBS=1 bash run_suite.sh s2_thumbs ramp --thumbs
THUMBS=1 AI_WORKER_IN_API=false UVICORN_ARGS="--workers 2" bash run_suite.sh final all --thumbs
python3 summary.py                             # key numbers of every results file
```

`THUMBS=1` gives the seeded photos their small copies, as uploads after the
optimisation have. `UVICORN_ARGS` and `AI_WORKER_IN_API` set up the API the way
the updated `backend/Dockerfile` and `deploy/docker-compose.yml` run it.
`--thumbs` makes the lists ask for the small photo copies (`?size=thumb`),
as the website does after the optimisation.
