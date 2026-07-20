# ocloud-telemetry-agent

RAN-agnostic CPU and energy telemetry for **Split 7.2x O-DU** characterisation on
an O-Cloud, with a standards-conformant exposure path.

Built for: OCUDU 2026.04 and OAI, on StarlingX (node `joule`), against
O-RAN.WG6 O2IMS / O-Cloud IM / O-Cloud-ES and 3GPP TS 28.552 / 28.554.

## What it answers

> Does scheduler-visible CPU occupancy track offered load for a Split 7.2x O-DU
> on a conformant O-Cloud?

The literature assumes DPDK-based DUs busy-poll and therefore saturate CPU
occupancy, which would invalidate the CPU-time-proportional energy estimators in
TS 28.554 §6.7.3.1.4 and O-RAN.WG6.TR.O-CLOUD-ES Table 6.3-2.

**Measured on joule, they do not saturate.** The EAL lcores run at 26.4% mean
with the cell up. See [`docs/findings.md`](docs/findings.md).

Either answer publishes. That is the point.

## Independence

The agent never links against, patches, or reads the source of any RAN stack. It
reads `/proc`, `/sys`, PMU counters, and Redfish. The only RAN-specific input is
one environment variable:

| | OCUDU | OAI |
|---|---|---|
| `GNB_COMM` | `gnb` | `nr-softmodem` |

## Node health (operator convenience, not measurement)

A slow (120s) heartbeat so the r-App can show green/red without SSHing into the
node. Polled, never pushed -- the agent exposes it, the r-App consumes it:

```bash
python3 -m agent.main health          # one-shot: PTP / iptables / route / VF
curl localhost:5010/health            # when serving; r-App polls this
```

Watches (thresholds from the joule PTP guide): PTP master_offset <100ns +
gmPresent + grandmaster identity, ptp4l/phc2sys alive, PHC<->REALTIME offset,
iptables ruleset change vs first-run baseline (nft backend), ip route change,
enp202s0f0 link + VF3 (spoof/link-state/trust/tx-drops). Cost ~40ms / 120s.
Needs root for pmc + iptables; degrades to "unavailable" otherwise.

## What it captures

| Source | Metric | Serves |
|---|---|---|
| `/proc/stat` | per-core occupancy | the busy-poll question |
| `/sys/.../cpuidle` | idle residency (POLL, C1) + stale guard | the mechanism |
| `/sys/class/powercap` (RAPL) | energy per package + DRAM | ES TR EC_node,* |
| `perf` | IPC, MPKI — real work vs presence | discriminates load |
| `/proc/<pid>/task` | per-thread CPU + core placement | DU vs CU threads |
| Redfish (off-node) | whole-box watts | EC_node,platform |
| `msr/aperf,mperf` | **delivered** frequency (not requested) | throttle-proofs energy |
| `cstate_core/*-residency` | hardware C-states | cross-check for stale sysfs |
| `perf --per-node` | memory bandwidth, per-socket | NUMA / bottleneck analysis |
| NUMA sysfs | placement, FH-VF alignment | correctness (500us deadlines) |

## Quickstart — the A/B protocol

No cluster needed. Same collectors as the served mode.

```bash
# A: node idle, no gNB
sudo python3 -m agent.once --label A-idle-no-gnb --window 30   # x3

# deploy the gNB, then within minutes:
sudo python3 -m agent.once --label B-gnb-idle --window 30      # x3
```

Auto-detecting capture (the framework decides the state, not you):

```bash
sudo ./tools/run_campaign.sh          # detect current state, capture once
sudo ./tools/run_campaign.sh --watch  # re-detect + capture each cycle
```

It classifies A (no gNB) / B (gNB, no UE) / B-ue (gNB + UE, no traffic) /
C (iperf running). It NEVER generates traffic — iperf is driven by the r-App;
the script only detects it and labels the run. A pod Running while the gNB
process is absent (the O1-timeout restart loop) is reported, not labelled.

