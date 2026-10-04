#!/usr/bin/env python3
"""
Mininet driver (v2: adds ground-truth sampler + bursty bulk traffic).
Run with sudo and system python3 (where mininet is installed).

  sudo python3 run_experiment.py --mode cli
  sudo python3 run_experiment.py --mode check
  sudo python3 run_experiment.py --mode experiment --out logs/x [--burst-on 8 --burst-off 7]

Timeline of --mode experiment:
   0 .. warmup           : URLLC ping only
   warmup .. +bulk-time  : bulk flow hA2->hB2 (continuous, or bursts of
                           burst-on seconds ON / burst-off seconds OFF)
   then cooldown
Independently of the controller, a sampler thread reads the kernel tx_bytes
counter of every switch-to-switch interface every --gt-interval seconds ->
ground_truth.csv. This is the "physical network state" the twin is compared to.
"""
import argparse
import csv
import json
import os
import subprocess
import threading
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


class GroundTruthSampler(threading.Thread):
    """Reads /sys/class/net/<sw>-eth<port>/statistics/tx_bytes (switches live in
    the root namespace, so this works from the Mininet process)."""

    def __init__(self, path, interval):
        super().__init__(daemon=True)
        self.path, self.interval = path, interval
        self.stop_evt = threading.Event()
        self.ifaces = []
        for a, pa, b, pb, _bw, _d, _r in T.LINKS:
            for dpid, port in ((a, pa), (b, pb)):
                self.ifaces.append((dpid, port, '/sys/class/net/%s-eth%d/statistics/tx_bytes'
                                    % (T.SWITCHES[dpid], port)))

    def run(self):
        with open(self.path, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['ts', 'dpid', 'port', 'tx_bytes'])
            nxt = time.time()
            while not self.stop_evt.is_set():
                ts = time.time()
                for dpid, port, p in self.ifaces:
                    try:
                        with open(p) as g:
                            w.writerow(['%.3f' % ts, dpid, port, int(g.read())])
                    except (OSError, ValueError):
                        pass
                f.flush()
                nxt += self.interval
                self.stop_evt.wait(max(0.0, nxt - time.time()))

    def stop(self):
        self.stop_evt.set()
        self.join(timeout=2)


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

    sampler = GroundTruthSampler(os.path.join(a.out, 'ground_truth.csv'), a.gt_interval)
    sampler.start()

    f_ping = open(os.path.join(a.out, 'urllc_ping.txt'), 'w')
    f_srv = open(os.path.join(a.out, 'bulk_server.txt'), 'w')
    f_cli = open(os.path.join(a.out, 'bulk_client.txt'), 'w')

    srv = hB2.popen(['iperf', '-s', '-i', '1'] + (['-u'] if udp else []),
                    stdout=f_srv, stderr=subprocess.STDOUT)
    time.sleep(1)

    t0 = time.time()
    ping = hA1.popen(['ping', '-n', '-D', '-i', str(a.ping_interval),
                      '-c', str(n_pings), hB1.IP()],
                     stdout=f_ping, stderr=subprocess.STDOUT)
    info('*** URLLC probe started (%d pings)\n' % n_pings)
    time.sleep(a.warmup)

    t_bulk_start = time.time()
    t_target_end = t_bulk_start + a.bulk_time
    bursts = []
    while True:
        remaining = t_target_end - time.time()
        if remaining < 1:
            break
        dur = remaining if a.burst_on <= 0 else min(a.burst_on, remaining)
        cmd = ['iperf', '-c', hB2.IP(), '-t', str(max(1, int(round(dur)))), '-i', '1']
        if udp:
            cmd += ['-u', '-b', '%dM' % a.bulk_mbps]
        b0 = time.time()
        info('*** bulk ON  (%s)\n' % ' '.join(cmd))
        cli = hA2.popen(cmd, stdout=f_cli, stderr=subprocess.STDOUT)
        try:
            cli.wait(timeout=dur + 15)
        except subprocess.TimeoutExpired:
            cli.kill()
        bursts.append([b0, time.time()])
        if a.burst_on <= 0:
            break
        gap = min(a.burst_off, max(0.0, t_target_end - time.time()))
        info('*** bulk OFF for %.1fs\n' % gap)
        time.sleep(gap)
    t_bulk_end = time.time()

    info('*** cooling down\n')
    time.sleep(a.cooldown)
    try:
        ping.wait(timeout=15)
    except subprocess.TimeoutExpired:
        ping.terminate()
    srv.terminate()
    sampler.stop()

    meta = dict(t0=t0, t_bulk_start=t_bulk_start, t_bulk_end=t_bulk_end, bursts=bursts,
                n_pings=n_pings, ping_interval=a.ping_interval,
                lmax_rtt_ms=T.LMAX_RTT_MS, bulk_proto=a.bulk_proto,
                bulk_mbps=a.bulk_mbps, gt_interval=a.gt_interval,
                label=os.path.basename(a.out.rstrip('/')))
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
    ap.add_argument('--burst-on', type=float, default=0, help='0 = continuous bulk')
    ap.add_argument('--burst-off', type=float, default=0)
    ap.add_argument('--ping-interval', type=float, default=0.1)
    ap.add_argument('--gt-interval', type=float, default=0.1)
    args = ap.parse_args()

    setLogLevel('info')
    net = build_net(args.ctrl_ip, args.ctrl_port)
    net.start()
    try:
        net.waitConnected(timeout=30)
    except Exception:
        pass
    time.sleep(3)
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
