#!/usr/bin/env bash
# Called by upload.ps1 on the server. Starts deploy.sh in the background, so
# the build keeps going even if the SSH connection drops, and shows its output.
# If you get disconnected, reconnect and watch it with:  tail -f ~/deploy.log
set -u
rm -f ~/deploy.status
: > ~/deploy.log
setsid nohup bash -c 'bash ~/lost-found/deploy/deploy.sh > ~/deploy.log 2>&1; echo $? > ~/deploy.status' \
  > /dev/null 2>&1 < /dev/null &

tail -n +1 -f ~/deploy.log &
TAIL=$!
while [ ! -f ~/deploy.status ]; do sleep 3; done
sleep 1
kill "$TAIL" 2>/dev/null
exit "$(cat ~/deploy.status)"
