#!/usr/bin/env python3
"""
Usage: python3 analyze.py logs/static logs/baseline
Prints a comparison table and writes <dir>/timeline.png and <dir>/summary.json.
Needs: python3-matplotlib
"""
import csv
import json
import os
import re
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import topo_config as T

PING_RX = re.compile(r'\[(\d+\.\d+)\].*icmp_seq=(\d+).*time=([\d.]+)\s*ms')


def pct(v, p):
    if not v:
        return float('nan')
    s = sorted(v)
    return s[min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1))))]


def load_ping(d):
    out = []
    with open(os.path.join(d, 'urllc_ping.txt')) as f:
        for line in f:
            m = PING_RX.search(line)
            if m:
                out.append((float(m.group(1)), int(m.group(2)), float(m.group(3))))
    return out


def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def analyze(d):
    meta = json.load(open(os.path.join(d, 'meta.json')))
    ping = load_ping(d)
    events = load_csv(os.path.join(d, 'events.csv'))
    stats = load_csv(os.path.join(d, 'link_stats.csv'))
    t0, bs, be = meta['t0'], meta['t_bulk_start'], meta['t_bulk_end']
    lmax = meta['lmax_rtt_ms']
    sent = meta['n_pings']
    rtts = [p[2] for p in ping]
    lost = max(0, sent - len(ping))
    viol = sum(1 for r in rtts if r > lmax) + lost
    reroutes = [(float(e['ts']), e['detail']) for e in events if e['event'] == 'reroute']
    first_alt = [t for t, det in reroutes if t >= bs and 'to=alt' in det]
    fm = 0
    for _, det in reroutes:
        m = re.search(r'flow_mods=(\d+)', det)
        fm += int(m.group(1)) if m else 0
    bulk_rtts = [p[2] for p in ping if bs <= p[0] <= be]

    s = dict(
        label=meta['label'], pings_sent=sent, pings_lost=lost,
        loss_pct=round(100.0 * lost / sent, 2),
        rtt_mean_ms=round(sum(rtts) / len(rtts), 1) if rtts else None,
        rtt_p95_ms=round(pct(rtts, 95), 1), rtt_p99_ms=round(pct(rtts, 99), 1),
        rtt_max_ms=round(max(rtts), 1) if rtts else None,
        rtt_mean_during_bulk_ms=round(sum(bulk_rtts) / len(bulk_rtts), 1) if bulk_rtts else None,
        sla_violations=viol, sla_violation_pct=round(100.0 * viol / sent, 2),
        sla_violation_seconds=round(viol * meta['ping_interval'], 1),
        reroutes=len(reroutes), flow_mods=fm,
        detect_to_reroute_s=round(min(first_alt) - bs, 2) if first_alt else None,
        stats_samples=len(stats))
    json.dump(s, open(os.path.join(d, 'summary.json'), 'w'), indent=2)

    # ---- plot ----
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    a1.plot([p[0] - t0 for p in ping], rtts, '.', ms=3)
    a1.axhline(lmax, ls='--', c='r', label='L_max = %.0f ms' % lmax)
    a1.axvspan(bs - t0, be - t0, alpha=0.15, label='bulk flow ON')
    for i, (t, _) in enumerate(reroutes):
        a1.axvline(t - t0, c='g', label='reroute' if i == 0 else None)
    a1.set_ylabel('URLLC RTT (ms)')
    a1.set_title('Run: %s' % meta['label'])
    a1.legend(loc='upper right')
    for name, keys in T.AGG_PORTS.items():
        for dpid, port in keys:
            pts = [(float(r['ts']) - t0, float(r['util'])) for r in stats
                   if int(r['dpid']) == dpid and int(r['port']) == port]
            if pts:
                a2.plot(*zip(*pts), label='%s (dpid %d, port %d)' % (name, dpid, port))
    a2.axhline(1.0, c='gray', ls=':')
    a2.axvspan(bs - t0, be - t0, alpha=0.15)
    a2.set_ylabel('link utilisation (polled)')
    a2.set_xlabel('time since probe start (s)')
    a2.legend(loc='upper right', fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(d, 'timeline.png'), dpi=130)
    plt.close(fig)
    return s


if __name__ == '__main__':
    rows = [analyze(d) for d in sys.argv[1:]]
    if not rows:
        sys.exit(__doc__)
    keys = list(rows[0].keys())
    print('%-26s' % 'metric' + ''.join('%-16s' % r['label'] for r in rows))
    for k in keys[1:]:
        print('%-26s' % k + ''.join('%-16s' % r[k] for r in rows))
