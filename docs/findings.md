# Findings ledger

Measured on **joule** (StarlingX 24.09, kernel 6.6.0-1-rt-amd64, dual-socket
Xeon) unless noted. Each row is evidence, not inference.

## Platform facts

| # | Finding | Evidence |
|---|---|---|
| F1 | `intel_idle.max_cstate=0`; only **POLL** and **C1** exist. No C6. | `/proc/cmdline`, `cpuidle/state*/name` |
| F2 | `intel_pstate=none` on cmdline, but `scaling_driver=intel_cpufreq`, governor `performance`, `scaling_setspeed` present. Something re-enabled it (StarlingX ships `host-cpu-max-frequency-modify`). | `cpu0/cpufreq/*` |
| F3 | RAPL exposes `package-0`, `package-1`, `dram`×2. **No `core` (PP0), no `uncore`, no `psys`.** | `ls /sys/class/powercap` |
| F4 | `max_energy_range_uj = 262143328850` (≈2^38), not 2^32. | sysfs |
| F5 | `energy_uj` is 0400 root:root (CVE-2020-8694). | permission denied as non-root |
| F6 | `perf_event_paranoid = 2` → per-CPU events blocked without CAP_PERFMON. | `/proc/sys/kernel/...` |
| F7 | Clock is **UTC**: `phc2sys -O -37 -w`, PHC−REALTIME = **36.99 s**, `currentUtcOffset 37`, `currentUtcOffsetValid 1`, `ptpTimescale 1`, `timeSource 0x20` (GNSS), `master_offset -3` ns. | `pmc -d 24`, bracketed `date` |
| F8 | StarlingX declares `cmdline_opts=-a -r -r` but the running phc2sys is `-s enp202s0f0 -c CLOCK_REALTIME -O -37 -w`. Config drift; both yield UTC. | `system ptp-instance-show`, `ps` |
| F9 | joule cannot route to its own BMC. 192.168.8.78 → HTTP 200. | `ping`, `curl -w %{http_code}` |
| F10 | gNB is confined to **socket 1** (all isolated CPUs are odd; all odd CPUs are NODE 1). package-0 is a free control group. | `lscpu -e`, `/sys/.../isolated` |
| F11 | `nproc` returns **5** of 32 (`kthread_cpus=0-1,4,8,12` confines the shell). | `nproc` vs `ls -d cpu[0-9]*` |
| F12 | Mitigations off: `nopti nospectre_v1 nospectre_v2`. Materially affects IPC. | `/proc/cmdline` |

## Measurement-method findings

| # | Finding |
|---|---|
| M1 | **cpuidle `time` counters go stale.** They advance only on state *exit*. A deeply idle core (nohz_full + rcu_nocbs) leaves them quantised — exactly `28.000s` / `32.000s` across unrelated cores, sometimes exceeding the wall-clock window (negative "busy"). Confirmed on 3 independent runs. → `STALE_RATIO` guard. |
| M2 | **All idle states count.** `busy = 100 − C1%` counts POLL as work. |
| M3 | **The window must be measured**, midpoint-to-midpoint. `sleep 10` + two 32-core read loops = ~10.35 s. |
| M4 | **RAPL must be keyed by sysfs dir.** `dram` appears twice; keying by `name` loses socket 0. |
| M5 | **`/proc/stat` is not namespaced** → host-wide from inside a container, no hostPID needed for it. Self-normalising, so immune to M3. Use it to cross-check cpuidle. |
| M6 | **DPDK lcore placement is not reproducible.** `--lcores (0-13)@(3,5,...)` is *group* syntax: one shared mask across all 14 lcores, no 1:1 pinning. Hot pair moved cpu13/25 → cpu5/17 between deployments. Fix: `--lcores 0@3,1@5,...`. |

## Results

