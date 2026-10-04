"""
twin.py -- selectively-synchronised network digital twin (paper Sec. IV-VI, XX).

Pure Python (no Ryu / Mininet imports) so it can be unit-tested and reused by
the Week-3 safety validator.

Telemetry source m  = one directed switch-to-switch port (dpid, port).
State x_m           = link utilisation in [0, ~1]  (tx rate / capacity).

Per-slot dynamics (paper eq. 2-4):
    refreshed in slot :  A_m = 0 (age measured from reply),  eps_m = eps_meas
    otherwise         :  A_m += slot,  eps_m = min(eps_max, alpha*eps_m + sigma)
Uncertainty set (eq. 5):  U = { x : |x_m - xhat_m| <= eps_m }
Fidelity (eq. 6):         F = exp(-sum_m w_m eps_m)
"""
import math


class Source:
    def __init__(self, key, cap_mbps, weight, eps_max, t_start):
        self.key = key
        self.cap = float(cap_mbps)
        self.weight = float(weight)
        self.x_hat = 0.0              # estimated utilisation
        self.eps = eps_max            # cold start: we know nothing
        self.last_update = t_start    # timestamp of last successful refresh
        self.prev_bytes = None        # last raw counter (to turn bytes -> rate)
        self.prev_ts = None
        self.refreshed = False        # refreshed since the previous tick?
        self.n_updates = 0


class NetworkTwin:
    def __init__(self, sources, eps_meas=0.03, alpha=1.0, sigma=0.04,
                 eps_max=1.0, t_start=0.0):
        """sources: dict key -> (capacity_mbps, importance_weight)"""
        self.eps_meas, self.alpha, self.sigma, self.eps_max = eps_meas, alpha, sigma, eps_max
        self.src = {k: Source(k, cap, w, eps_max, t_start) for k, (cap, w) in sources.items()}

    # ---- telemetry arrives --------------------------------------------------
    def update(self, key, tx_bytes, ts):
        """Feed one raw port counter reading. The first reading of a port only
        stores the counter (a rate needs two points) and does NOT refresh the
        estimate. Note the measured rate is averaged over the whole interval
        since the previous reading, so sparse polling also smooths the signal."""
        s = self.src[key]
        ok = False
        if s.prev_bytes is not None and ts > s.prev_ts:
            rate_mbps = (tx_bytes - s.prev_bytes) * 8.0 / (ts - s.prev_ts) / 1e6
            s.x_hat = max(0.0, rate_mbps / s.cap)
            s.eps = self.eps_meas
            s.last_update = ts
            s.refreshed = True
            s.n_updates += 1
            ok = True
        s.prev_bytes, s.prev_ts = tx_bytes, ts
        return ok

    # ---- slot boundary --------------------------------------------------------
    def tick(self, now):
        """Advance one slot. Returns the set of keys refreshed during the slot
        that just ended; everything else gets staler / more uncertain."""
        refreshed = set()
        for s in self.src.values():
            if s.refreshed:
                refreshed.add(s.key)
                s.refreshed = False
            else:
                s.eps = min(self.eps_max, self.alpha * s.eps + self.sigma)
        return refreshed

    # ---- queries ----------------------------------------------------------------
    def age(self, key, now):
        return max(0.0, now - self.src[key].last_update)

    def estimate(self, key):
        s = self.src[key]
        return s.x_hat, s.eps

    def bounds(self, key):
        """Worst-case interval for utilisation: (lo, hi). Used by Week-3 shield."""
        s = self.src[key]
        return max(0.0, s.x_hat - s.eps), s.x_hat + s.eps

    def total_uncertainty(self):
        return sum(s.weight * s.eps for s in self.src.values())

    def fidelity(self):
        return math.exp(-self.total_uncertainty())

    def rows(self, now, refreshed):
        out = []
        for k in sorted(self.src):
            s = self.src[k]
            out.append((k, s.x_hat, s.eps, self.age(k, now), int(k in refreshed), s.weight))
        return out


class TelemetryScheduler:
    """Chooses which sources to refresh each slot under a budget B_tel
    (paper eq. 1 with unit cost per poll, a single fidelity level).

      all          : poll every source every slot (ignores budget) - upper bound
      roundrobin   : fixed periodic polling, B sources per slot, importance-blind
      uncertainty  : greedy top-B by  w_m * eps_m * (1 + kappa * xhat_m)
                     (weighted uncertainty, boosted for busy links)
    """
    POLICIES = ('all', 'roundrobin', 'uncertainty')

    def __init__(self, policy='roundrobin', budget=2, kappa=1.0):
        if policy not in self.POLICIES:
            raise ValueError('policy must be one of %s' % (self.POLICIES,))
        self.policy, self.budget, self.kappa = policy, int(budget), float(kappa)
        self._rr = 0

    def select(self, twin):
        keys = sorted(twin.src)
        n = len(keys)
        if self.policy == 'all':
            return keys
        b = max(0, min(self.budget, n))
        if self.policy == 'roundrobin':
            sel = [keys[(self._rr + i) % n] for i in range(b)]
            self._rr = (self._rr + b) % n
            return sel
        def score(k):
            s = twin.src[k]
            return -(s.weight * s.eps * (1.0 + self.kappa * s.x_hat))
        return sorted(keys, key=lambda k: (score(k), k))[:b]
