"""
Week-2 controller: Week-1 routing + a selectively-synchronised digital twin.

Differences from baseline_te.py:
  * No "poll everything every second". A slot loop (TWIN_SLOT s) ages the twin,
    asks the TelemetryScheduler which <= B sources to refresh, and sends ONE
    single-port OFPPortStatsRequest per chosen source.
  * The reroute rule now reads the TWIN's estimate xhat (not raw stats), and
    deliberately ignores eps. This is the "naive twin consumer" that Week 3's
    safety validator replaces with a worst-case check.

Env knobs:
  POLL_POLICY       all | roundrobin | uncertainty     (default roundrobin)
  TELEMETRY_BUDGET  max port polls per slot            (default 2)
  TWIN_SLOT         slot length in seconds             (default 0.5)
  EPS_MEAS, TWIN_ALPHA, TWIN_SIGMA, EPS_MAX, KAPPA     twin / scheduler params
  HIGH_THRESH LOW_THRESH ALT_MAX HOLD_TIME REROUTE LOG_DIR   as in Week 1

Logs (LOG_DIR): twin_state.csv, polls.csv, events.csv

Run:  PYTHONPATH=$PWD ryu-manager controller/twin_te.py
"""
import csv
import os
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (CONFIG_DISPATCHER, MAIN_DISPATCHER,
                                    DEAD_DISPATCHER, set_ev_cls)
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub

import topo_config as T
from twin import NetworkTwin, TelemetryScheduler

SLOT = float(os.environ.get('TWIN_SLOT', '0.5'))
POLICY = os.environ.get('POLL_POLICY', 'roundrobin')
BUDGET = int(os.environ.get('TELEMETRY_BUDGET', '2'))
EPS_MEAS = float(os.environ.get('EPS_MEAS', '0.03'))
ALPHA = float(os.environ.get('TWIN_ALPHA', '1.0'))
SIGMA = float(os.environ.get('TWIN_SIGMA', '0.04'))
EPS_MAX = float(os.environ.get('EPS_MAX', '1.0'))
KAPPA = float(os.environ.get('KAPPA', '1.0'))
HIGH_THRESH = float(os.environ.get('HIGH_THRESH', '0.80'))
LOW_THRESH = float(os.environ.get('LOW_THRESH', '0.30'))
ALT_MAX = float(os.environ.get('ALT_MAX', '0.60'))
HOLD_TIME = float(os.environ.get('HOLD_TIME', '5.0'))
REROUTE = os.environ.get('REROUTE', '1') == '1'
LOG_DIR = os.environ.get('LOG_DIR', 'logs/default')
PRIO = 100
W_AGG, W_CORE = 1.0, 0.2          # importance weights w_m