Or as a DaemonSet:

```bash
kubectl apply -k deploy/
curl -XPOST localhost:5010/experiment/start -H 'Content-Type: application/json' \
     -d '{"label":"C-iperf-900","meta":{"dl_mbps":900}}'
# ... run iperf ...
curl -XPOST localhost:5010/experiment/stop
```

Off-node platform power (the node cannot reach its own BMC):

```bash
REDFISH_PASSWORD=... python3 tools/redfish_probe.py \
  --url https://<bmc> --run-id <run_id> --window 30 --out redfish.csv
```

## Layout

```
agent/collectors/   cpuidle, procstat, rapl, threads, perf, redfish,
                    topology, cpufreq, numa, uncore, health
agent/core/         sampler (measured windows), run (manifest)
agent/sinks/        filesink (authoritative CSV)
agent/api/          REST — the r-App drives this
deploy/             namespace (PSA privileged), rbac, daemonset, service
pm-dictionary/      proposed PM Dictionary — a thesis artifact
o2/                 O2ims_InfrastructurePerformance skeleton (verify-first)
docs/               findings ledger, architecture, ICS
tools/              baseline.py (standalone), redfish_probe.py (off-node)
analysis/           pandas loaders
```

## Requirements

| | Why |
|---|---|
| `hostPID: true` | `/proc` sees host processes; replaces `nsenter` |
| `runAsUser: 0` | `energy_uj` is 0400 root:root (CVE-2020-8694) |
| `CAP_PERFMON` | bypasses `perf_event_paranoid=2` (kernel ≥5.8) |
| `CAP_SYS_PTRACE` | read other processes' `/proc/<pid>/task` |
| hostPath `/sys` (ro) | RAPL, cpuidle, topology |
| hostPath `/mnt/debugging-logs` | run files + rendered `gnb-config.yml` |
| PSA `enforce=privileged` on the namespace | StarlingX blocks the pod otherwise |

## Correctness rules

Each is baked into code because it was a real bug first. Full list in
[`docs/findings.md`](docs/findings.md).

1. **Measure the window**, midpoint-to-midpoint. `sleep 10` is not 10.000 s.
2. **All idle states count.** `busy = 100 − C1%` counts POLL as work.
3. **Stale-counter guard.** cpuidle `time` advances only on state *exit*; a
   deeply idle core yields round constants (28.000s) or values exceeding the
   window. Report `STALE`, never a busy figure.
4. **RAPL wraps at `max_energy_range_uj`** (≈2^38 here), not 2^32.
5. **Key RAPL by sysfs dir**, not `name` — `dram` appears once per socket.
6. **Never `nproc`** — `kthread_cpus=` makes it report 5 of 32.
7. **Parse `/proc/.../stat` via the last `)`** — comm may contain spaces.
8. **Archive the rendered `gnb-config.yml`** with every run.
9. **Correlate on monotonic offset from `t0`**, never wall clock.
10. **Files are authoritative.** InfluxDB is for looking at.

## Status

- [x] 10 collectors (cpuidle, procstat, rapl, threads, perf, redfish,
      topology, cpufreq, numa, uncore), sampler, manifest, file sink, REST, DaemonSet
- [x] A/B protocol; idle floor and DU static cost measured
- [x] PM Dictionary (proposed); ICS skeleton
- [x] uncore collector: delivered freq (aperf/mperf), hw C-states (cross-check
      for stale sysfs), per-socket memory bandwidth (IMC) — all validated on joule
- [x] O2 InfrastructurePerformance skeleton (data model; not yet wired to pti-o2)
- [ ] Deterministic lcore pinning (`--lcores 0@3,1@5,…`) — placement currently moves across restarts
- [ ] UPI + AVX-512 clipping — probe-only, enable during sweep if they fire
- [ ] InfluxDB sink
- [ ] `O2ims_InfrastructurePerformance` on pti-o2
- [ ] r-App: O1 (DV) ↔ O2 (EC) join → EE KPI
