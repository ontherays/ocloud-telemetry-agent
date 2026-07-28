# O-Cloud Energy Measurement — Findings

**Note on this report.** Section 1 describes how the data was collected and why
the capture method changed part-way through the campaign. Sections 2–4 present
the verified dataset and its analysis. **Section 5 corrects an earlier finding
that did not survive controlled re-measurement** — recorded deliberately rather
than quietly dropped.

---

## 1. Method

### 1.1 Testbed

| Item | Value |
|---|---|
| Node | joule — StarlingX RT worker, dual Xeon |
| DU socket | socket 1 → RAPL domain **package-1** |
| Control socket | socket 0 → RAPL domain **package-0** (no workload) |
| Instrument | `ocloud-telemetry-agent` — RAPL energy, PMU (IPC/MPKI, memory BW), cpuidle occupancy, per-thread CPU, Redfish chassis power |
| Stacks compared | **OCUDU 2026.04** (srsRAN-derived, Split 7.2x) and **OAI** (`nr-softmodem`) |
| Data pipeline | joule captures → Galileo ships → InfluxDB bucket `infra-telemetry` → Grafana / rApp |

**Collector availability differs by stack.** OAI runs are captured with the PMU
collectors disabled (see F7), so OAI has **no** `ocloud_perf` (IPC/MPKI) or
`ocloud_membw` data. Energy and occupancy — the quantities this report depends
on — are unaffected.

### 1.2 Controlled conditions

Energy is measured under four states. Each changes exactly one thing, so the
*differences* isolate individual costs:

| Condition | State | The difference isolates |
|---|---|---|
| A | no gNB running | platform floor |
| B | gNB up, no UE | DU static cost = B − A |
| C | gNB up, UE attached, **no traffic** | UE attach cost = C − B |
| D | gNB up, UE + iperf at rate R | traffic cost = D − C |

A single "measure whatever is running" reading cannot produce any of these
quantities; only the controlled differences can.

### 1.3 Why the method changed

Earlier capture rounds produced inconsistent results. Root causes, all
identified:

1. **Labels were free text.** The agent cannot observe the iperf rate (traffic
   is generated on a different host) nor which gNB stack is running (only the
   process name it is told to look for). One batch labelled `D-oai-iperf-100M`
   was in fact OCUDU at 200 Mbps.
2. **Configuration changed between conditions.** Different radios (TM500 2×2 vs
   Pegatron 4×4), different core pinning, and different log levels appeared
   within what was recorded as a single ladder.
3. **Traffic timing was open-loop.** iperf was started separately from the
   captures, so some 30 s windows did not overlap live traffic.

**Fix — all captures in this report use `capture.sh`**, which verifies the
system state *before* capturing and refuses to proceed on a mismatch:

- the other stack running → abort (prevents mixing OCUDU and OAI)
- condition A with a gNB process present → abort
- conditions B/C/D with no gNB process → abort
- B/C with iperf running locally → abort
- C/D → explicit operator confirmation of UE attachment / live traffic

It also sets the correct environment per stack automatically and takes **3 runs
per condition**, reporting each run and the group mean.

### 1.4 How values are computed

Two levels of averaging:

1. **Within a run** — mean of all RAPL samples in that run's `power.csv`.
2. **Across runs** — mean of the three per-run means.

Verified by test case: samples of 70 / 80 / 90 W return 80.00, not 90.00 — i.e.
every sample is read, not only the last.

---

## 2. Results — verified dataset (2026-07-24)

All values are package-1 (DU socket) watts. `pkg0` is the control socket.

### 2.1 OAI

| Condition | run 1 | run 2 | run 3 | **mean** | spread | pkg0 |
|---|---|---|---|---|---|---|
| A — no gNB | 66.36 | 66.36 | 66.35 | **66.36** | 0.01 | 64.24 |
| B — gNB, no UE | 78.48 | 78.51 | 78.60 | **78.53** | 0.12 | 64.30 |
| C — UE attached | 78.67 | 78.65 | 78.64 | **78.65** | 0.03 | 64.20 |
| D — iperf 100 | 80.08 | 80.13 | 80.11 | **80.11** | 0.05 | 64.37 |

### 2.2 OCUDU

