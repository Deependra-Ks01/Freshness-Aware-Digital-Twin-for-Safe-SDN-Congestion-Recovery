# Scoped project: “Freshness-Aware Digital Twin for Safe SDN Congestion Recovery”

**Core idea kept from the paper:** the twin is never fully synchronized; each telemetry source has an age and an uncertainty radius; a deterministic safety validator only allows a reroute/reconfiguration if it is safe *under the worst case in that uncertainty region* — so a stale twin forces more conservative action, and refreshing telemetry has a measurable cost/benefit (VoTI).

## What’s kept vs. simplified vs. dropped, and why

| Paper component | Decision | Why |
| --- | --- | --- |
| SDN control loop, path/bandwidth actions | **Keep** | This is the graded skill — real Ryu app on real Mininet topology. |
| Twin freshness (AoI) + uncertainty radius ε_m(t) | **Keep** | Cheap to implement (a timer + growth function per link), and it is the paper’s key mechanism. |
| Deterministic safety shield g(t) | **Keep** | Straightforward “check worst-case bound, else fallback” logic — very demoable. |
| Value of Twin Information (VoTI) | **Keep, empirical only** | Compute it post-hoc from logged experiments; do not try to solve it as a live optimization. |
| Adaptive Agentic-AI reasoning depth ρ | **Simplify** | Two tiers only: fast heuristic reroute vs. a “deep” pass that checks more candidate paths or a short lookahead. No real LLM required (one tier can be stubbed as an optional API call if time allows). |
| Multi-domain MARL (D domains, policy learning) | **Simplify** | Two domains maximum, each with a simple rule-based policy or a single small Q-learner — not full decentralized MARL training. |
| Full stochastic game / Lyapunov drift proof | **Drop** | Not verifiable in a month and not needed for an SDN-systems demo. |
| MEC compute placement, service migration | **Drop or stretch** | Add only if Weeks 1–3 finish early. |

**Reference topology (Mininet):** 2 domains × (3 switches + 2–3 hosts) joined by an aggregation link, with one host per domain acting as a “MEC server.” Two flow types are used: a small latency-sensitive flow (ping/UDP, standing in for URLLC) and a bulk flow (`iperf`) throttled with `tc` to create congestion on demand.

## 4-week plan

### Week 1 — Baseline system

- Stand up the Mininet topology and Ryu controller with basic monitoring (periodic OpenFlow port/flow-stat polling).
- Define the two flow types and a congestion-injection script (`tc netem`/rate-limit on the aggregation link).
- Build a baseline controller that reroutes immediately using the latest stats, with no freshness or uncertainty model.
- *Deliverable:* a working baseline demo and a congestion scenario reproducible on command.

### Week 2 — Digital twin module

- Create a Python twin object per link/port: last-update timestamp → AoI; uncertainty ε resets small on refresh and grows over time otherwise.
- Set a telemetry budget that caps the links polled per control interval, forcing selective refresh and mirroring the paper’s B^tel constraint.
- *Deliverable:* a plot of estimated versus ground-truth link state over time, showing divergence when telemetry is not refreshed.

### Week 3 — Safety validator + tiered reasoning

- Before deploying a reroute, evaluate predicted latency/capacity at the *worst case* inside the uncertainty region; reject it and fall back to a safe default if it fails.
- Use fast and deep tiers: fast evaluates one default alternate path; deep evaluates all candidate paths and triggers only if fast fails the safety check or the margin is thin. Enforce a hard deadline, aborting slow deep reasoning and falling back to safe/fast behavior.
- *Deliverable:* a closed-loop demo — inject congestion, watch the controller refresh selectively, validate safety, then reroute or fall back.

### Week 4 — Evaluation and writeup

- Compare three policies: always poll everything, fixed periodic polling, and adaptive/uncertainty-triggered polling.
- Measure SLA violations, telemetry overhead (number of polls), flow-table churn (reconfiguration cost), safety-shield trigger rate, and empirical VoTI per link (objective improvement from refreshing ÷ polling cost).
- Produce plots/tables, the report, and a demo video or live run.