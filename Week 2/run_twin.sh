#!/usr/bin/env bash
# ./run_twin.sh <policy: all|roundrobin|uncertainty> <budget> [extra run_experiment args]
# Bursty scenario: 3 x (8 s ON / 7 s OFF) so the twin must track changing state.
set -euo pipefail
POLICY=${1:?usage: $0 <all|roundrobin|uncertainty> <budget>}
BUDGET=${2:?usage: $0 <all|roundrobin|uncertainty> <budget>}
shift 2
export POLL_POLICY=$POLICY TELEMETRY_BUDGET=$BUDGET APP=controller/twin_te.py
exec ./run_scenario.sh "twin_${POLICY}_B${BUDGET}" 1 \
     --bulk-time 45 --burst-on 8 --burst-off 7 --cooldown 10 "$@"
