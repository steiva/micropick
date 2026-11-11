#!/bin/sh
# Test suite audit: a flaky test's failure rate, under load.
#
#   scripts/audit/stress.sh REPEAT HOGS WORKERS pytest-targets...
#
# Runs each target test REPEAT times over WORKERS xdist workers while HOGS
# processes burn a core each (0 for none), with scripts/audit/hang_dump.py
# adding the robot jobs' state to every failure. Mock fixtures only.
set -u
REPEAT=$1; HOGS=$2; WORKERS=$3; shift 3
HERE=$(cd "$(dirname "$0")" && pwd)
pids=""
i=0
while [ "$i" -lt "$HOGS" ]; do
    ( while :; do :; done ) &
    pids="$pids $!"
    i=$((i + 1))
done
trap 'kill $pids 2>/dev/null' EXIT INT TERM
PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" conda run --no-capture-output -n lab \
    python -m pytest -q -p no:cacheprovider -p repeat_plugin -p hang_dump \
    --audit-repeat "$REPEAT" -n "$WORKERS" "$@"
