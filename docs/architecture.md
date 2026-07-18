# Architecture

## Principle

**Collection is not standardised; exposure is.**

No O-RAN or 3GPP document says how to read a CPU counter. They have a great
deal to say about how the SMO consumes the result. So the agent reads whatever
is accurate, and the standards question lives entirely in the sinks.

## Interface split

Getting this wrong once cost real time, so it is stated explicitly:

| Data | Interface | Why |
|---|---|---|
| Node/infrastructure CPU + energy | **O2** (`O2ims_InfrastructurePerformance`) | O-Cloud infrastructure. SMO ↔ O-Cloud. |
| gNB data volume (PDCP SDU) | **O1** | Managed Element measurement. SMO ↔ ME. |
| EE KPI = DV ÷ EC | **r-App** | Needs both. Nothing specifies the join (findings S8). |

**TS 28.552 PEE is not the path for a containerised DU.** PEE measurements are
PNF-related — they assume a box with a sensor. A CNF has none; ES TR §6.4 routes
cloudified-NF energy through O-Cloud estimation instead.

## Data flow

```
  /proc, /sys        perf PMU         RAPL          Redfish BMC
  occupancy,         IPC, MPKI        package,      platform watts
  idle residency                      dram          (OFF-NODE)
       |                 |               |               |
       +--------+--------+-------+-------+               |
                         |                               |
              ocloud-telemetry-agent                     |
              DaemonSet | hostPID | no nsenter           |
                         |                               |
        +----------------+----------------+              |
        |                |                |              |
    run files        InfluxDB        O2 PM service       |
    AUTHORITATIVE    dashboards      pti-o2 ext          |
        |                                 |              |
        +---------------- join on run_id -+--------------+
                         |
                       r-App  <--- O1 PM (data volume)
                         |
                  EE KPI bit/J + conformance verdict
```

## Why hostPID and not nsenter

The predecessor (`cpu_service.py`) used `nsenter -t 1 -m -u -n -i` to reach the
host's `/proc`. That requires `hostPID: true` anyway to be meaningful — and once
you have `hostPID`, the container's own `/proc` already shows every host process
and `nsenter` buys nothing. It cost ~80 `fork`+`exec` per sample (2 per thread ×
~40 threads), which inflated both measurement overhead and the sampling window.

`/proc/stat` specifically is not namespaced at all and is host-wide even without
`hostPID`.

## Why files are authoritative

InfluxDB is for looking at. Files are the record:

- no silent drops when a network path hiccups
- immutable per run, with the manifest bound to the samples
- readable in two years with no running infrastructure

A run directory:

```
runs/<run_id>/
├── manifest.json     node facts, cmdline, topology, phc2sys argv, collectors
├── gnb-config.yml    the rendered config the gNB ACTUALLY ran
├── cores.csv         per-core idle residency + /proc/stat, per sample
├── power.csv         RAPL per domain, J and W
├── threads.csv       per-thread cpu_pct, last_cpu, migration
├── perf.csv          IPC, MPKI, instructions, context switches
├── redfish.csv       platform watts (joined off-node by run_id)
└── summary.json      duration, sample count, collector errors
```

`gnb-config.yml` is not optional. Two deployments differed by 10 W and the cause
was only identifiable from the config (finding R4).

## RAN-agnosticism

The agent never links against, patches, or reads the source of any RAN stack. It
reads `/proc`, `/sys`, PMU counters, and Redfish. The only RAN-specific input is
configuration:

| Config | OCUDU | OAI |
|---|---|---|
| `GNB_COMM` | `gnb` | `nr-softmodem` |

This is what makes "OCUDU vs OAI on identical instrumentation" a defensible
interoperability comparison rather than two unrelated experiments.

## Correlation

Everything correlates on **monotonic offset from `t0`**, never wall clock. The
clock is currently UTC and correct (finding F7) — but `phc2sys` argv has already
changed under us once, so the manifest records it every run and the analysis
never depends on it.
