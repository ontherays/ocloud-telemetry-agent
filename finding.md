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

---

# PART II — CROSS-STACK: OAI gNB (same node, same method)

**Radio/stack:** OAI `nr-softmodem` (OAI gNB), launched with
`--telnetsrv.shrmod o1`, DU on socket 1 (same isolated odd cores as OCUDU).
Measured with **perf/uncore collectors disabled** — see F5 below. RAPL energy
and cpuidle occupancy are unaffected.

## 9. OAI energy — B (gNB up, no UE)

| Metric | OAI | OCUDU (srsRAN) | Δ |
|---|---|---|---|
| package-1 (gNB idle) | **78.49 W** | 76.83 W | **+1.7 W** |
| package-0 control | ~64.2 W flat | ~64.3 W flat | — |

Five OAI runs: 78.46, 78.46, 78.52, 78.44, 78.56 W — stable to 0.1 W. OAI idles
~1.7 W higher than OCUDU; plausibly attributable in part to OAI's continuous
self-monitoring (F5).

## 10. Occupancy signature — the key cross-stack contrast

At **idle** (gNB up, no UE, no traffic), OAI's isolated DU cores read:

| Core | OAI thread | busy % |
|---|---|---|
| cpu13 | `fh_main_poll` | **100.0 %** |
| cpu15 | `fh_rx_bbdev` | **100.0 %** |
| cpu5 | `L1_tx_thread` | ~14–15 % |
| cpu3 | `L1_rx_thread` | ~6 % |
| cpu7 | `ru_thread` | ~2–3 % |
| cpu9,11,17–31 | (idle) | ~0 % |

**OAI pins two fronthaul threads that busy-poll at a full 100 % even with no UE
and no traffic.** This is DPDK busy-poll in its purest form: two cores spin at
100 % continuously, independent of load.

## 11. Headline cross-stack finding — occupancy fails in BOTH directions

| | OCUDU (srsRAN) | OAI |
|---|---|---|
| DU-core occupancy at idle | ~0 % (timer-driven, cores park in C1) | **cpu13/15 = 100 %** (busy-poll) |
| idle energy (B) | 76.8 W | 78.5 W |
| occupancy error mode | **under**-reads (0 % but draws 77 W) | **over**-reads (100 % but doing nothing) |

An energy/load estimator based on scheduler-visible CPU occupancy fails on both
stacks, in opposite directions:

- **OCUDU:** occupancy ~0 % → estimator says "idle, low power" → wrong, it's 77 W.
- **OAI:** occupancy 100 % on two cores → estimator says "loaded, high power" →
  wrong, it's idle (no UE, no traffic).

**Neither stack's occupancy reflects actual work or energy.** It is pinned high
(OAI) or low (OCUDU) by the polling architecture, independent of load. This is
the thesis result, demonstrated with two contrasting implementations: **CPU
occupancy is not a valid energy or load proxy for a poll-mode Split 7.2x O-DU,
and the direction of its error is implementation-dependent.** Energy (RAPL) is
the faithful signal for both.

## 12. F5 — OAI monopolizes the PMU (interoperability finding)

OAI's O1 telemetry (`--telnetsrv.shrmod o1`) **continuously spawns
`perf stat -e instructions -C 3,5,7,…,31`** on the DU cores, and these processes
can get stuck (observed: 3–6 zombie perf processes that respawn after being
killed). Consequences:

- OAI uses hardware performance counters for its own monitoring; **OCUDU/srsRAN
  does not.**
- This **contends with external PMU-based measurement**: the agent's `perf` and
  `uncore` collectors hang on OAI and must be disabled (`ENABLE_PERF=false
  ENABLE_UNCORE=false`).
- Energy (RAPL) and occupancy (cpuidle) measurement is **unaffected** — the A/B/C/D
  energy ladder is fully measurable on OAI.
