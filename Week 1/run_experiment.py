#!/usr/bin/env python3
"""
Mininet driver. Run with sudo and system python3 (where mininet is installed).

  sudo python3 run_experiment.py --mode cli                       # interactive
  sudo python3 run_experiment.py --mode check                     # connectivity + flow dump
  sudo python3 run_experiment.py --mode experiment --out logs/x   # scripted congestion run

Timeline of --mode experiment:
   0 .. warmup            : URLLC ping only
   warmup .. +bulk-time   : bulk flow (hA2->hB2) saturates the primary aggregation link
   then cooldown          : bulk stopped, watch recovery / revert
"""
import argparse
import json
import os
import subprocess
import time

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

import topo_config as T


def build_net(ip, port):
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,
                  autoSetMacs=False, autoStaticArp=True)
    net.addController('c0', controller=RemoteController, ip=ip, port=port)
    sw = {}
    for dpid, name in T.SWITCHES.items():
        sw[dpid] = net.addSwitch(name, dpid='%016x' % dpid, protocols='OpenFlow13')
    for name, h in T.HOSTS.items():
        host = net.addHost(name, ip=h['ip'] + '/24', mac=h['mac'])
        net.addLink(host, sw[h['dpid']], port2=h['port'])
    for a, pa, b, pb, bw, delay, _role in T.LINKS:
        net.addLink(sw[a], sw[b], port1=pa, port2=pb, bw=bw,
                    delay='%dms' % delay, max_queue_size=T.QUEUE_PKTS)
    return net


def check(net):
    info('*** all-pairs ping\n')
    net.pingAll()
    for name in T.SWITCHES.values():
        info('*** flows on %s\n' % name)
        os.system('ovs-ofctl -O OpenFlow13 dump-flows %s' % name)


def experiment(net, a):
    os.makedirs(a.out, exist_ok=True)
    hA1, hA2 = net.get('hA1'), net.get('hA2')
    hB1, hB2 = net.get('hB1'), net.get('hB2')
    total = a.warmup + a.bulk_time + a.cooldown
    n_pings = int(total / a.ping_interval)
    udp = a.bulk_proto == 'udp'

    f_ping = open(os.path.join(a.out, 'urllc_ping.txt'), 'w')
    f_srv = open(os.path.join(a.out, 'bulk_server.txt'), 'w')
    f_cli = open(os.path.join(a.out, 'bulk_client.txt'), 'w')

    srv_cmd = ['iperf', '-s', '-i', '1'] + (['-u'] if udp else [])
    srv = hB2.popen(srv_cmd, stdout=f_srv, stderr=subprocess.STDOUT)
    time.sleep(1)

    t0 = time.time()
    ping = hA1.popen(['ping', '-n', '-D', '-i', str(a.ping_interval),
                      '-c', str(n_pings), hB1.IP()],
                     stdout=f_ping, stderr=subprocess.STDOUT)
    info('*** URLLC probe started (%d pings)\n' % n_pings)
    time.sleep(a.warmup)

    t_bulk_start = time.time()
    cli_cmd = ['iperf', '-c', hB2.IP(), '-t', str(a.bulk_time), '-i', '1']
    if udp:
        cli_cmd += ['-u', '-b', '%dM' % a.bulk_mbps]
    info('*** bulk flow ON (%s)\n' % ' '.join(cli_cmd))
    cli = hA2.popen(cli_cmd, stdout=f_cli, stderr=subprocess.STDOUT)
    try:
        cli.wait(timeout=a.bulk_time + 15)
    except subprocess.TimeoutExpired:
        cli.kill()
    t_bulk_end = time.time()
    info('*** bulk flow OFF, cooling down\n')
    time.sleep(a.cooldown)
    try:
        ping.wait(timeout=15)
    except subprocess.TimeoutExpired:
        ping.terminate()
    srv.terminate()

    meta = dict(t0=t0, t_bulk_start=t_bulk_start, t_bulk_end=t_bulk_end,
                n_pings=n_pings, ping_interval=a.ping_interval,
                lmax_rtt_ms=T.LMAX_RTT_MS, bulk_proto=a.bulk_proto,
                bulk_mbps=a.bulk_mbps, label=os.path.basename(a.out.rstrip('/')))
    with open(os.path.join(a.out, 'meta.json'), 'w') as f:
        json.dump(meta, f, indent=2)
    info('*** experiment done -> %s\n' % a.out)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['cli', 'check', 'experiment'], default='cli')
    ap.add_argument('--out', default='logs/manual')
    ap.add_argument('--ctrl-ip', default='127.0.0.1')
    ap.add_argument('--ctrl-port', type=int, default=6653)
    ap.add_argument('--warmup', type=float, default=10)
    ap.add_argument('--bulk-time', type=float, default=30)
    ap.add_argument('--cooldown', type=float, default=15)
    ap.add_argument('--bulk-mbps', type=int, default=15)
    ap.add_argument('--bulk-proto', choices=['udp', 'tcp'], default='udp')
    ap.add_argument('--ping-interval', type=float, default=0.1)
    args = ap.parse_args()

    setLogLevel('info')
    net = build_net(args.ctrl_ip, args.ctrl_port)
    net.start()
    try:
        net.waitConnected(timeout=30)
    except Exception:
        pass
    time.sleep(3)   # let the controller push flows
    try:
        if args.mode == 'cli':
            CLI(net)
        elif args.mode == 'check':
            check(net)
        else:
            experiment(net, args)
    finally:
        net.stop()
        uid, gid = os.environ.get('SUDO_UID'), os.environ.get('SUDO_GID')
        if uid and os.path.isdir(args.out):
            subprocess.run(['chown', '-R', '%s:%s' % (uid, gid), args.out])
