#!/usr/bin/env python3
"""Unit tests for twin.py (no Ryu/Mininet needed):  python3 test_twin.py"""
import math
from twin import NetworkTwin, TelemetryScheduler

SRC = {(1, 1): (100, 0.2), (2, 2): (10, 1.0), (3, 2): (10, 1.0), (5, 2): (10, 1.0)}


def test_eps_growth_and_reset():
    tw = NetworkTwin(SRC, eps_meas=0.03, alpha=1.0, sigma=0.05, eps_max=1.0, t_start=0)
    k = (2, 2)
    assert tw.estimate(k)[1] == 1.0                      # cold start: eps_max
    tw.update(k, 0, 0.0)                                 # first reading: counter only
    assert tw.estimate(k)[1] == 1.0
    tw.update(k, 625000, 1.0)                            # 5 Mbit / 1 s = 5 Mbps on 10M
    x, e = tw.estimate(k)
    assert abs(x - 0.5) < 1e-9 and e == 0.03
    tw.tick(1.0)                                         # refreshed this slot -> no growth
    assert tw.estimate(k)[1] == 0.03
    tw.tick(1.5)
    assert abs(tw.estimate(k)[1] - 0.08) < 1e-9          # grows by sigma
    for _ in range(100):
        tw.tick(0)
    assert tw.estimate(k)[1] == 1.0                      # capped at eps_max


def test_bounds_and_fidelity():
    tw = NetworkTwin(SRC, eps_meas=0.1, sigma=0.0, eps_max=0.5)
    k = (2, 2)
    tw.update(k, 0, 0); tw.update(k, 1250000, 1.0)       # 10 Mbps -> x=1.0
    lo, hi = tw.bounds(k)
    assert abs(lo - 0.9) < 1e-9 and abs(hi - 1.1) < 1e-9
    assert 0 < tw.fidelity() < 1
    assert abs(tw.fidelity() - math.exp(-tw.total_uncertainty())) < 1e-12


def test_age():
    tw = NetworkTwin(SRC, t_start=100.0)
    assert tw.age((2, 2), 105.0) == 5.0
    tw.update((2, 2), 0, 106.0); tw.update((2, 2), 10, 107.0)
    assert abs(tw.age((2, 2), 108.0) - 1.0) < 1e-9


def test_schedulers_respect_budget():
    tw = NetworkTwin(SRC)
    assert len(TelemetryScheduler('all', 1).select(tw)) == 4
    rr = TelemetryScheduler('roundrobin', 2)
    seen = []
    for _ in range(2):
        s = rr.select(tw); assert len(s) == 2; seen += s
    assert sorted(seen) == sorted(SRC)                   # full cycle covers everyone
    un = TelemetryScheduler('uncertainty', 2)
    assert (1, 1) not in un.select(tw)                   # low-weight core port loses to agg


def test_uncertainty_prefers_stale_important():
    tw = NetworkTwin(SRC, eps_meas=0.01, sigma=0.05, eps_max=1.0)
    for k in SRC:
        tw.update(k, 0, 0.0); tw.update(k, 0, 1.0)       # all fresh
    tw.tick(1.0)
    for _ in range(5):
        tw.update((3, 2), 0, 2.0)                        # keep (3,2) fresh...
        tw.tick(2.0)
    assert TelemetryScheduler('uncertainty', 1).select(tw)[0] in [(2, 2), (5, 2)]


if __name__ == '__main__':
    for n, f in list(globals().items()):
        if n.startswith('test_'):
            f(); print('PASS', n)