| Condition | run 1 | run 2 | run 3 | **mean** | spread | pkg0 |
|---|---|---|---|---|---|---|
| A — no gNB | *(shared platform floor)* | | | **66.36** | — | — |
| B — gNB, no UE | 86.76 | 86.82 | 86.66 | **86.75** | 0.16 | 64.16 |
| C — UE attached | 87.04 | 87.09 | 87.04 | **87.06** | 0.05 | 64.26 |
| D — iperf 100M | 90.36 | 90.40 | 88.66 | **89.81** | **1.74** ⚠ | 64.37 |
| D — iperf 200M | 91.39 | 91.41 | 91.36 | **91.39** | 0.05 | 64.49 |

⚠ The 100M spread is an order of magnitude larger than every other condition —
see §6.1.

### 2.3 Control-socket validation

pkg0 stayed within **64.07 – 64.50 W** across all 21 runs — a total range of
0.43 W while pkg1 moved over 25 W. The rise on package-1 is therefore
attributable to the DU on socket 1, not to system-wide drift.

---

## 3. Analysis — derived costs

### 3.1 OAI

| Step | Δ (W) | Interpretation |
|---|---|---|
| A → B | **+12.17** | DU static cost |
| B → C | **+0.12** | UE attach — *within run-to-run spread* |
| C → D | **+1.45** | 100 Mbps traffic |
| **A → D** | **+13.75** | total to serve one UE at 100 Mbps |

### 3.2 OCUDU

| Step | Δ (W) | Interpretation |
|---|---|---|
| A → B | **+20.39** | DU static cost |
| B → C | **+0.31** | UE attach — *within run-to-run spread* |
| C → D100 | **+2.75** | first 100 Mbps |
| D100 → D200 | **+1.58** | second 100 Mbps |
| **A → D200** | **+25.03** | total at 200 Mbps |

### 3.3 Side by side

| Cost component | OAI | OCUDU | ratio |
|---|---|---|---|
| DU static (A→B) | 12.17 W | 20.39 W | OCUDU **1.7×** |
| UE attach (B→C) | 0.12 W | 0.31 W | both ≈ 0 |
| First 100 Mbps | 1.45 W | 2.75 W | OCUDU 1.9× |
| **Total to serve 100 Mbps** | **13.75 W** | **23.45 W** | OCUDU **1.7×** |

---

## 4. Findings

### F1 — The DU's static cost dominates everything else

Simply *running* the DU, with no UE and no traffic, accounts for the
overwhelming majority of energy above the platform floor:

- **OAI:** 12.17 of 13.75 W = **88 %**
- **OCUDU:** 20.39 of 23.45 W = **87 %**

Attaching a UE and carrying 100 Mbps together add only ~12 %. The DU is
essentially an always-on cost.

### F2 — Attaching a UE is energetically free (both stacks)

B → C is **+0.12 W (OAI)** and **+0.31 W (OCUDU)**. Both are comparable to the
run-to-run spread *within* a single condition (0.01–0.16 W), so neither is a
confidently non-zero effect.

This is mechanistically consistent: the DU transmits the full downlink frame
structure — synchronisation signals, reference signals, control channels —
whether or not a UE is attached. One idle UE changes almost nothing about what
the radio is already doing.

### F3 — Traffic is cheap, and gets cheaper

OCUDU load response:

| offered load | pkg1 (W) | marginal Δ | W per 100 Mbps |
|---|---|---|---|
| 0 (attached) | 87.06 | — | — |
| 100 Mbps | 89.81 | +2.75 | 2.75 |
| 200 Mbps | 91.39 | +1.58 | 1.58 |

The second 100 Mbps costs **43 % less** than the first. Energy-vs-load is
**sub-linear and flattening**, consistent with F1: the fixed radio cost
dominates and marginal energy per bit falls as load rises.

### F4 — OAI serves the same load for ~40 % less energy

OAI delivers 100 Mbps to one UE for **+13.75 W** over the platform floor;
OCUDU needs **+23.45 W** — **1.7×** as much for the same service. The gap
originates almost entirely in the **static** cost (12.17 vs 20.39 W), not in
traffic handling.

*Caveat:* the two stacks were not configured identically (§6.2). This compares
two working deployments, not two identically-tuned ones.

### F5 — Energy efficiency is strongly load-dependent

