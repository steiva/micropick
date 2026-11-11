#!/bin/sh
# Test suite audit: run the suite many times, one junit file per run.
#
#   scripts/audit/run_many.sh OUT [PLUGINS]
#
# OUT      a folder for the junit files and logs (outside the repository).
# PLUGINS  a folder with pytest-randomly and pytest-timeout installed with
#          `pip install --target`, so the `lab` env itself is left alone.
#          Without it the random-order runs are skipped.
#
# Mock fixtures only: nothing here touches the robot or a camera.
# Summarize with scripts/audit/junit_summary.py OUT.
set -u
OUT=${1:?output folder}
PLUGINS=${2:-}
PY="conda run --no-capture-output -n lab python"
mkdir -p "$OUT"

run() {  # name, then pytest arguments
    name=$1; shift
    start=$(date +%s)
    $PY -m pytest -q -p no:cacheprovider --junitxml="$OUT/$name.xml" "$@" \
        > "$OUT/$name.log" 2>&1
    echo "$name exit=$? wall=$(( $(date +%s) - start ))s" >> "$OUT/times.txt"
}

for i in 2 3 4 5 6; do run "par$i" -n auto tests; done
run ser1 --durations=120 --durations-min=0.05 tests
run ser2 tests
if [ -n "$PLUGINS" ]; then
    export PYTHONPATH="$PLUGINS"
    for seed in 1 2 3; do
        run "rand_gui_ser_$seed" --randomly-seed=$seed --timeout=600 tests/gui
    done
    for seed in 11 12; do
        run "rand_all_par_$seed" --randomly-seed=$seed --timeout=600 \
            -n auto tests
    done
fi
