"""
Week-1 BASELINE controller: proactive static routing + periodic port-stats
polling + immediate threshold-based reroute of the URLLC flow.

No digital twin, no freshness model, no safety shield -- this is the reference
that Weeks 2-4 improve on.

Env knobs (so Week 4 can sweep them without editing code):
  POLL_INTERVAL  seconds between OpenFlow port-stats polls   (default 1.0)
  HIGH_THRESH    primary-agg utilisation that triggers reroute (default 0.80)
  LOW_THRESH     primary-agg utilisation below which we revert (default 0.30)
  ALT_MAX        alt path must be below this to be a target     (default 0.60)
  HOLD_TIME      min seconds between path changes (anti-flap)   (default 5.0)
  REROUTE        1 = reroute enabled, 0 = static routing only   (default 1)
  LOG_DIR        where CSV logs are written                     (default logs/default)

Run:  PYTHONPATH=$PWD ryu-manager controller/baseline_te.py
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

POLL_INTERVAL = float(os.environ.get('POLL_INTERVAL', '1.0'))
HIGH_THRESH = float(os.environ.get('HIGH_THRESH', '0.80'))
LOW_THRESH = float(os.environ.get('LOW_THRESH', '0.30'))
ALT_MAX = float(os.environ.get('ALT_MAX', '0.60'))
HOLD_TIME = float(os.environ.get('HOLD_TIME', '5.0'))
REROUTE = os.environ.get('REROUTE', '1') == '1'
LOG_DIR = os.environ.get('LOG_DIR', 'logs/default')
PRIO = 100


class BaselineTE(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        os.makedirs(LOG_DIR, exist_ok=True)

        # ---- static topology knowledge (from topo_config) ----
        self.port_of = {}   # (dpid_from, dpid_to) -> egress port on dpid_from
        self.cap = {}       # (dpid, port) -> capacity in Mbps
        for a, pa, b, pb, bw, _delay, _role in T.LINKS:
            self.port_of[(a, b)] = pa
            self.port_of[(b, a)] = pb
            self.cap[(a, pa)] = bw
            self.cap[(b, pb)] = bw

        # ---- runtime state ----
        self.dps = {}          # dpid -> datapath
        self.prev = {}         # (dpid, port) -> (tx_bytes, ts)
        self.util = {}         # (dpid, port) -> latest utilisation 0..1
        self.mode = 'primary'  # path currently used by URLLC flow
        self.last_change = 0.0
        self.n_reroutes = 0
        self.n_flowmods = 0

        # routes[(src_host, dst_host)] = list of dpids
        self.routes = {}
        for s in T.HOSTS:
            for d in T.HOSTS:
                if s != d:
                    self.routes[(s, d)] = self._path(s, d, 'primary')

        # ---- logs ----
        self.f_stats = open(os.path.join(LOG_DIR, 'link_stats.csv'), 'w', newline='')
        self.w_stats = csv.writer(self.f_stats)
        self.w_stats.writerow(['ts', 'dpid', 'port', 'tx_mbps', 'util'])
        self.f_ev = open(os.path.join(LOG_DIR, 'events.csv'), 'w', newline='')
        self.w_ev = csv.writer(self.f_ev)
        self.w_ev.writerow(['ts', 'event', 'detail'])
        self._event('start', 'reroute=%s poll=%.2fs high=%.2f low=%.2f alt_max=%.2f hold=%.1fs' %
                    (REROUTE, POLL_INTERVAL, HIGH_THRESH, LOW_THRESH, ALT_MAX, HOLD_TIME))

        self.monitor_thread = hub.spawn(self._monitor)

    # ------------------------------------------------------------------ helpers
    def _event(self, kind, detail=''):
        self.w_ev.writerow(['%.3f' % time.time(), kind, detail])
        self.f_ev.flush()
        self.logger.info('[%s] %s', kind, detail)

    @staticmethod
    def _path(src, dst, which):
        hs, hd = T.HOSTS[src], T.HOSTS[dst]
        if hs['dpid'] == hd['dpid']:
            return [hs['dpid']]
        base = T.PATHS[which]                       # A-edge -> B-edge
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
        inst = [p.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS,
                                        [p.OFPActionOutput(out_port)])]
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=PRIO,
                                 match=self._match(dp, src, dst), instructions=inst))

    def _del_flow(self, dp, src, dst):
        ofp, p = dp.ofproto, dp.ofproto_parser
        dp.send_msg(p.OFPFlowMod(datapath=dp, command=ofp.OFPFC_DELETE_STRICT,
                                 priority=PRIO, out_port=ofp.OFPP_ANY,
                                 out_group=ofp.OFPG_ANY,
                                 match=self._match(dp, src, dst)))

    # --------------------------------------------------------- switch lifecycle
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _on_switch_features(self, ev):
        dp = ev.msg.datapath
        ofp, p = dp.ofproto, dp.ofproto_parser
        self.dps[dp.id] = dp
        # wipe any stale flows (controller restarts), then install our routes
        dp.send_msg(p.OFPFlowMod(datapath=dp, table_id=ofp.OFPTT_ALL,
                                 command=ofp.OFPFC_DELETE, out_port=ofp.OFPP_ANY,
                                 out_group=ofp.OFPG_ANY, match=p.OFPMatch()))
        n = 0
        for (s, d), path in self.routes.items():
            if dp.id in path:
                i = path.index(dp.id)
                self._add_flow(dp, s, d, self._out_port(path, i, d))
                n += 1
        self._event('switch_up', 'dpid=%d proactive_flows=%d' % (dp.id, n))

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _on_state_change(self, ev):
        if ev.state == DEAD_DISPATCHER and ev.datapath.id in self.dps:
            del self.dps[ev.datapath.id]
            self._event('switch_down', 'dpid=%d' % ev.datapath.id)

    # ------------------------------------------------------------- monitoring
    def _monitor(self):
        while True:
            for dp in list(self.dps.values()):
                req = dp.ofproto_parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY)
                dp.send_msg(req)
            hub.sleep(POLL_INTERVAL)      # replies are processed while we sleep
            self._control()

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _on_port_stats(self, ev):
        now = time.time()
        dpid = ev.msg.datapath.id
        for st in ev.msg.body:
            key = (dpid, st.port_no)
            if key not in self.cap:            # only inter-switch ports
                continue
            if key in self.prev:
                b0, t0 = self.prev[key]
                dt = now - t0
                if dt > 0:
                    mbps = (st.tx_bytes - b0) * 8.0 / dt / 1e6
                    u = mbps / self.cap[key]
                    self.util[key] = u
                    self.w_stats.writerow(['%.3f' % now, dpid, st.port_no,
                                           '%.3f' % mbps, '%.3f' % u])
            self.prev[key] = (st.tx_bytes, now)
        self.f_stats.flush()

    # ------------------------------------------------------ baseline decision
    def _agg_util(self, which):
        vals = [self.util[k] for k in T.AGG_PORTS[which] if k in self.util]
        return max(vals) if vals else None

    def _control(self):
        if not REROUTE:
            return
        up, ua = self._agg_util('primary'), self._agg_util('alt')
        if up is None or ua is None:
            return
        if time.time() - self.last_change < HOLD_TIME:
            return
        if self.mode == 'primary' and up > HIGH_THRESH and ua < ALT_MAX:
            self._reroute('alt', 'util_primary=%.2f util_alt=%.2f' % (up, ua))
        elif self.mode == 'alt' and up < LOW_THRESH:
            self._reroute('primary', 'util_primary=%.2f util_alt=%.2f' % (up, ua))

    def _reroute(self, new_mode, reason):
        s, d = T.URLLC_PAIR
        n = 0
        for a, b in ((s, d), (d, s)):
            n += self._apply_path(a, b, self.routes[(a, b)], self._path(a, b, new_mode))
            self.routes[(a, b)] = self._path(a, b, new_mode)
        self.mode = new_mode
        self.last_change = time.time()
        self.n_reroutes += 1
        self.n_flowmods += n
        self._event('reroute', 'to=%s|flow_mods=%d|%s' % (new_mode, n, reason))

    def _apply_path(self, src, dst, old, new):
        """Make-before-break: install downstream-first, only where the egress
        port actually changes; then delete rules on switches no longer used."""
        old_out = {old[i]: self._out_port(old, i, dst) for i in range(len(old))}
        n = 0
        for i in reversed(range(len(new))):
            dp = self.dps.get(new[i])
            port = self._out_port(new, i, dst)
            if dp is None or old_out.get(new[i]) == port:
                continue
            self._add_flow(dp, src, dst, port)
            n += 1
        for dpid in old:
            if dpid not in new and dpid in self.dps:
                self._del_flow(self.dps[dpid], src, dst)
                n += 1
        return n