Expressed in the 3GPP TS 28.554 §6.7 EE-KPI form (throughput ÷ incremental
power over the platform floor):

| Stack | load | incremental W | Mbit/s per W |
|---|---|---|---|
| OAI | 100 Mbps | 13.75 | **7.3** |
| OCUDU | 100 Mbps | 23.45 | **4.3** |
| OCUDU | 200 Mbps | 25.03 | **8.0** |

Two observations: OAI is ~1.7× more efficient at 100 Mbps, and **OCUDU's
efficiency nearly doubles when load doubles** (4.3 → 8.0 Mbit/s/W) because the
fixed cost is amortised over more traffic. A DU at low utilisation is very
inefficient per bit.

### F6 — CPU occupancy does not track energy, and fails differently per stack

Measured occupancy on the isolated DU cores at idle (condition B):

| Stack | occupancy | behaviour |
|---|---|---|
| OCUDU | ~0 % (cores park in C1) | timer-driven; occupancy **under**-reads |
| OAI | **cpu13 / cpu15 = 100 %** | DPDK busy-poll; occupancy **over**-reads |

OAI's per-thread breakdown at idle (condition B, no UE, no traffic):

| Core | OAI thread | busy % |
|---|---|---|
| cpu13 | `fh_main_poll` | **100.0 %** |
| cpu15 | `fh_rx_bbdev` | **100.0 %** |
| cpu5 | `L1_tx_thread` | ~14–15 % |
| cpu3 | `L1_rx_thread` | ~6 % |
| cpu7 | `ru_thread` | ~2–3 % |
| cpu9, 11, 17–31 | (idle) | ~0 % |

Two fronthaul threads busy-poll at a full 100 % with nothing attached and no
traffic — DPDK busy-poll in its purest form: two cores spinning continuously,
independent of load.

An estimator assuming power ∝ CPU-time (3GPP TS 28.554 §6.7.3.1.4; O-RAN
O-Cloud-ES) fails on **both** stacks, in opposite directions:

- **OCUDU:** sees ~0 % → predicts "idle, cheap" → actually 86.75 W.
- **OAI:** sees 100 % on two cores → predicts "fully loaded" → actually idle.

Neither reflects real work. **Scheduler-visible CPU occupancy is not a valid
energy or load proxy for a poll-mode Split 7.2x O-DU, and the direction of its
error is implementation-dependent.** RAPL energy is the faithful signal for both.

### F7 — OAI monopolises the PMU (interoperability finding)

OAI's O1 telemetry (launched with `--telnetsrv.shrmod o1`) **continuously spawns
`perf stat -e instructions -C 3,5,7,…,31`** against the isolated DU cores. These
processes can get stuck: 3–6 zombie `perf` processes were observed, and they
respawn after being killed.

Consequences:

- OAI uses hardware performance counters for its own monitoring; **OCUDU does
  not.**
- This **contends with external PMU-based measurement**. The agent's `perf` and
  `uncore` collectors hang during their availability check and must be disabled
  (`ENABLE_PERF=false ENABLE_UNCORE=false`) for every OAI capture.
- Energy (RAPL) and occupancy (cpuidle) are **unaffected**, so the full A/B/C/D
  ladder remains measurable on OAI — but IPC, MPKI and memory bandwidth are not.
- OAI's continuous self-monitoring is itself an energy cost, included in its
  package-1 reading.

This is a concrete **interoperability limitation**: PMU-based O-Cloud telemetry
cannot coexist with OAI's perf-based O1 monitoring without contention. It should
be noted by any deployment intending to measure OAI via hardware counters.

### F8 — Supporting hardware measurements (OCUDU, gNB up)

| Metric | Value | Note |
|---|---|---|
| delivered frequency ratio | ~1.476, steady | no throttling; stable across A–D |
| C6 residency | **0** | DU cores never enter deep sleep (hardware-confirmed) |
| C1 residency | high | cores park in C1, not C6 |
| IPC (idle baseline) | ~0.61 | expected to rise under load — to verify in the sweep |
| memory BW, socket 1 | ~294 R / 226 W MiB/s | the DU socket; socket 0 much lower |

The absence of C6 matters for standards work: O-RAN O-Cloud-ES use cases that
assume per-NF C-state selection are not implementable on this platform, because
the low-latency profile removes the deep C-states they rely on.

