#!/usr/bin/env bash
# Full Week-2 matrix (~10-12 min). Produces logs/twin_*/ and logs/twin_compare.png
set -euo pipefail
./run_twin.sh all 12
for B in 1 2 4; do
  ./run_twin.sh roundrobin  $B
  ./run_twin.sh uncertainty $B
done
python3 analyze_twin.py logs/twin_all_B12 logs/twin_roundrobin_B1 logs/twin_uncertainty_B1 \
  logs/twin_roundrobin_B2 logs/twin_uncertainty_B2 logs/twin_roundrobin_B4 logs/twin_uncertainty_B4
