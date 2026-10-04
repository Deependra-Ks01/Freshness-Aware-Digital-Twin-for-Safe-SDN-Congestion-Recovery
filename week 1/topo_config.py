"""
Single source of truth for the topology. Imported by BOTH the Mininet script
and the Ryu controller, so port numbers / paths can never drift apart.

Topology (2 domains, diamond in each, joined by two aggregation links):

   Domain A                                   Domain B
   hA1 (URLLC src) --+                    +-- hB1 (URLLC dst)
   hA2 (bulk src)  --+-- sA1 --+-- sA2 ==[agg-primary 10M]== sB2 --+-- sB1 --+-- hB2 (bulk dst)
   hA3 (MEC A)     --+         |                                    |         +-- hB3 (MEC B)
                               +-- sA3 ==[agg-alt 10M]======= sB3 --+
"""

AGG_BW_MBPS = 10      # aggregation links (the bottleneck)
CORE_BW_MBPS = 100    # everything else
QUEUE_PKTS = 100      # netem queue limit -> ~120 ms of buffering at 10 Mbps

# dpid -> name
SWITCHES = {1: 'sA1', 2: 'sA2', 3: 'sA3', 4: 'sB1', 5: 'sB2', 6: 'sB3'}

# Hosts: switch port is explicit so the controller knows it without discovery
HOSTS = {
    'hA1': dict(ip='10.0.0.1',  mac='00:00:00:00:00:01', dpid=1, port=3, role='urllc_src'),
    'hA2': dict(ip='10.0.0.2',  mac='00:00:00:00:00:02', dpid=1, port=4, role='bulk_src'),
    'hA3': dict(ip='10.0.0.3',  mac='00:00:00:00:00:03', dpid=1, port=5, role='mec_A'),
    'hB1': dict(ip='10.0.0.11', mac='00:00:00:00:00:0b', dpid=4, port=3, role='urllc_dst'),
    'hB2': dict(ip='10.0.0.12', mac='00:00:00:00:00:0c', dpid=4, port=4, role='bulk_dst'),
    'hB3': dict(ip='10.0.0.13', mac='00:00:00:00:00:0d', dpid=4, port=5, role='mec_B'),
}

# (dpid_a, port_a, dpid_b, port_b, bw_mbps, delay_ms, role)
LINKS = [
    (1, 1, 2, 1, CORE_BW_MBPS, 1, 'core'),
    (1, 2, 3, 1, CORE_BW_MBPS, 1, 'core'),
    (2, 2, 5, 2, AGG_BW_MBPS,  5, 'agg_primary'),
    (3, 2, 6, 2, AGG_BW_MBPS,  8, 'agg_alt'),      # alt path is longer -> reroute has a latency price
    (4, 1, 5, 1, CORE_BW_MBPS, 1, 'core'),
    (4, 2, 6, 1, CORE_BW_MBPS, 1, 'core'),
]

# Domain-A edge -> Domain-B edge, as a list of dpids. Reverse for B -> A.
PATHS = {
    'primary': [1, 2, 5, 4],
    'alt':     [1, 3, 6, 4],
}

# (dpid, port) of the egress ports on each aggregation link (both directions)
AGG_PORTS = {
    'primary': [(2, 2), (5, 2)],
    'alt':     [(3, 2), (6, 2)],
}

# The latency-sensitive "URLLC" flow that the controller is allowed to reroute
URLLC_PAIR = ('hA1', 'hB1')
BULK_PAIR = ('hA2', 'hB2')

# URLLC intent (RTT, ms) -- L_max_k in the paper. Primary RTT ~14 ms, alt ~20 ms.
LMAX_RTT_MS = 30.0