---

## 5. Correction to an earlier finding

**Superseded claim.** An earlier analysis (2026-07-22 data) reported a UE-attach
cost of **+9.9 W** for OCUDU (B = 76.83 → C = 86.68 W) and concluded that
attaching an idle UE cost nearly as much as running the DU itself.

**This does not survive controlled re-measurement.** Under state-verified
capture with a single unchanged configuration, OCUDU's attach cost is
**+0.31 W** (§3.2).

**Probable cause of the earlier result.** The 2026-07-22 B value (76.83 W) and
this session's B value (86.75 W) differ by ~10 W — approximately the size of the
spurious "attach cost". Moreover the earlier C value (86.68 W) is very close to
the current B value (86.75 W). The most likely explanation is that **the gNB
configuration changed between capturing B and C** in the earlier session, so the
computed difference measured a configuration change rather than UE attachment.

**Lesson.** A difference between two conditions is meaningful only if everything
except the intended variable is held constant. The state verification and
single-session discipline of §1.3 exist to enforce exactly this. Per-run
configuration archiving is what made the discrepancy diagnosable rather than
merely confusing.

---

## 6. Data-quality observations

### 6.1 One outlier in OCUDU D-100M

| | |
|---|---|
| runs | 90.36, 90.40, **88.66** |
| mean | 89.81 |
| median | 90.36 |
| spread | **1.74 W** (all other conditions: 0.01–0.16 W) |

The third run is 1.7 W below the other two, with a spread ~10× every other
condition. Most plausible cause: the iperf stream ended or stalled part-way
through that 30 s window, so the capture partly measured condition C rather than
D. Excluding it gives 90.38 W, which would raise the traffic cost from +2.75 to
+3.32 W.

**Treatment:** the run is retained and reported, and the anomaly flagged rather
than silently removed. The 100 Mbps figure should be re-measured with a longer
iperf duration before being quoted as final.

### 6.2 OCUDU configuration differs from the earlier TM500 ladder

This session's OCUDU B (86.75 W) matches the earlier Pegatron-style
configuration (86.89 W) rather than the TM500 2×2 configuration (76.83 W). The
OAI and OCUDU numbers therefore compare **two working deployments**, not two
identically-parameterised ones. The exact OCUDU radio/config for this session
should be read from the archived `gnb-config.yml` before F4 is quoted as a
like-for-like stack comparison.

### 6.3 Slight control-socket drift under load

pkg0 sits at 64.16–64.30 W in conditions B and C, and 64.37–64.49 W in the D
conditions — a ~0.2 W rise correlated with traffic, plausibly kernel
network-stack work on the housekeeping socket. It is small (0.8 % of the pkg1
change) and does not affect the conclusions, but the control socket is not
perfectly inert under traffic.

### 6.4 The `threads` collector as an independent state verifier

The agent's `threads` collector logs either
`collector threads SKIPPED: no process matching (<name>)` or lists `threads`
among the active collectors. This is a **machine-generated record of whether the
named gNB process existed during the capture**, independent of the operator's
label.

It caught two mislabelled runs in an earlier session: two runs labelled
`A-oai-no-gnb` read 79.7 W (far above the 66.4 W floor) and showed `threads`
*active* — the gNB was still running. Energy alone could not have proven the
label wrong; this flag could.

**Important limitation.** The flag verifies only that *the process named in
`GNB_COMM`* was absent — **not** that no gNB was running. In one batch,
`GNB_COMM=nr-softmodem` was passed while OCUDU (process `gnb`) was running, so
the collector skipped on a **name mismatch** and per-thread data was lost for
those runs. The flag is a valid state check **only when `GNB_COMM` matches the
stack under test** — which `capture.sh` now guarantees by setting it from the
`stack` argument.

### 6.5 Idle-capture flood in the InfluxDB bucket

Approximately **1600 condition-A runs** exist from an earlier period of
continuous `run_campaign.sh --watch` operation. These give excellent statistics
for the platform floor, but they numerically dominate the bucket and bury the
deliberate captures. The bucket should be cleaned (or the deliberate runs tagged
distinctly) before it is used for dashboards or rApp queries.

### 6.6 Condition A captured once, for OAI only

