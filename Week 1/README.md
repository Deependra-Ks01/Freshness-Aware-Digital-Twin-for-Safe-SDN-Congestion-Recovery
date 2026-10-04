# Week 1 – Baseline SDN system (Mininet + Ryu)

Deliverable: a working baseline demo + a congestion scenario reproducible with one command.

## 0. One-time setup (inside the Ubuntu VM)

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch iperf python3-matplotlib \
                    software-properties-common git tmux tcpdump
sudo systemctl enable --now openvswitch-switch

# Ryu needs an old Python (3.9); newer Pythons break its eventlet dependency
sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt update
sudo apt install -y python3.9 python3.9-venv python3.9-dev
python3.9 -m venv ~/ryu-venv
source ~/ryu-venv/bin/activate
pip install --upgrade pip wheel "setuptools<66"
pip install "eventlet==0.30.2" ryu==4.34
ryu-manager --version          # should print ryu-manager 4.34
deactivate

# If tc/netem is missing ("Unknown qdisc netem"):
sudo apt install -y linux-modules-extra-$(uname -r) && sudo modprobe sch_netem
```

## 1. Smoke test (manual, 2 terminals)

Terminal 1 (controller):
```bash
cd sdn-twin && source ~/ryu-venv/bin/activate
PYTHONPATH=$PWD LOG_DIR=logs/manual ryu-manager controller/baseline_te.py
```
Terminal 2 (network):
```bash
cd sdn-twin
sudo python3 run_experiment.py --mode check     # all-pairs ping + flow dump
sudo python3 run_experiment.py --mode cli       # interactive: mininet> hA1 ping -c3 hB1
```
Expected: 0% packet loss, URLLC RTT ~14 ms (primary path).

## 2. Reproducible experiment

```bash
./run_scenario.sh static   0      # reroute disabled  -> shows the damage
./run_scenario.sh baseline 1      # reactive reroute  -> baseline behaviour
python3 analyze.py logs/static logs/baseline
```
Outputs per run in `logs/<name>/`: `urllc_ping.txt`, `bulk_*.txt`, `link_stats.csv`
(polled telemetry = raw input for the Week-2 twin), `events.csv`, `meta.json`,
`summary.json`, `timeline.png`.

## 3. Topology / design cheat-sheet

| Item | Value |
|---|---|
| Switches (dpid) | sA1=1 sA2=2 sA3=3 sB1=4 sB2=5 sB3=6 |
| Primary path | sA1-sA2=sB2-sB1 (agg 10 Mbps, 5 ms) |
| Alternate path | sA1-sA3=sB3-sB1 (agg 10 Mbps, 8 ms) |
| URLLC flow | hA1 <-> hB1, ping every 100 ms, L_max = 30 ms RTT |
| Bulk flow | hA2 -> hB2, iperf UDP 15 Mbps (>10 Mbps => congestion) |
| MEC hosts | hA3, hB3 (idle in Week 1; used in stretch goals) |
| Routing | proactive per-(src,dst) IPv4 flows, static ARP, no flooding (topology has loops) |
| Baseline policy | poll every 1 s; if primary-agg util > 0.8 and alt < 0.6 -> move URLLC to alt; revert when util < 0.3; 5 s hold-down |

## 4. Troubleshooting

| Symptom | Fix |
|---|---|
| `ImportError ... ALREADY_HANDLED` from ryu-manager | wrong Python/eventlet; recreate venv with python3.9 and `eventlet==0.30.2` |
| `Unknown qdisc "netem"` | install `linux-modules-extra`, `modprobe sch_netem` |
| pingAll shows 100% loss | controller not running / not on 6653; check `logs/*/ryu.log`; `sudo ovs-ofctl -O OpenFlow13 dump-flows sA1` |
| Leftover interfaces / "File exists" | `sudo mn -c` |
| `Cannot find required executable iperf` | `sudo apt install iperf` |
| no reroute happening | check `events.csv` for `start`; lower `HIGH_THRESH`; confirm `link_stats.csv` shows util ~1.0 on dpid 2 port 2 |

## 5. Week-1 plan (7 days, 3 people)

| Day | Controller/Mininet eng. | Twin/safety eng. | Eval lead |
|---|---|---|---|
| 1 | VM + Mininet + OVS setup | Ryu venv setup | Repo, git workflow, read paper Sec. IV-VI, XII |
| 2 | Topology runs, pingAll OK | Verify OF1.3 flow install, dump-flows | Define metrics list (RTT, loss, SLA %, flow_mods, polls) |
| 3 | Congestion scenario works | Port-stats polling -> link_stats.csv | Draft analyze.py plots |
| 4 | Baseline reroute logic | Review reroute code, add unit checks | Run static vs baseline, first plots |
| 5 | Fix bugs, tune thresholds | Design Week-2 twin class (AoI, eps) on paper | Sweep POLL_INTERVAL 0.5/1/2 s |
| 6 | Record demo video | Prepare twin interface hooks | Tables/figures for report |
| 7 | Freeze v1 (git tag `week1`) | Freeze | Week-1 writeup + Week-2 plan |

## 6. Week-1 "definition of done"

- [ ] `--mode check` gives 0% loss on all 30 host pairs
- [ ] `./run_scenario.sh static 0` shows large RTT/loss during bulk
- [ ] `./run_scenario.sh baseline 1` shows a reroute in `events.csv` and RTT back under 30 ms
- [ ] `timeline.png` for both runs; `summary.json` comparison table
- [ ] Code in git, tag `week1`, 2-minute demo video
