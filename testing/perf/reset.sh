#!/usr/bin/env bash
# Fresh perf database + stack + seeded data. Same starting point for every run.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
bash "$HERE/stop_stack.sh"
rm -f "$HERE/../../backend/uploads/items/"lt*.jpg "$HERE/../../backend/uploads/thumbs/"lt*.webp
PSQL="${PSQL:-psql -h /tmp -U postgres}" TEST_DB=lostfound_perf bash "$HERE/../start_stack.sh" | tail -1
cd "$HERE/../../backend" && . .venv/bin/activate && DB_NAME=lostfound_perf python "$HERE/seed_load.py"