A is a property of the node with no gNB running, so it is stack-independent and
reused for both ladders. The OAI A measurement (66.36 W, spread 0.01) agrees
with all prior A measurements (66.32–66.44 W across ~1600 earlier runs), so the
reuse is justified — though a per-session A is preferable.

---

## 7. Summary

| | OAI | OCUDU |
|---|---|---|
| A — platform floor | 66.36 W | 66.36 W |
| B — DU running, no UE | 78.53 W (**+12.17**) | 86.75 W (**+20.39**) |
| C — UE attached | 78.65 W (**+0.12**) | 87.06 W (**+0.31**) |
| D — 100 Mbps | 80.11 W (**+1.45**) | 89.81 W (**+2.75**) |
| D — 200 Mbps | — | 91.39 W (**+1.58**) |
| **Total to serve 100 Mbps** | **+13.75 W** | **+23.45 W** |
| Efficiency at 100 Mbps | **7.3 Mbit/s/W** | **4.3 Mbit/s/W** |
| Occupancy failure mode | over-reads (100 % busy-poll) | under-reads (~0 %) |
| PMU available to external telemetry | **no** (OAI holds it, F7) | yes |

**In one sentence:** the DU's static cost accounts for ~88 % of its energy
footprint, attaching a UE is essentially free, additional traffic is cheap and
gets cheaper, OAI serves the same load for ~40 % less power than OCUDU, and CPU
occupancy misreports all of it — in opposite directions on the two stacks.

---

---

# PART III — PEGATRON RU + SAMSUNG UE (OCUDU, full load sweep to 1 Gbps)

**Radio:** Pegatron RU, 4×4. **UE:** Samsung (commercial handset). **Stack:**
OCUDU. **Session:** 2026-07-27. A **separate ladder** — different radio and UE
from Parts I–II — so its absolute numbers are not comparable to the TM500 or OAI
ladders. This is the campaign's most complete dataset: A, B, C and a **ten-point
traffic sweep from 100 Mbps to 1 Gbps**, 3 runs per point.

## 9. Data selection and label correction

Two clean-up steps were applied before analysis:

1. **Mislabelled C-with-rate runs → D.** Condition C means *no traffic*, and
   `capture.sh` ignores the rate argument for C — so runs entered as
   `capture.sh ocudu C 100M` (…200M, 300M, 400M) were written with the directory
   label `C-ocudu-ue-noTraffic` while iperf was running. Their energy (92–96 W,
   vs 87 W for a true no-traffic C) confirms they are **D** runs; they are
   reassigned by the command rate. The plain `ocudu C` run (87.04 W) is the true C.

2. **Single-session, tightest-group selection.** `analyze.sh` groups by label
   across *all* sessions, which mixes the earlier TM500 configuration into some
   labels (e.g. the `C` group aggregated 18 runs spanning three different
   states). For this ladder **only 2026-07-27 Pegatron runs are used**, and for
   each rate the consistent 3-run group is taken (intra-group spread ≤ 0.27 W).

## 10. Pegatron + Samsung energy ladder (full sweep)

| Condition | offered | **achieved DL** | loss % | pkg1 (W) | Δ prev |
|---|---|---|---|---|---|
| A — no gNB | — | — | — | 66.30 | — |
| B — gNB, no UE | — | — | — | 86.80 | **+20.50** |
| C — UE attached | 0 | 0 | — | 87.04 | **+0.24** |
| D | 100 Mbps | **100** | 0 | 92.01 | +4.97 |
| D | 200 Mbps | **200** | 0 | 93.18 | +1.17 |
| D | 300 Mbps | **300** | 0 | 94.66 | +1.48 |
| D | 400 Mbps | **400** | 0 | 95.63 | +0.97 |
| D | 500 Mbps | **500** | 0 | 96.48 | +0.85 |
| D | 600 Mbps | **600** | 0 | 97.12 | +0.64 |
| D | 700 Mbps | **700** | 0 | 97.50 | +0.38 |
| D | 800 Mbps | **800** | 0.003 | 98.41 | +0.91 |
| D | 900 Mbps | **900** | 0.048 | 99.34 | +0.93 |
| D | 1000 Mbps | **919** ⚠ | **8.1** | 99.57 | +0.23 |