class TwinTE(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        os.makedirs(LOG_DIR, exist_ok=True)

        self.port_of, self.cap = {}, {}
        for a, pa, b, pb, bw, _d, _r in T.LINKS:
            self.port_of[(a, b)], self.port_of[(b, a)] = pa, pb
            self.cap[(a, pa)], self.cap[(b, pb)] = bw, bw

        agg = {k for ks in T.AGG_PORTS.values() for k in ks}
        sources = {k: (c, W_AGG if k in agg else W_CORE) for k, c in self.cap.items()}
        self.twin = NetworkTwin(sources, EPS_MEAS, ALPHA, SIGMA, EPS_MAX, time.time())
        self.sched = TelemetryScheduler(POLICY, BUDGET, KAPPA)

        self.dps = {}
        self.mode = 'primary'
        self.last_change = 0.0
        self.n_polls = 0
        self.routes = {}
        for s in T.HOSTS:
            for d in T.HOSTS:
                if s != d:
                    self.routes[(s, d)] = self._path(s, d, 'primary')

        self.f_twin = open(os.path.join(LOG_DIR, 'twin_state.csv'), 'w', newline='')
        self.w_twin = csv.writer(self.f_twin)
        self.w_twin.writerow(['ts', 'dpid', 'port', 'x_hat', 'eps', 'aoi', 'polled', 'weight'])
        self.f_poll = open(os.path.join(LOG_DIR, 'polls.csv'), 'w', newline='')
        self.w_poll = csv.writer(self.f_poll)
        self.w_poll.writerow(['ts', 'dpid', 'port'])
        self.f_ev = open(os.path.join(LOG_DIR, 'events.csv'), 'w', newline='')
        self.w_ev = csv.writer(self.f_ev)
        self.w_ev.writerow(['ts', 'event', 'detail'])
        self._event('start', 'policy=%s budget=%d slot=%.2f eps_meas=%.3f alpha=%.3f sigma=%.3f '
                    'eps_max=%.2f kappa=%.2f reroute=%s' %
                    (POLICY, BUDGET, SLOT, EPS_MEAS, ALPHA, SIGMA, EPS_MAX, KAPPA, REROUTE))

        hub.spawn(self._slot_loop)

    # ------------------------------------------------------------ helpers
    def _event(self, kind, detail=''):
        self.w_ev.writerow(['%.3f' % time.time(), kind, detail])
        self.f_ev.flush()
        self.logger.info('[%s] %s', kind, detail)

    @staticmethod
    def _path(src, dst, which):
        hs, hd = T.HOSTS[src], T.HOSTS[dst]
        if hs['dpid'] == hd['dpid']:
            return [hs['dpid']]
        base = T.PATHS[which]
        return list(base) if hs['dpid'] == base[0] else list(reversed(base))

    def _out_port(self, path, i, dst):
        if i == len(path) - 1:
            return T.HOSTS[dst]['port']
        return self.port_of[(path[i], path[i + 1])]

    @staticmethod
    def _match(dp, src, dst):
        return dp.ofproto_parser.OFPMatch(eth_type=0x0800,
                                          ipv4_src=T.HOSTS[src]['ip'],
                                          ipv4_dst=T.HOSTS[dst]['ip'])

    def _add_flow(self, dp, src, dst, out_port):
        ofp, p = dp.ofproto, dp.ofproto_parser
        inst = [p.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, [p.OFPActionOutput(out_port)])]
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=PRIO,
                                 match=self._match(dp, src, dst), instructions=inst))

    def _del_flow(self, dp, src, dst):
        ofp, p = dp.ofproto, dp.ofproto_parser
        dp.send_msg(p.OFPFlowMod(datapath=dp, command=ofp.OFPFC_DELETE_STRICT, priority=PRIO,
                                 out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
                                 match=self._match(dp, src, dst)))

    # ------------------------------------------------------ switch lifecycle
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _on_switch_features(self, ev):
        dp = ev.msg.datapath
        ofp, p = dp.ofproto, dp.ofproto_parser
        self.dps[dp.id] = dp
        dp.send_msg(p.OFPFlowMod(datapath=dp, table_id=ofp.OFPTT_ALL, command=ofp.OFPFC_DELETE,
                                 out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
                                 match=p.OFPMatch()))
        n = 0
        for (s, d), path in self.routes.items():
            if dp.id in path:
                self._add_flow(dp, s, d, self._out_port(path, path.index(dp.id), d))
                n += 1
        self._event('switch_up', 'dpid=%d proactive_flows=%d' % (dp.id, n))

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _on_state_change(self, ev):
        if ev.state == DEAD_DISPATCHER and ev.datapath.id in self.dps:
            del self.dps[ev.datapath.id]
            self._event('switch_down', 'dpid=%d' % ev.datapath.id)

    # ----------------------------------------------------- twin slot loop
    def _slot_loop(self):
        hub.sleep(1.0)
        while True:
            now = time.time()
            refreshed = self.twin.tick(now)
            for (dpid, port), x, eps, aoi, pol, w in self.twin.rows(now, refreshed):
                self.w_twin.writerow(['%.3f' % now, dpid, port, '%.4f' % x, '%.4f' % eps,
                                      '%.3f' % aoi, pol, w])
            self.f_twin.flush()

            self._control()

            for key in self.sched.select(self.twin):
                dp = self.dps.get(key[0])
                if dp is None:
                    continue
                dp.send_msg(dp.ofproto_parser.OFPPortStatsRequest(dp, 0, key[1]))
                self.n_polls += 1
                self.w_poll.writerow(['%.3f' % time.time(), key[0], key[1]])
            self.f_poll.flush()
            hub.sleep(SLOT)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _on_port_stats(self, ev):
        now = time.time()
        dpid = ev.msg.datapath.id
        for st in ev.msg.body:
            key = (dpid, st.port_no)
            if key in self.twin.src:
                self.twin.update(key, st.tx_bytes, now)

    # ------------------------------------- naive twin-consuming policy (Wk 1 rule)
    def _agg_est(self, which):
        return max(self.twin.estimate(k)[0] for k in T.AGG_PORTS[which])

    def _control(self):
        if not REROUTE or time.time() - self.last_change < HOLD_TIME:
            return
        up, ua = self._agg_est('primary'), self._agg_est('alt')
        if self.mode == 'primary' and up > HIGH_THRESH and ua < ALT_MAX:
            self._reroute('alt', 'xhat_primary=%.2f xhat_alt=%.2f eps_primary=%.2f' %
                          (up, ua, max(self.twin.estimate(k)[1] for k in T.AGG_PORTS['primary'])))
        elif self.mode == 'alt' and up < LOW_THRESH:
            self._reroute('primary', 'xhat_primary=%.2f xhat_alt=%.2f' % (up, ua))

    def _reroute(self, new_mode, reason):
        s, d = T.URLLC_PAIR
        n = 0
        for a, b in ((s, d), (d, s)):
            new = self._path(a, b, new_mode)
            n += self._apply_path(a, b, self.routes[(a, b)], new)
            self.routes[(a, b)] = new
        self.mode, self.last_change = new_mode, time.time()
        self._event('reroute', 'to=%s|flow_mods=%d|%s' % (new_mode, n, reason))

    def _apply_path(self, src, dst, old, new):
        old_out = {old[i]: self._out_port(old, i, dst) for i in range(len(old))}
        n = 0
        for i in reversed(range(len(new))):
            dp, port = self.dps.get(new[i]), self._out_port(new, i, dst)
            if dp is None or old_out.get(new[i]) == port:
                continue
            self._add_flow(dp, src, dst, port)
            n += 1
        for dpid in old:
            if dpid not in new and dpid in self.dps:
                self._del_flow(self.dps[dpid], src, dst)
                n += 1
        return n
