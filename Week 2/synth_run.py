#!/usr/bin/env python3
"""
Generate a SYNTHETIC run directory (ground_truth.csv, twin_state.csv, polls.csv,
events.csv, meta.json) using the real twin.py + scheduler, with no Mininet/Ryu.
Lets you test analyze_twin.py / calibrate_twin.py on your laptop or VM first.

  python3 tools/synth_run.py --out logs/synth_rr_B2 --policy roundrobin --budget 2
"""
import argparse
import csv
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import topo_config as T
from gt_utils import CAP, AGG, bytes_at
from twin import NetworkTwin, TelemetryScheduler

ap = argparse.ArgumentParser()
ap.add_argument('--out', required=True)
ap.add_argument('--policy', default='roundrobin')
ap.add_argument('--budget', type=int, default=2)
ap.add_argument('--slot', type=float, default=0.5)
ap.add_argument('--seed', type=int, default=1)
a = ap.parse_args()
random.seed(a.seed)
os.makedirs(a.out, exist_ok=True)

t0, warmup, bulk, cool = 1000.0, 10.0, 45.0, 10.0
total = warmup + bulk + cool
bs = t0 + warmup
bursts = [[bs + 15 * i, bs + 15 * i + 8] for i in range(3)]
T_START, T_END, GT_DT = t0 - 6.0, t0 + total, 0.1


def util(key, t):
    on = any(b0 <= t < b1 for b0, b1 in bursts)
    n = random.uniform(-0.01, 0.01)
    if key == (2, 2):
        return max(0, (0.97 if on else 0.002) + n)
    if key == (1, 1):
        return max(0, (0.095 if on else 0.001) + n / 10)
    return max(0, 0.002 + n / 20)


keys = sorted(CAP)
cum = {k: 0.0 for k in keys}
series = {k: ([], []) for k in keys}
with open(os.path.join(a.out, 'ground_truth.csv'), 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['ts', 'dpid', 'port', 'tx_bytes'])
    t = T_START
    while t <= T_END + 1e-9:
        for k in keys:
            cum[k] += util(k, t) * CAP[k] * 1e6 / 8 * GT_DT
            series[k][0].append(t); series[k][1].append(int(cum[k]))
            w.writerow(['%.3f' % t, k[0], k[1], int(cum[k])])
        t += GT_DT

sources = {k: (CAP[k], 1.0 if k in AGG else 0.2) for k in keys}
twin = NetworkTwin(sources, 0.03, 1.0, 0.04, 1.0, T_START)
sched = TelemetryScheduler(a.policy, a.budget)
ft = open(os.path.join(a.out, 'twin_state.csv'), 'w', newline='')
fp = open(os.path.join(a.out, 'polls.csv'), 'w', newline='')
wt, wp = csv.writer(ft), csv.writer(fp)
wt.writerow(['ts', 'dpid', 'port', 'x_hat', 'eps', 'aoi', 'polled', 'weight'])
wp.writerow(['ts', 'dpid', 'port'])
t = T_START + 1.0
pending = []
while t <= T_END:
    for key, tp in pending:                        # replies from previous slot
        twin.update(key, bytes_at(series[key], tp), tp)
    pending = []
    refreshed = twin.tick(t)
    for (dp, pt), x, e, ag, pol, wgt in twin.rows(t, refreshed):
        wt.writerow(['%.3f' % t, dp, pt, '%.4f' % x, '%.4f' % e, '%.3f' % ag, pol, wgt])
    for key in sched.select(twin):
        tp = t + random.uniform(0.01, 0.05)
        wp.writerow(['%.3f' % tp, key[0], key[1]])
        pending.append((key, tp))
    t += a.slot
ft.close(); fp.close()

with open(os.path.join(a.out, 'events.csv'), 'w') as f:
    f.write('ts,event,detail\n%.3f,start,policy=%s budget=%d slot=%.2f synthetic\n' %
            (T_START, a.policy, a.budget, a.slot))
json.dump(dict(t0=t0, t_bulk_start=bs, t_bulk_end=bursts[-1][1], bursts=bursts,
               n_pings=int(total / 0.1), ping_interval=0.1, lmax_rtt_ms=30,
               label=os.path.basename(a.out.rstrip('/'))),
          open(os.path.join(a.out, 'meta.json'), 'w'), indent=2)
print('wrote', a.out)