Achieved DL throughput is the iperf3 **receiver** bitrate (UDP, `-l 1200`,
120 s). For 100–900 Mbps, achieved = offered with negligible loss (≤ 0.05 %).
⚠ **At 1000 Mbps offered, the link saturates: achieved is 919 Mbps with 8.1 %
loss** — the Samsung UE / radio delivered-throughput ceiling on this cell.

Every point is 3 runs, intra-group spread ≤ 0.27 W. pkg0 (control) stays
64.26–64.82 W across all 39 runs; the pkg1 rise 66 → 99.6 W is the DU.
**Total A → line rate (~919 Mbps) = +33.27 W.**

## 11. F9 — Energy-vs-load is dominated by fixed cost; traffic is sub-linear

Decomposition of the 1 Gbps serving cost:

| component | Δ (W) | share of total (+33.27) |
|---|---|---|
| DU static (A→B) | 20.50 | **62 %** |
| UE attach (B→C) | 0.24 | ~1 % (≈ free) |
| Traffic 0 → 1 Gbps (C→D1000) | 12.53 | 38 % |

Even at 1 Gbps — ten times the earlier tests — the **fixed DU cost (62 %) still
dominates**, the UE attach is free (+0.24 W, consistent with every stack so far),
and a full gigabit of traffic adds only 12.5 W.

**Marginal energy per 100 Mbps** falls sharply and stays low:

| step | Δ (W/100 Mbps) | step | Δ (W/100 Mbps) |
|---|---|---|---|
| C → 100M | 4.97 | 500 → 600M | 0.64 |
| 100 → 200M | 1.17 | 600 → 700M | 0.38 |
| 200 → 300M | 1.48 | 700 → 800M | 0.91 |
| 300 → 400M | 0.97 | 800 → 900M | 0.93 |
| 400 → 500M | 0.85 | 900 → 1000M | 0.23 |

The first 100 Mbps costs ~5 W; every subsequent 100 Mbps costs **≤ 1.5 W**, i.e.
at least 3× cheaper, and often far cheaper. **Traffic energy is strongly
sub-linear across the entire 0–1 Gbps range.**

*Non-monotonicity note.* The marginal cost is not perfectly monotonic — it dips
to +0.38 W at 700 Mbps then rises to ~+0.9 W at 800–900 Mbps. This ~0.5 W
wobble is small (comparable to a few times the run spread) and does not change
the overall sub-linear trend, but it is reported rather than smoothed. It may
reflect a scheduling/MCS transition around that load, or offered-vs-achieved
differences (below).

## 12. F10 — Efficiency vs achieved throughput (measured)

With achieved DL throughput now recorded, efficiency is computed against
**delivered** bits (Mbit/s per incremental watt over the platform floor):

| achieved DL | incremental W | Mbit/s per W |
|---|---|---|
| 100 Mbps | 25.71 | 3.9 |
| 300 Mbps | 28.36 | 10.6 |
| 500 Mbps | 30.18 | 16.6 |
| 700 Mbps | 31.20 | 22.4 |
| 900 Mbps | 33.04 | 27.2 |
| **919 Mbps (max)** | 33.27 | **27.6** |

Efficiency rises ~7× from 100 Mbps to line rate as the +20.5 W fixed cost is
amortised. At the cell's delivered ceiling (~919 Mbps) the DU reaches
**27.6 Mbit/s/W**.

**F10 — the top-end energy plateau is throughput saturation, not energy
saturation.** The earlier open question is resolved by the achieved-rate data.
Offered load of 1000 Mbps produced only 919 Mbps achieved with 8.1 % UDP loss;
the matching energy step (900 → 1000 Mbps offered) was just +0.23 W —
consistent with the +19 Mbps of *actual* additional throughput delivered. Below
saturation (100–900 Mbps, ≤ 0.05 % loss) achieved equals offered, so the energy
curve there is a genuine energy-vs-load relationship; only the final point is
saturation-limited. This confirms F9's sub-linear trend is real up to ~900 Mbps,
and identifies **~919 Mbps as the delivered-throughput ceiling** of this
Pegatron + Samsung configuration.

**Practical implication (F5 confirmed at scale):** a Split 7.2x DU is ~7× more
energy-efficient per delivered bit at line rate than at 100 Mbps. For an
energy-proportional RAN, consolidating traffic onto fewer, well-utilised DUs is
far preferable to spreading it across many lightly-loaded ones — a lightly-loaded
DU wastes most of its 20.5 W fixed cost.


