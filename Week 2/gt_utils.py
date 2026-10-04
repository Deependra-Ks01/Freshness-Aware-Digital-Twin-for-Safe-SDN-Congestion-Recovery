"""Helpers for the independent ground-truth link counters (ground_truth.csv)."""
import bisect
import csv
import os

import topo_config as T

CAP = {}
for _a, _pa, _b, _pb, _bw, _d, _r in T.LINKS:
    CAP[(_a, _pa)] = _bw
    CAP[(_b, _pb)] = _bw
AGG = {k for ks in T.AGG_PORTS.values() for k in ks}


def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def load_gt(d):
    gt = {}
    for r in load_csv(os.path.join(d, 'ground_truth.csv')):
        k = (int(r['dpid']), int(r['port']))
        ts, bs = gt.setdefault(k, ([], []))
        ts.append(float(r['ts']))
        bs.append(int(r['tx_bytes']))
    return gt


def bytes_at(series, t):
    ts, bs = series
    if t < ts[0] or t > ts[-1]:
        return None
    i = bisect.bisect_left(ts, t)
    if ts[i] == t or i == 0:
        return bs[i]
    t0, t1 = ts[i - 1], ts[i]
    return bs[i - 1] + (bs[i] - bs[i - 1]) * (t - t0) / (t1 - t0)


def truth_util(gt, key, t, window):
    """True utilisation of port `key` averaged over (t-window, t]."""
    if key not in gt:
        return None
    b1, b0 = bytes_at(gt[key], t), bytes_at(gt[key], t - window)
    if b1 is None or b0 is None:
        return None
    return (b1 - b0) * 8.0 / window / 1e6 / CAP[key]


def quantile(v, q):
    if not v:
        return float('nan')
    s = sorted(v)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]
