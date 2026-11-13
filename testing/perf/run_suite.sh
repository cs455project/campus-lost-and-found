#!/usr/bin/env bash
# One full measurement. Every step starts on a fresh database and a freshly
# started API, so one overloaded step can't spoil the next.
#   ramp   - 1 to 100 users with no pause between pages (finds the limit)
#   campus - 50 to 400 users who pause about 5 s between pages (normal use)
#   spike  - 50 users at once for 30 s, then: how long until the API answers again?
# Usage: bash run_suite.sh <name> [ramp|all] [extra loadgen options, e.g. --thumbs]
cd "$(dirname "$0")"
fresh() { bash reset.sh > /dev/null 2>&1; sleep 3; }
NAME="$1"; WHICH="${2:-all}"; shift 2 2>/dev/null || shift; EXTRA="$*"
set -- "$NAME"
rm -f "results/$1_ramp.json" "results/$1_campus.json" "results/$1_spike.json"
for n in 1 5 10 25 50 100; do
  fresh; python3 loadgen.py --name "$1" --test ramp --users $n --duration 30 --warmup 5 --pause 0 --append $EXTRA
done
[ "$WHICH" = ramp ] && exit 0
for n in 50 100 200 400; do
  fresh; python3 loadgen.py --name "$1" --test campus --users $n --think 5 --duration 45 --warmup 10 --pause 0 --append $EXTRA
done
fresh; python3 loadgen.py --name "$1" --test spike --users 50 --duration 30 --warmup 0 --pause 0 --recovery $EXTRA
