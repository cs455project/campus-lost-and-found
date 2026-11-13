#!/usr/bin/env bash
# Stop the backend, worker and website that start_stack.sh started (by PID,
# so this script never matches its own command line). An overloaded API may
# never finish shutting down politely, so anything left after 5 s is killed.
pids() { ps -eo pid,args | awk '/uvicorn app.main:app|app\.worker|next start|next-server/ && !/awk/ {print $1}'; }
for pid in $(pids); do kill "$pid" 2>/dev/null; done
for i in 1 2 3 4 5; do [ -z "$(pids)" ] && break; sleep 1; done
for pid in $(pids); do kill -9 "$pid" 2>/dev/null; done
# Close any database sessions the killed processes left behind.
${PSQL:-psql -h /tmp -U postgres} -q -t -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'lostfound_perf' AND pid <> pg_backend_pid();" > /dev/null
sleep 1