| # | Result |
|---|---|
| R1 | **Idle node floor: package-1 = 66.275 W** (6 runs, 66.218–66.364). package-0 = 64.15 W. Packages total ≈ 130.4 W. |
| R2 | **DU static cost, deployment B: +20.83 W** (87.104 / 87.109 W — two runs). 14 EAL lcores active, mean 26.4%. |
| R3 | **DU static cost, deployment C: +10.71 W** (76.993 / 76.984 W). Only 2 lcores active (cpu5 52.8%, cpu17 39.0%); the other 12 stale-idle. Cell verified up: `nof_prbs=273`, `ether_tx=882.08 Mbps`. |
| R4 | B and C ran **different cell configs** (C picked up a stale `gnb-config.yml` from hostPath after the o1-adapter timed out: `dl_ul_tx_period: 10 / 7 / 2`, not the intended DDDSU 5/3/1). **This is why every run archives its rendered config.** |
| R5 | **package-0 never moves** (64.15 → 64.31 W) across all states. Control group intact; rules out drift/thermal/background load. |
| R6 | **The OFH does not busy-poll.** Two hot cores; the rest ≤24% or idle. Nothing near 100%, in either deployment. |
| R7 | cpu9 — isolated but absent from `--lcores` — stays idle while all EAL cores light up. Instrument validation. |

## Standards findings

