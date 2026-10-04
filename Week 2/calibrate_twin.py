#!/usr/bin/env python3
"""
Fit the twin's growth parameters from a ground-truth log.

  python3 calibrate_twin.py logs/twin_all_B12 [--slot 0.5] [--q 0.95]

For lags k = 1..K slots, takes the q-quantile of |u(t+k*slot) - u(t)| on the
aggregation links (how far truth can drift in k slots without a refresh) and
fits  eps_k ~= e0 + sigma * k  (alpha = 1).  Prints suggested env settings:
    EPS_MEAS = q-quantile of 1-sample jitter, TWIN_SIGMA = fitted slope.
Tip: if bursts are rare, use --q 0.99 so transitions are covered.
"""
import argparse

from gt_utils import AGG, load_gt, quantile, truth_util

ap = argparse.ArgumentParser()
ap.add_argument('dir')
ap.add_argument('--slot', type=float, default=0.5)
ap.add_argument('--q', type=float, default=0.95)
ap.add_argument('--K', type=int, default=10)
a = ap.parse_args()

gt = load_gt(a.dir)
series = {}
for key in sorted(AGG):
    ts = gt[key][0]
    t, s = ts[0] + a.slot, []
    while t <= ts[-1]:
        u = truth_util(gt, key, t, a.slot)
        if u is not None:
            s.append(u)
        t += a.slot
    series[key] = s

jit = []
for key in AGG:
    ts = gt[key][0]
    step = ts[1] - ts[0]
    u = [truth_util(gt, key, t, a.slot) for t in ts]
    u = [x for x in u if x is not None]
    jit += [abs(u[i + 1] - u[i]) for i in range(len(u) - 1)]
eps0 = quantile(jit, a.q)

ks, es = [], []
print('lag(k)  slot-lag(s)  q%.2f |du|' % a.q)
for k in range(1, a.K + 1):
    d = []
    for s in series.values():
        d += [abs(s[i + k] - s[i]) for i in range(len(s) - k)]
    e = quantile(d, a.q)
    ks.append(k); es.append(e)
    print('%4d    %6.2f       %.3f' % (k, k * a.slot, e))

n = len(ks)
mk, me = sum(ks) / n, sum(es) / n
sigma = sum((k - mk) * (e - me) for k, e in zip(ks, es)) / sum((k - mk) ** 2 for k in ks)
sigma = max(sigma, 0.0)
print('\nfit: eps_k ~= %.3f + %.3f * k' % (me - sigma * mk, sigma))
print('\nSuggested:  EPS_MEAS=%.3f  TWIN_SIGMA=%.3f  TWIN_ALPHA=1.0  (slot=%.2fs)' %
      (eps0, sigma, a.slot))