- OAI's continuous self-monitoring is itself an energy cost captured in its
  package-1 reading (consistent with OAI's +1.7 W over OCUDU at idle).

This is a concrete **interoperability limitation**: PMU-based O-Cloud telemetry
cannot coexist with OAI's perf-based O1 monitoring without contention. Worth
noting for any deployment intending to measure OAI via hardware counters.

## 13. Complete OAI ladder (2026-07-23 session, 3 runs per condition)

| Condition | package-1 runs (W) | mean | Δ prev |
|---|---|---|---|
| **A** no gNB | 66.35, 66.35, 66.36 | **66.36** | — |
| **B** OAI up, no UE | 77.85, 78.04, 78.17 | **78.02** | **+11.7** |
| **C** UE attached, no traffic | 77.78, 77.81, 77.85 | **77.81** | **−0.2** |
| **D** UE + iperf 100 Mbps | 79.69, 79.76, 79.76 | **79.74** | **+1.9** |
| | | | **+13.4 total** |

package-0 control flat at 64.1–64.4 W in every condition. ✓

## 14. Cross-stack comparison — OCUDU vs OAI

| Condition | OCUDU (srsRAN) | OAI | difference |
|---|---|---|---|
| A — no gNB | 66.33 W | 66.36 W | shared floor ✓ |
| B — gNB idle | 76.83 W (**+10.5**) | 78.02 W (**+11.7**) | OAI idles +1.2 W higher |
| C — UE attached | 86.68 W (**+9.9**) | 77.81 W (**−0.2**) | **OCUDU +9.9 W vs OAI ~0 W** |
| D — +100 Mbps | 89.78 W (**+3.1**) | 79.74 W (**+1.9**) | traffic cheap on both |
| **Total A→D** | **+23.4 W** | **+13.4 W** | **OAI serves a UE for ~10 W less** |

### F6 — UE attachment cost is implementation-dependent, and the gap is large

Attaching an idle UE costs **OCUDU +9.9 W** but **OAI ≈ 0 W** (−0.2 W, within
measurement noise; the B and C sample sets overlap). The same logical operation —
bringing one UE to attached, synchronized, zero-traffic state — has an order-of-
magnitude different energy cost between two Split 7.2x implementations on
identical hardware.

Consequently OAI serves one UE at ~100 Mbps for **+13.4 W over the platform
floor**, versus **+23.4 W for OCUDU** — roughly **43 % less energy** for the same
service, despite OAI idling 1.2 W *higher*. Idle power alone is therefore a poor
predictor of serving efficiency.

### F7 — Traffic is cheap on both stacks

100 Mbps adds **+3.1 W (OCUDU)** and **+1.9 W (OAI)**. Both are small relative
to the static and attach costs, reinforcing F2: DU energy is dominated by
radio/state maintenance, not user-plane throughput.

## 15. Validity note — the `threads` collector as a state verifier

Two runs labelled `A-oai-no-gnb` (2026-07-23 10:34, 10:38) read **79.7 W**, far
above the true A baseline of 66.4 W. The capture log identifies the cause: those
runs show `collectors active: … threads …`, whereas genuine A runs show
`collector threads SKIPPED: no process matching ('nr-softmodem',)`. The gNB was
still running; the runs are mislabelled and are excluded from the ladder above
(their 79.7 W matches the D-iperf runs, indicating traffic was also still active).

**Method note:** the `threads` collector's SKIPPED/active status is an
independent, machine-generated record of whether the gNB process existed during a
capture. It should be used to validate every run's label — energy alone cannot
distinguish a mislabelled condition, but this flag can.

## 16. Open caveat — confirm UE attachment for OAI condition C

The OAI result C ≈ B (−0.2 W) admits two readings that energy alone cannot
separate:

- **(a)** UE attachment genuinely costs OAI ~nothing — a substantive finding, or
- **(b)** the UE did not attach during the C captures, making them a second B.

Given OCUDU showed **+9.9 W** for the identical operation, positive confirmation
of UE attachment (OAI telnet/log UE count > 0 during the C window) is required
before F6 can be stated as final.

---

## 17. OCUDU load curve — first sweep points

| OCUDU condition | offered load | package-1 (W) | Δ prev |
|---|---|---|---|
| C — UE attached | 0 Mbps | 86.68 | — |
| D — iperf | 100 Mbps | 89.78 | **+3.1** |
| D — iperf | **200 Mbps** | **91.19** | **+1.4** |

### F8 — Energy-vs-load is sub-linear and flattening

The first 100 Mbps costs **+3.1 W**; the second 100 Mbps costs only **+1.4 W**.
Doubling offered load did not double the traffic-related energy — the increment
roughly halved. Combined with F3 (50 vs 100 Mbps indistinguishable) and F7
(traffic cheap on both stacks), the picture is consistent: **DU energy is
dominated by the always-on radio/state maintenance, and marginal energy per
additional bit falls as load rises.**

*Caveat:* the 200 Mbps figure is currently a single confirmed run (91.19 W);
the other two captures in that batch have not yet been extracted. Treat as
provisional until all three are averaged.

## 18. Labelling and `GNB_COMM` caveats (method)

**The `--label` string is user-supplied metadata only.** The agent cannot
observe:
- the **iperf rate** — iperf runs on a separate host; the agent never sees it;
- the **gNB stack** — identified solely by the `GNB_COMM` value passed in.

A batch captured on 2026-07-23 11:12–11:14 was labelled `D-oai-iperf-100M` but
was in fact **OCUDU at 200 Mbps**. The energy (91.19 W) and the operator's record
identify the true condition; the directory name does not. Labels must therefore
be verified against the actual deployment at capture time.

**Correction to §15.** The `threads SKIPPED` flag verifies only that *the process
named in `GNB_COMM`* was absent — not that no gNB was running. In the batch
above, `GNB_COMM=nr-softmodem` was passed while OCUDU (process `gnb`) was
running, so the collector skipped on a **name mismatch**, losing per-thread data
for those runs. The flag is a valid state check **only when `GNB_COMM` matches
the stack under test**.
