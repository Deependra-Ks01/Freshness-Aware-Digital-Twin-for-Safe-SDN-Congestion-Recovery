# Week 2 – Digital-twin module (AoI + uncertainty + telemetry budget)

Deliverable: a plot of the twin's estimated link state vs. independently measured ground
truth over time, showing divergence when the twin is not refreshed.

## What is new vs. Week 1

| File | Purpose |
|---|---|
| `twin.py` | `NetworkTwin` (AoI, eps, bounds, fidelity F), `TelemetryScheduler` (all / roundrobin / uncertainty). Pure Python, unit-tested. |
| `controller/twin_te.py` | Ryu app: slot loop, budgeted single-port polls, twin-fed reroute rule |
| `run_experiment.py` (REPLACES Week-1 file) | adds ground-truth sampler thread + bursty bulk traffic |
| `gt_utils.py`, `analyze_twin.py` | truth computation, metrics, plots |
| `calibrate_twin.py` | fits `TWIN_SIGMA`, `EPS_MEAS` from a ground-truth log |
| `run_twin.sh`, `sweep_week2.sh`, `run_scenario.sh` (updated) | one-command runs |
| `tools/synth_run.py`, `test_twin.py` | test everything without Mininet |

## Model (paper Sec. IV-VI, XX)

* Telemetry source m = directed switch-to-switch port (12 of them). State = utilisation (tx rate / capacity).
* Slot = 0.5 s. Refreshed in slot: `eps = EPS_MEAS`. Otherwise `eps = min(EPS_MAX, ALPHA*eps + SIGMA)`; AoI keeps growing.
* Cold start: eps = EPS_MAX (twin knows nothing until a port is read twice - a rate needs two counters).
* Uncertainty set `U = {x : |x - xhat| <= eps}`; `twin.bounds(key)` gives worst-case (lo, hi) for the Week-3 shield.
* Fidelity `F = exp(-sum w_m eps_m)`. Weights: aggregation ports 1.0, core ports 0.2.
* Budget: at most `TELEMETRY_BUDGET` single-port OpenFlow stats requests per slot (unit cost, one fidelity level; q_m is dropped on purpose).
* Policies: `all` (upper bound, ignores budget), `roundrobin` (fixed periodic, importance-blind), `uncertainty` (top-B of `w*eps*(1+kappa*xhat)`).
* The measured rate is averaged since the previous counter reading, so sparse polling also *smooths* fast bursts - this is real behaviour, not a bug.
* The Week-2 controller reroutes using `xhat` and ignores `eps` (naive twin consumer). Week 3 replaces it with the worst-case check.

## Step 0 – dry run without Mininet (5 min)

```bash
python3 test_twin.py                                    # 5 PASS lines
python3 tools/synth_run.py --out logs/synth_rr_B2 --policy roundrobin  --budget 2
python3 tools/synth_run.py --out logs/synth_un_B2 --policy uncertainty --budget 2
python3 analyze_twin.py logs/synth_rr_B2 logs/synth_un_B2
```
Open `logs/synth_rr_B2/twin_vs_truth.png`. Delete `logs/synth_*` before the real runs.

## Step 1 – real runs

Copy the new files over your Week-1 folder (overwrite `run_experiment.py`, `run_scenario.sh`).
Week-1 commands still work.

```bash
./run_twin.sh all 12              # reference: refresh everything every slot
./run_twin.sh roundrobin 2        # fixed periodic, 2 polls / slot
./run_twin.sh uncertainty 2       # uncertainty-driven, same budget
python3 analyze_twin.py logs/twin_all_B12 logs/twin_roundrobin_B2 logs/twin_uncertainty_B2
```
Full matrix (B = 1, 2, 4 for both policies, ~12 min): `./sweep_week2.sh` -> `logs/twin_compare.png`.

Per-run outputs: `ground_truth.csv`, `twin_state.csv`, `polls.csv`, `events.csv`, `meta.json`,
`twin_vs_truth.png`, `twin_summary.json`.

## Step 2 – calibrate eps (do this once, then re-run)

```bash
python3 calibrate_twin.py logs/twin_all_B12 --q 0.99
TWIN_SIGMA=<suggested> EPS_MEAS=<suggested> ./run_twin.sh uncertainty 2
```
Interpretation: `coverage_agg` should be roughly 0.95+ when eps is well calibrated. Square-wave
traffic has a heavy tail (a step is ~1.0 in one slot), so treat the fit as a starting point and
report coverage honestly. Real bursts cannot be covered by any small sigma without wasting polls - that is exactly the
telemetry-vs-safety trade-off the paper is about.

## Metrics (aggregation ports)

`mae_agg`, `max_err_agg`, `coverage_agg`, `mean_eps_agg`, `mean_aoi_agg_s`, `fidelity_mean`, `polls_per_s`,
`detect_lag_s` (burst start -> twin first reports util > 0.8; staleness turned into a control delay), `bursts_missed`.

## Pitfalls

| Symptom | Fix |
|---|---|
| `ground_truth.csv` empty / no rows | interface names are `<switch>-eth<port>`; check `ls /sys/class/net | grep sA2`; run experiment with sudo |
| Twin never updates | check `polls.csv` grows and `ryu.log` for errors; single-port request needs OF1.3 |
| Huge error at start | cold start; analysis skips the first 5 s after probe start (`--skip`); raise `--warmup` for low budgets |
| Reroute never happens at B=1 | expected sometimes - the twin never saw the burst (`bursts_missed`); this is a result, not a bug |
| matplotlib missing | `sudo apt install python3-matplotlib` |

## Week-2 plan (7 days, 3 people)

| Day | Controller/Mininet | Twin/safety | Eval lead |
|---|---|---|---|
| 1 | Merge files, ground-truth sampler works | `test_twin.py` passes, read `twin.py` | Step-0 dry run, check plots |
| 2 | First real `all 12` run | Verify twin_state.csv sane | Run analyze_twin on it |
| 3 | Bursty scenario stable | Tune EPS_MEAS/SIGMA via calibrate | Pick final params, document |
| 4 | Run roundrobin + uncertainty B=2 | Add `bounds()` usage sketch for Week 3 | Per-run plots |
| 5 | Run full sweep | Review scheduler; optional score variants | Compare plot, table |
| 6 | Demo run + video | Prepare Week-3 safety interface | Report section |
| 7 | Freeze, tag `week2` | | Week-2 writeup |

## Definition of done

- [ ] `test_twin.py` passes; synthetic dry run produces plots
- [ ] real run: `twin_vs_truth.png` shows divergence + refresh markers + eps band
- [ ] roundrobin vs uncertainty at equal budget compared (`twin_compare.png`, table)
- [ ] eps calibrated and coverage reported
- [ ] git tag `week2`, short demo video
