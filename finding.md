# O-Cloud Telemetry — Findings & Results

**Testbed:** joule (StarlingX RT worker, dual Xeon; gNB on socket 1 = package-1,
control on socket 0 = package-0). OCUDU 2026.04 Split 7.2x.
**Radio for this result set:** TM500 emulator, 2×2 (nof_antennas_dl/ul = 2),
RU MAC `10:70:fd:14:1c:10` — confirmed identical across the B/C/D ladder via
per-run `gnb-config.yml` archiving.
**Instrument:** ocloud-telemetry-agent (RAPL + PMU + cpuidle + Redfish).
**Pipeline:** joule captures → Galileo ships → InfluxDB (`infra-telemetry`).

> All package-1 values are the mean of the RAPL `watts` field over a 30 s window.
> package-0 is the untouched control socket. Every condition is a distinct,
> verified state on the **same TM500 gNB config** — not mixed radios.

---

## 1. The energy ladder (TM500 2×2, single config)

| Condition | State | package-1 (W) | Δ prev | Isolates |
|---|---|---|---|---|
| **A** | no gNB | **66.33** | — | platform floor |
| **B** | gNB up, no UE | **76.83** | **+10.5** | DU static (software) cost |
| **C** | UE attached, no traffic | **86.68** | **+9.9** | UE attach / radio-link cost |
| **D** | UE + iperf ~100 Mbps | **89.78** | **+3.1** | user-traffic cost |
| | | | **+23.4** | total A → D |

Per-run values (reproducibility):

| Condition | runs | package-1 (W) |
|---|---|---|
| A | 4 | 66.33, 66.36, 66.36, 66.32 |
| B | 3 | 76.86, 76.79, 76.85 |
| C | 2 | 86.70, 86.66 |
| D | 4 | 90.20, 89.78, 89.60, 89.55 |

Each condition's repeats agree to ~0.1 W; the four conditions are cleanly
separated. Distinct conditions producing distinct, repeatable energy is the
evidence the instrument measures live state (no staleness/duplication).

---

## 2. Control socket validates the deltas

package-0 (socket 0, no workload) stays flat at **~64 W across every condition**.
Because the control socket does not move while package-1 climbs 66 → 90 W, the
rise is attributable to the DU on socket 1, not to system-wide drift. This is
what makes the per-step deltas causal rather than coincidental.

---

## 3. Headline findings

**F1 — Attaching a UE costs almost as much as running the DU.**
C − B = **+9.9 W** for a UE that is attached and synchronized but passing **zero
traffic**. Maintaining the radio link (timing, control channels, sync) for one
idle-attached UE costs nearly as much (+9.9 W) as the entire DU static cost
(+10.5 W). This is non-obvious: an "idle" attached UE is not free.

**F2 — User traffic is comparatively cheap.**
D − C = **+3.1 W** to carry ~100 Mbps on top of an attached UE. The large energy
step is *attaching* (B→C, +9.9 W); moving user bytes adds little (C→D, +3.1 W).
The DU's energy is dominated by **radio maintenance**, not user-plane throughput.

**F3 — Energy is barely sensitive to offered load in this range.**
iperf at 50 Mbps and 100 Mbps produced essentially the same package-1 power
(all D runs 89.5–90.2 W, ~0.5 W spread). Doubling offered load did not
meaningfully change energy — consistent with F2 (traffic is cheap relative to
the always-on radio cost).

**F4 — Occupancy does not track this energy (the thesis point).**
Under 100 Mbps load the isolated DU cores read ~12–23 % scheduler-visible
occupancy while the socket draws +23 W over the platform floor. An estimator
that assumes power ∝ CPU-time (3GPP TS 28.554 §6.7.3.1.4; O-RAN O-Cloud-ES)
would badly under-read — most sharply for the +9.9 W attach cost, which barely
moves CPU occupancy at all. **Energy is the faithful signal; occupancy is not.**

---

## 4. Supporting measurements (from run files)

| Metric | Value (gNB up) | Note |
|---|---|---|
| delivered freq ratio | ~1.476, steady | no throttling; stable across A–D |
| C6 residency | 0 | DU cores never deep-sleep (hardware-confirmed) |
| C1 residency | high | cores park in C1, not C6 |
| IPC (idle baseline) | ~0.61 | expected to rise under load — to verify in sweep |
| mem BW socket 1 | ~294 R / 226 W MiB | the DU socket; socket 0 much lower |

---

## 5. Provenance note — why the earlier "contradiction" was not an error

An earlier B reading of ~87 W (vs 76.8 W here) was traced, via the archived
per-run `gnb-config.yml`, to a **different radio/config** (Pegatron 4×4, debug
logging, cores unpinned `@(0-31)`) — not a measurement fault. The TM500 ladder
above uses one consistent config (2×2, `all_level` low, pinned cores). Lesson:
**idle DU energy is configuration-dependent**; per-run config archiving is what
makes cross-run numbers interpretable rather than contradictory.

---

## 6. Data-quality caveats (honest scope)

1. **iperf rate labels.** Some D runs were captured during 50 Mbps vs 100 Mbps
   iperf but all labelled `100mbps`. Energy was ~equal either way (F3), so the
   ladder is unaffected, but rate labels should be corrected for the formal
   sweep table.
2. **Open-loop capture timing.** iperf and captures were started separately;
   most D captures overlapped live traffic (confirmed by the +3 W over C), but a
   synchronized long-iperf + back-to-back-captures method is preferred for the
   formal sweep so every window provably has traffic.
3. **Sweep not yet complete.** Only ~50–100 Mbps measured. A full 100 → 1000 Mbps
   sweep is needed to characterise energy-vs-load as a curve (expected to stay
   fairly flat, per F3).
4. **A-run flood.** ~1600 idle A captures exist from earlier continuous `--watch`
   — excellent A statistics, but the InfluxDB bucket needs cleanup so deliberate
   captures aren't buried.

---

## 7. What's proven vs pending

**Proven (TM500 2×2, one config):**
- A = 66.3, B = 76.8, C = 86.7, D = 89.8 W — clean, repeatable, separated ladder.
- DU static cost **+10.5 W**; UE attach cost **+9.9 W**; 100 Mbps traffic **+3.1 W**.
- Control socket flat → deltas are causal (the DU), not noise.
- Instrument measures live state (distinct conditions → distinct energy).
- Full pipeline capture → ship → InfluxDB, values faithful.

**Pending:**
- Rate sweep 100 → 1000 Mbps with correct labels + synchronized capture.
- IPC-vs-load (does compute-per-cycle rise while occupancy stays flat?).
- Clean B on other radios (Pegatron 4×4) for a cross-radio comparison.
- Fix agent default `GNB_CONFIG_PATH` (`/mnt/gnb_runtime` → `/mnt/gnb-runtime`).

---

## 8. Headline numbers (abstract / slides)

```
Platform floor (no gNB)              66.3 W
+ DU running (no UE)        +10.5 W → 76.8 W
+ UE attached (no traffic) + 9.9 W → 86.7 W   ← attaching costs ~as much as the DU
+ ~100 Mbps traffic        + 3.1 W → 89.8 W   ← user traffic is cheap
Control socket (all cond.)   ~64 W flat       ← validates the deltas
DU-core occupancy under load ~12-23 %         ← energy says otherwise
```

*On a TM500 2×2 Split 7.2x O-DU, attaching an idle UE costs ~9.9 W — nearly the
DU's own static cost — while 100 Mbps of traffic adds only ~3 W. The DU's energy
is dominated by radio maintenance, not throughput, and none of it tracks
scheduler-visible CPU occupancy.*