| # | Finding |
|---|---|
| S1 | **ES TR Use Case 3 is unimplementable on a conformant O-DU node.** UC3 selects per-NF between C6 and C1; F1 shows no C6 exists. The Low-Latency profile an O-DU *requires* forecloses the choice UC3 says the O-Cloud should offer. UC3 §5.3.3.2 also states "no measurement data required" — yet C-state residency is the only thing that proves the config took effect. |
| S2 | **ES TR Table 6.2-2 names EC_node,core** as the metric for CPU-bound workloads. F3: server-class Xeons do not expose PP0. The TR recommends an unavailable counter. |
| S3 | **The report format chain never closes.** O2IMS TS §3.7 specifies PM job/subscription lifecycle but §3.7.6.2.5 leaves notification/file/stream formats unspecified; §3.7.5 says the API specifies no notifications while `NOTIFICATION` is a mandatory delivery enum. O-Cloud IM defers content to a WG10 `PerformanceMeasureDictionary`; ES TR §6.2.2.1 says the dictionary is *vendor-provided*. Two conformant O-Clouds can emit mutually unintelligible reports. |
| S4 | **ES TR §6.2.2.2 explicitly does not address exposing EC_node,* over O2.** The TR defines the metrics; the TS defines the pipe; nothing connects them. |
| S5 | **O-RAN cites the DPDK PMD polling problem on the control side** (ES TR ref [5], Intel "User Wait Instructions Power Saving for DPDK PMD Polling Workloads"; §4.2.2.2.2 waitpkg / C0.1 / C0.2) while specifying **CPU Time** as the power-estimation input on the measurement side (Table 6.3-2). |
| S6 | **TS 28.554 §6.7.3.1.4** estimates NF energy as node energy × (NF mean vCPU usage ÷ Σ all NFs' mean vCPU usage). Under co-location a workload with constant vCPU usage has a *falling* share as neighbours get busier. Single-NF case is degenerate (ratio = 1). |
| S7 | `O2ims_InfrastructurePerformance` is fully specified (TS §3.7, API v2.2.0, all resources M) and **not implemented** by StarlingX `oran-o2 24.09-25`, which ships Inventory + Monitoring(alarms) only. |
| S8 | ES TR §6.5.2 expects the SMO to compute EE = Perf/EC from O-Cloud measurements and flags WG10 collaboration — i.e. the **O1 (data volume) ↔ O2 (energy) join is acknowledged but unspecified**. |

## Retracted

Claims made during investigation and **disproven by measurement**. Kept so they
are not re-derived.

| Claim | Status |
|---|---|
| "DPDK O-DU busy-polls → occupancy saturates at 100%" | **Disproven.** R6: 26.4% mean, two deployments. |
| "CLOCK_REALTIME is TAI, 37 s skew" | **Disproven.** F7: `-O -37`, measured 36.99 s. |
| "The `-O` change is causing `rx_total=0`" | **Unsupported.** No evidence of an epoch change. |
| "Kepler is structurally blind to load" | **Unproven.** ES TR Annex A.1 says bare-metal Kepler counts *instructions*, not CPU time. Must be measured. |
| "`intel_pstate=none` means no P-state driver → UC2 unimplementable" | **Wrong.** F2. |

## Added 2026-07-18 (frequency / thermal / uncore)

| # | Finding | Evidence |
|---|---|---|
| F13 | Fronthaul VF `0000:ca:01.3` is on **NUMA node 1**, same socket as the isolated (odd) cores. Fronthaul path is NUMA-aligned; no forced UPI crossing for FH traffic. | `cat /sys/bus/pci/devices/0000:ca:01.3/numa_node` = 1 |
| F14 | **RETRACTED.** Earlier claim "uncore PMU absent" was a wrong event *name* (`uncore_upi`, correct: `uncore_upi_0`). joule exposes the full fabric: `uncore_imc_0..15`, `uncore_upi_0..2`, `uncore_cha_0..15`, `uncore_pcu`, `uncore_m3upi_*`. | `perf list`, `ls /sys/devices \| grep uncore` |
| F15 | `scaling_cur_freq` = requested P-state, not delivered. Delivered ratio = `msr/aperf/ ÷ msr/mperf/`; measured 1.48 at one instant. Both readable. Recorded so the sweep's energy comparison can be shown free of thermal throttling. | `perf stat -e msr/aperf/,msr/mperf/,msr/tsc/` |
| F16 | **Hardware C-state counters exist and validate the stale-sysfs finding (M1).** `cstate_core/c1-residency`, `/c6-residency` readable per-core. cpu5: c1=22316784, **c6=0** — C6 genuinely never entered, confirming F1 from an independent counter path. | `perf stat -e cstate_core/... -C 5` |
| F17 | **Memory bandwidth is per-socket via `perf --per-node`.** The sysfs `cpumask` does NOT give the socket -- all 12 IMCs report `cpumask 0-1` (it names the *reader* CPU, not the IMC's socket). `perf stat -a --per-node` uses perf's own authoritative IMC->node map. Measured idle-with-gNB: N0 (idle socket) ~200 MiB read, N1 (gNB socket) ~294 MiB read. | `perf stat -x , -a --per-node -e uncore_imc/cas_count_read/` |
| F18 | UPI cross-socket traffic is countable (`uncore_upi_0`, nonzero+varying) but **deferred** — VF is NUMA-aligned so cross-socket pressure may be minor; confirm it matters under load first. | `perf stat -e uncore_upi_0/event=0x2,umask=0x0f/` |
| F19 | AVX-512 frequency-clipping counter exists (`uncore_pcu`), reads **0 at idle**. Probe-only until nonzero under the sweep. | `perf stat -e uncore_pcu/event=0x74/` = 0 |
| F20 | perf runs unprivileged but userspace-only (`:u`) at `perf_event_paranoid=2`. Full counters need CAP_PERFMON (the DaemonSet has it) or root. | `perf stat -e instructions` shows `instructions:u` |

## Hardware validation (2026-07-18, full agent on joule)

First run of the complete 10-collector agent on real hardware. gNB was up (28h),
so this is a gNB-idle capture, not baseline A. All values reproducible across 6
samples.

| Metric | Value | Note |
|---|---|---|
| package-1 (gNB socket) | **76.93 W** | matches deployment C (76.99 W) to 0.06 W |
| package-0 (control) | 64.62 W | idle socket, unchanged |
| delivered freq ratio | **1.4762**, steady | aperf/mperf; throttle baseline for the sweep |
| C6 residency | **0** everywhere | F16 confirmed on hardware: C6 never entered |
| C1 residency | ~25.3e9 cyc/window | cores park in C1 |
| mem BW socket 1 (gNB) | ~294 MiB read / 226 write | via --per-node (F17 fix) |
| mem BW socket 0 (idle) | ~200 MiB read / 90 write | control |
| IPC | **0.61** | low at idle (poll spinning); expect rise under load |
| MPKI | 0.70 | cache-miss rate baseline |

The IPC-rises-while-occupancy-stays-flat prediction now has its idle anchor.

## Capture-framework detection (2026-07-18)

| # | Finding | Evidence |
|---|---|---|
| D1 | gNB metrics port **8001 not reachable from host** (pod netns only). ESTAB `:8001` connections are StarlingX pmond/collectd, unrelated. | `ss -tlnp \| grep 8001` empty; curl times out |
| D2 | **No host metrics log file.** gNB metrics go to pod stdout; `nof_ues` read via `kubectl logs`, or host log if METRICS_LOG is set. | `ls /mnt/debugging-logs/*.log` empty |
| D3 | **gNB liveness = pod Running AND pgrep gnb.** Pod-alone reads Running during the O1-timeout restart loop. Disagreement = restart-loop signal; framework reports rather than mislabels. | classifier design |
| D4 | **iperf is r-App-driven only.** Framework never generates traffic; detects iperf3 and labels C. Keeps the agent observational / RAN-agnostic. | design decision |