---

# PART IV — PEGATRON RU + SAMSUNG UE (OAI, load sweep, DL)

**Radio:** Pegatron RU. **UE:** Samsung. **Stack:** OAI (`nr-softmodem`).
**Session:** 2026-07-28. Same radio and UE as Part III, **different RAN stack** —
so this pairs directly with the OCUDU Pegatron sweep for a like-for-like
cross-stack comparison on identical hardware. Captured with PMU collectors
disabled (F7). Downlink; achieved throughput + loss from iperf3 receiver lines.

> **Incomplete by design.** The sweep currently runs to 800 Mbps offered. The
> OAI DL link saturates at ~700–720 Mbps on this cell (see F11); 800/900/1000 M
> will be re-tested after the current OAI gNB limit around 720 Mbps is addressed.

## 13. OAI + Pegatron energy ladder (DL)

| Condition | offered | **achieved DL** | loss % | pkg1 (W) | Δ prev |
|---|---|---|---|---|---|
| A — no gNB | — | — | — | 66.30 | — |
| B — gNB, no UE | — | — | — | 80.55 | **+14.25** |
| C — UE attached | 0 | 0 | — | 80.56 | **+0.01** |
| D | 100 Mbps | **100** | 0 | 82.21 | +1.65 |
| D | 200 Mbps | **200** | 0 | 83.04 | +0.83 |
| D | 300 Mbps | **300** | 0.001 | 84.14 | +1.10 |
| D | 400 Mbps | **400** | 0.001 | 84.97 | +0.83 |
| D | 500 Mbps | **500** | 0 | 85.56 | +0.59 |
| D | 600 Mbps | **600** | 0 | 86.53 | +0.97 |
| D | 700 Mbps | **698** | 0.37 | 87.29 | +0.76 |
| D | 800 Mbps | **721** ⚠ | **9.8** | 87.65 | +0.36 |

Every point is 3 runs, intra-group spread ≤ 0.19 W. pkg0 (control) stays
64.39–64.64 W across all runs. Condition A reuses the stack-independent platform
floor (66.30 W).
⚠ At 800 Mbps offered the OAI DL link saturates: achieved 721 Mbps, 9.8 % loss.

## 14. F11 — OAI has a lower delivered-throughput ceiling than OCUDU

On the **same Pegatron RU and Samsung UE**, the two stacks reach different DL
ceilings:

| Stack | achieved at 700 M offered | achieved at 800 M offered | loss at 800 M | ceiling |
|---|---|---|---|---|
| **OCUDU** | 700 (0 %) | 800 (0.003 %) | negligible | **~919 Mbps** (Part III) |
| **OAI** | 698 (0.37 %) | 721 (9.8 %) | heavy | **~700–720 Mbps** |

OAI loses 9.8 % of a 800 Mbps offered load where OCUDU carries it cleanly. OAI's
DL throughput saturates ~200 Mbps *lower* than OCUDU on identical radio hardware.
This is consistent with the independent finding (TNSM, Guemdani et al., same
OCUDU 2026.04) that **OAI's DL deficit localises to the DU-side MAC/RLC stack** —
reduced PRB allocation, lower MCS, RLC buffer saturation under sustained load.
Here that deficit appears as a lower delivered-throughput ceiling, and the
energy curve flattens against it (F12).

## 15. F12 — OAI energy-vs-load: same sub-linear shape, lower fixed cost

Cost decomposition (to 700 Mbps, the clean pre-saturation range):

| component | OAI | OCUDU (Part III) |
|---|---|---|
| DU static (A→B) | **+14.25 W** | +20.50 W |
| UE attach (B→C) | +0.01 W (≈ free) | +0.24 W (≈ free) |
| Traffic 0→700 Mbps (C→700M) | +6.73 W | +10.46 W |

Both stacks show the same structure — a large fixed cost, a free UE attach, and
sub-linear traffic — but **OAI's fixed DU cost is ~6 W lower** (14.25 vs 20.50 W).
Marginal energy per 100 Mbps (OAI): 1.65 → 0.83 → 1.10 → 0.83 → 0.59 → 0.97 →
0.76 W, then +0.36 W into saturation. The +0.36 W at "800 M" reflects only the
+23 Mbps of *actual* additional throughput delivered (698 → 721), not +100 —
throughput saturation, exactly as in OCUDU's F10.

