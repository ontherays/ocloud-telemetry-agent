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
see  6.1.

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

*Caveat:* the two stacks were not configured identically ( 6.2). This compares
two working deployments, not two identically-tuned ones.

### F5 — Energy efficiency is strongly load-dependent

Expressed in the 3GPP TS 28.554  6.7 EE-KPI form (throughput ÷ incremental
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

An estimator assuming power ∝ CPU-time (3GPP TS 28.554  6.7.3.1.4; O-RAN
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
**+0.31 W** ( 3.2).

**Probable cause of the earlier result.** The 2026-07-22 B value (76.83 W) and
this session's B value (86.75 W) differ by ~10 W — approximately the size of the
spurious "attach cost". Moreover the earlier C value (86.68 W) is very close to
the current B value (86.75 W). The most likely explanation is that **the gNB
configuration changed between capturing B and C** in the earlier session, so the
computed difference measured a configuration change rather than UE attachment.

**Lesson.** A difference between two conditions is meaningful only if everything
except the intended variable is held constant. The state verification and
single-session discipline of  1.3 exist to enforce exactly this. Per-run
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

## 8. Next steps

1. **Re-measure OCUDU D-100M** with a longer iperf run to resolve the outlier
   ( 6.1).
2. **Record both stacks' configurations** for this session so F4 can be stated
   as like-for-like or explicitly qualified ( 6.2).
3. **Extend the load sweep** to 500 and 1000 Mbps to confirm F3's flattening
   trend and map the efficiency curve of F5.
4. **Capture per-session condition A** for each ladder rather than reusing one.
5. **Measure occupancy under load** for both stacks to complete F6 — confirm
   OAI's poll threads stay pinned at 100 % while energy rises, i.e. that
   occupancy cannot distinguish idle from loaded.
6. **Fix the agent's default `GNB_CONFIG_PATH`** — currently `/mnt/gnb_runtime`
   (underscore) but the real directory is `/mnt/gnb-runtime` (hyphen), so config
   archiving silently fails unless the path is passed explicitly.
7. **Clean the InfluxDB bucket** of the ~1600 idle A-runs ( 6.5) so deliberate
   captures are usable for dashboards and rApp queries.
8. **Record IPC/MPKI under load for OCUDU** (available there, unlike OAI) to
   test whether compute-per-cycle rises while occupancy stays flat — the
   mechanism behind F6.