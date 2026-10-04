#!/usr/bin/env python3
"""
Week-2 analysis: twin estimate vs ground truth.

  python3 analyze_twin.py logs/twin_roundrobin_B2 [more run dirs ...]
  options: --window 0.5 (truth averaging window, = slot)  --skip 5 (s after probe start)

Per run  -> <dir>/twin_vs_truth.png, <dir>/twin_summary.json
Many runs-> logs/twin_compare.png (error / coverage vs. telemetry cost)
Metrics (aggregation ports unless noted):
  mae_agg, max_err_agg   |xhat - truth|
  coverage_agg           fraction of time truth lies inside [xhat-eps, xhat+eps]
  mean_eps_agg, mean_aoi_agg_s, fidelity_mean (paper eq. 6)
  polls_per_s            telemetry overhead
  detect_lag_s           burst start -> twin first shows primary-agg util > 0.8
"""
import argparse
import json
import math
import os
import re

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import topo_config as T
from gt_utils import load_csv, load_gt, truth_util, AGG

HIGH = 0.8


def analyze(d, window, skip):
    meta = json.load(open(os.path.join(d, 'meta.json')))
    gt = load_gt(d)
    twin = load_csv(os.path.join(d, 'twin_state.csv'))
    polls = load_csv(os.path.join(d, 'polls.csv'))
    events = load_csv(os.path.join(d, 'events.csv'))
    t0 = meta['t0']
    t_end = t0 + meta['n_pings'] * meta['ping_interval']
    tmin = t0 + skip
    bursts = meta.get('bursts') or [[meta['t_bulk_start'], meta['t_bulk_end']]]

    start = next((e['detail'] for e in events if e['event'] == 'start'), '')
    m = re.search(r'policy=(\w+) budget=(\d+)', start)
    policy, budget = (m.group(1), int(m.group(2))) if m else ('?', -1)

    by_key, per_slot = {}, {}
    err_all, err_agg, cov_all, cov_agg, eps_agg, aoi_agg = [], [], [], [], [], []
    for r in twin:
        ts = float(r['ts'])
        key = (int(r['dpid']), int(r['port']))
        x, eps, aoi = float(r['x_hat']), float(r['eps']), float(r['aoi'])
        row = dict(ts=ts, x=x, eps=eps, aoi=aoi, pol=int(r['polled']), w=float(r['weight']))
        by_key.setdefault(key, []).append(row)
        per_slot.setdefault(ts, 0.0)
        per_slot[ts] += row['w'] * eps
        if ts < tmin or ts > t_end:
            continue
        tr = truth_util(gt, key, ts, window)
        if tr is None:
            continue
        e = abs(x - tr)
        c = 1 if e <= eps else 0
        err_all.append(e)
        cov_all.append(c)
        if key in AGG:
            err_agg.append(e)
            cov_agg.append(c)
            eps_agg.append(eps)
            aoi_agg.append(aoi)

    fid = [math.exp(-v) for ts, v in per_slot.items() if tmin <= ts <= t_end]
    n_polls = sum(1 for p in polls if tmin <= float(p['ts']) <= t_end)
    dur = max(1e-9, t_end - tmin)

    # detection lag of congestion on the primary aggregation link as seen by the twin
    lags = []
    prim = by_key.get(T.AGG_PORTS['primary'][0], [])
    for b0, _b1 in bursts:
        hit = next((r['ts'] for r in prim if r['ts'] >= b0 and r['x'] > HIGH), None)
        lags.append(hit - b0 if hit is not None else None)
    seen = [l for l in lags if l is not None]

    mean = lambda v: round(sum(v) / len(v), 4) if v else None
    s = dict(label=meta['label'], policy=policy, budget=budget,
             mae_all=mean(err_all), mae_agg=mean(err_agg),
             max_err_agg=round(max(err_agg), 3) if err_agg else None,
             coverage_all=mean(cov_all), coverage_agg=mean(cov_agg),
             mean_eps_agg=mean(eps_agg), mean_aoi_agg_s=mean(aoi_agg),
             fidelity_mean=mean(fid), polls=n_polls, polls_per_s=round(n_polls / dur, 2),
             detect_lag_s=round(sum(seen) / len(seen), 2) if seen else None,
             bursts_missed=len(lags) - len(seen))
    json.dump(s, open(os.path.join(d, 'twin_summary.json'), 'w'), indent=2)

    # ---------------- plot ----------------
    fig, axs = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    for ax, (name, keys) in zip(axs[:2], [('primary agg', T.AGG_PORTS['primary'][0]),
                                          ('alternate agg', T.AGG_PORTS['alt'][0])]):
        rows = by_key[keys]
        gts, _ = gt[keys]
        tt, uu = [], []
        for t in gts:
            u = truth_util(gt, keys, t, window)
            if u is not None:
                tt.append(t - t0)
                uu.append(u)
        xs = [r['ts'] - t0 for r in rows]
        ax.plot(tt, uu, c='k', lw=1, label='ground truth (%.1fs window)' % window)
        ax.step(xs, [r['x'] for r in rows], where='post', c='C0', label='twin estimate')
        ax.fill_between(xs, [max(0, r['x'] - r['eps']) for r in rows],
                        [min(1.3, r['x'] + r['eps']) for r in rows],
                        step='post', color='C0', alpha=0.18, label='uncertainty +/- eps')
        px = [r['ts'] - t0 for r in rows if r['pol']]
        ax.plot(px, [-0.04] * len(px), 'v', c='C3', ms=4, label='telemetry refresh')
        for b0, b1 in bursts:
            ax.axvspan(b0 - t0, b1 - t0, color='orange', alpha=0.12)
        ax.set_ylim(-0.08, 1.35)
        ax.set_ylabel('utilisation')
        ax.set_title('%s  (dpid %d, port %d)' % (name, keys[0], keys[1]), fontsize=10)
        ax.legend(loc='upper right', fontsize=7, ncol=2)
    for key in sorted(AGG):
        rows = by_key[key]
        axs[2].plot([r['ts'] - t0 for r in rows], [r['eps'] for r in rows],
                    label='eps dpid%d:p%d' % key)
    for b0, b1 in bursts:
        axs[2].axvspan(b0 - t0, b1 - t0, color='orange', alpha=0.12)
    axs[2].set_ylabel('uncertainty eps')
    axs[2].set_xlabel('time since probe start (s)')
    axs[2].legend(loc='upper right', fontsize=7, ncol=2)
    fig.suptitle('%s | policy=%s budget=%d | MAE(agg)=%s coverage(agg)=%s polls/s=%s' %
                 (meta['label'], policy, budget, s['mae_agg'], s['coverage_agg'], s['polls_per_s']),
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(d, 'twin_vs_truth.png'), dpi=130)
    plt.close(fig)
    return s


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('dirs', nargs='+')
    ap.add_argument('--window', type=float, default=0.5)
    ap.add_argument('--skip', type=float, default=5.0)
    a = ap.parse_args()
    rows = [analyze(d, a.window, a.skip) for d in a.dirs]

    keys = [k for k in rows[0] if k != 'label']
    print('%-18s' % 'metric' + ''.join('%-22s' % r['label'][:21] for r in rows))
    for k in keys:
        print('%-18s' % k + ''.join('%-22s' % r[k] for r in rows))

    if len(rows) > 1:
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.5))
        for r in rows:
            mk = {'all': 's', 'roundrobin': 'o', 'uncertainty': '^'}.get(r['policy'], 'x')
            col = {'all': 'k', 'roundrobin': 'C1', 'uncertainty': 'C2'}.get(r['policy'], 'C7')
            a1.scatter(r['polls_per_s'], r['mae_agg'], marker=mk, c=col, s=60)
            a1.annotate('B%d' % r['budget'], (r['polls_per_s'], r['mae_agg']), fontsize=8,
                        xytext=(4, 4), textcoords='offset points')
            a2.scatter(r['polls_per_s'], r['coverage_agg'], marker=mk, c=col, s=60,
                       label=r['policy'])
        a1.set_xlabel('telemetry cost (polls / s)'); a1.set_ylabel('MAE on aggregation links')
        a2.set_xlabel('telemetry cost (polls / s)'); a2.set_ylabel('coverage of [xhat +/- eps]')
        h, l = a2.get_legend_handles_labels()
        uniq = dict(zip(l, h))
        a2.legend(uniq.values(), uniq.keys())
        fig.tight_layout()
        out = os.path.join(os.path.dirname(os.path.normpath(a.dirs[0])) or '.', 'twin_compare.png')
        fig.savefig(out, dpi=130)
        print('\nwrote', out)