## 16. F13 — Efficiency vs achieved throughput (OAI, DL)

| achieved DL | incremental W | Mbit/s per W |
|---|---|---|
| 100 Mbps | 15.91 | 6.3 |
| 300 Mbps | 17.84 | 16.8 |
| 500 Mbps | 19.26 | 26.0 |
| 700 Mbps | 20.99 | 33.3 |
| **721 Mbps (max)** | 21.35 | **33.8** |

At its ~721 Mbps ceiling OAI reaches **33.8 Mbit/s/W** — higher than OCUDU's
27.6 Mbit/s/W at 919 Mbps, because OAI's fixed cost is lower. But OCUDU delivers
~200 Mbps more absolute throughput. The trade-off: **OAI is more
energy-efficient per bit within its narrower throughput envelope; OCUDU reaches
a higher throughput ceiling at higher fixed energy cost.**

## 17. Cross-stack summary on identical hardware (Pegatron + Samsung, DL)

| | OAI | OCUDU |
|---|---|---|
| Platform floor (A) | 66.30 W | 66.30 W |
| DU static (A→B) | **+14.25 W** | +20.50 W |
| UE attach (B→C) | +0.01 W | +0.24 W |
| Delivered-throughput ceiling | **~720 Mbps** | **~919 Mbps** |
| Energy at ~700 Mbps | 87.29 W | 97.50 W |
| Peak efficiency | 33.8 Mbit/s/W @721 M | 27.6 Mbit/s/W @919 M |
| PMU available to telemetry | no (F7) | yes |

**In one line:** on identical Pegatron + Samsung hardware, OAI runs ~6 W cheaper
static and is more efficient per bit, but saturates ~200 Mbps lower than OCUDU —
its DU-side MAC/RLC ceiling caps both throughput and the reachable energy range.

---

## 8. Next steps

1. **Re-measure OCUDU D-100M** with a longer iperf run to resolve the outlier
   (§6.1).
2. **Record both stacks' configurations** for this session so F4 can be stated
   as like-for-like or explicitly qualified (§6.2).
3. **Extend the load sweep on other radios** (TM500, OAI) to match the Pegatron
   0–1 Gbps coverage now completed in Part III.
4. **Capture per-session condition A** for each ladder rather than reusing one.
5. **Measure occupancy under load** for both stacks to complete F6 — confirm
   OAI's poll threads stay pinned at 100 % while energy rises, i.e. that
   occupancy cannot distinguish idle from loaded.
6. **Fix the agent's default `GNB_CONFIG_PATH`** — currently `/mnt/gnb_runtime`
   (underscore) but the real directory is `/mnt/gnb-runtime` (hyphen), so config
   archiving silently fails unless the path is passed explicitly.
7. **Clean the InfluxDB bucket** of the ~1600 idle A-runs (§6.5) so deliberate
   captures are usable for dashboards and rApp queries.
8. **Record IPC/MPKI under load for OCUDU** (available there, unlike OAI) to
   test whether compute-per-cycle rises while occupancy stays flat — the
   mechanism behind F6.
9. **DONE for Pegatron** — achieved DL throughput + UDP loss now recorded per
   step (§10, F10); the ~919 Mbps ceiling and 8.1 % loss at 1 Gbps offered are
   captured. Apply the same achieved-rate logging to the TM500 and OAI sweeps.
10. **Fix `capture.sh` to fold the rate into the label for condition C** (or
    reject a rate on C), so a C-with-rate run cannot be silently mislabelled
    (§9).
11. **Probe beyond the ceilings** — OCUDU tops out ~919 Mbps and OAI ~720 Mbps
    on this cell. A higher-capability UE or DL-oriented TDD pattern would extend
    both curves.
12. **Complete the OAI Pegatron sweep to 800/900/1000 Mbps** once the OAI DL
    limit around 720 Mbps is resolved (Part IV is currently to 800 M offered /
    721 M achieved).
13. **Add uplink sweeps** for both stacks (UE as iperf client, core as server);
    label captures distinctly (e.g. `D-<stack>-UL-<rate>M`) so UL and DL do not
    mix in analysis.