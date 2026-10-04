#!/usr/bin/env bash
# One-command reproducible run.
#   ./run_scenario.sh static   0      # no rerouting (shows the damage)
#   ./run_scenario.sh baseline 1      # baseline reactive reroute
# Extra args are passed to run_experiment.py, e.g.  --bulk-mbps 20 --bulk-proto tcp
set -euo pipefail
NAME=${1:?usage: $0 <name> <reroute 0|1> [run_experiment args]}
REROUTE=${2:?usage: $0 <name> <reroute 0|1> [run_experiment args]}
shift 2
VENV=${VENV:-$HOME/ryu-venv}
OUT=logs/$NAME
mkdir -p "$OUT"

sudo mn -c >/dev/null 2>&1 || true

# shellcheck disable=SC1091
source "$VENV/bin/activate"
PYTHONPATH=$PWD REROUTE=$REROUTE LOG_DIR=$OUT \
  ryu-manager controller/baseline_te.py > "$OUT/ryu.log" 2>&1 &
RYU_PID=$!
trap 'kill $RYU_PID 2>/dev/null || true; sudo mn -c >/dev/null 2>&1 || true' EXIT
sleep 4

# system python (has mininet), not the venv python
sudo /usr/bin/python3 run_experiment.py --mode experiment --out "$OUT" "$@"
echo "Done. Logs in $OUT"
