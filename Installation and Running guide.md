# Installation & Running Guide

Two components on **two hosts**:

| Component | Runs on | Purpose |
|---|---|---|
| **agent** (`agent/`, `tools/`, `capture.sh`, `analyze.sh`) | **joule** (workload node) | captures CPU/energy telemetry, writes run files |
| **shipper** (`influxDB/`) | **galileo** (has InfluxDB reach) | pulls run files, writes to InfluxDB |

> Why two hosts: joule runs the gNB and can read RAPL/PMU but **cannot reach
> InfluxDB**. galileo **can** reach InfluxDB and can ssh to joule. So joule
> captures, galileo ships.

```
JOULE                          GALILEO                        INFLUXDB
capture.sh / run_campaign.sh   ocloud-shipper.service   ---->  infra-telemetry
  -> runs/<id>/*.csv  --rsync-> (influx.py, watch mode)        (Grafana / rApp read)
```

---

# PART 1 — INSTALLATION

## 1A. Agent (on joule)

**Requirements:** Python 3.9+, `perf`, root (for RAPL/PMU). Standard library
only, no pip packages.

```bash
# on joule
git clone https://github.com/ontherays/ocloud-telemetry-agent.git
cd ocloud-telemetry-agent

python3 -m tests.test_correctness     # must end "all correctness tests passed"
bash tools/test_classify.sh           # 7 PASS

chmod +x capture.sh analyze.sh        # the experiment scripts
```

Runs are written to `/mnt/debugging-logs/runs/<run_id>/`.
Optional env overrides (defaults shown): `WINDOW=30`, `ENABLE_UNCORE=true`,
`NS=ravi-ns`, `GNB_COMM=gnb`.

## 1B. Shipper (on galileo)

**Requirements:** Python 3.9+, `rsync`, ssh to joule.

**B1. Get the code**
```bash
# on galileo
git clone https://github.com/ontherays/ocloud-telemetry-agent.git
cd ocloud-telemetry-agent/influxDB
# files: influx.py  ocloud-ship.service  ship_influx.sh  readme.md
```

**B2. Passwordless SSH galileo → joule (one-time)**
```bash
ssh-keygen -t ed25519 -f ~/.ssh/joule_key -N ''
ssh-copy-id -i ~/.ssh/joule_key.pub sysadmin@192.168.206.82
ssh -o BatchMode=yes -i ~/.ssh/joule_key sysadmin@192.168.206.82 'echo ok'   # must NOT prompt
```

**B3. Config file — the ONLY place credentials live** (`/etc/ocloud-shipper.env`, root-only)
```bash
sudo tee /etc/ocloud-shipper.env >/dev/null <<'ENVEOF'
INFLUX_URL=http://192.168.8.69:30138
INFLUX_ORG=ravi-ric
INFLUX_BUCKET=infra-telemetry
INFLUX_TOKEN=<your-influxdb-write-token>
RSYNC_RSH=ssh -o BatchMode=yes -i /root/.ssh/joule_key
JOULE_RUNS=sysadmin@192.168.206.82:/mnt/debugging-logs/runs/
MIRROR_DIR=/root/ocloud-telemetry-agent/influxDB/runs-mirror
SHIP_INTERVAL=60
NODE_NAME=joule
ENVEOF
sudo chmod 600 /etc/ocloud-shipper.env
```
> Token: InfluxDB UI → Load Data → API TOKENS → token with **write** to `infra-telemetry`.

**B4. Install the systemd service (persistent, survives reboot)**

Edit `ocloud-ship.service` so `ExecStart` / `WorkingDirectory` point at your
clone (e.g. `/root/ocloud-telemetry-agent/influxDB/`), then:
```bash
sudo cp ocloud-ship.service /etc/systemd/system/ocloud-shipper.service
sudo systemctl daemon-reload
sudo systemctl enable --now ocloud-shipper
sudo systemctl status ocloud-shipper        # expect: active (running)
```
The service reads `/etc/ocloud-shipper.env` and runs the shipper in `watch`
mode (ships every 60 s, restarts on failure, auto-starts on boot).

---

# PART 2 — THE EXPERIMENT WORKFLOW (capture.sh)

The energy ladder is a **controlled experiment**: four conditions, each changing
exactly one thing, so the *differences* isolate each cost.

| Condition | State | Isolates |
|---|---|---|
| **A** | no gNB | platform floor |
| **B** | gNB up, no UE | DU static (software) cost |
| **C** | gNB up, UE attached, **no traffic** | UE attach / radio-link cost |
| **D** | gNB up, UE + iperf at a given rate | user-traffic cost |

Subtracting gives the answers: `B−A` = DU cost, `C−B` = attach cost,
`D−C` = traffic cost. A single "capture whatever is running" reading cannot
produce any of these.

## 2A. capture.sh — one condition, 3 runs, state-verified

```bash
cd ~/ocloud-telemetry-agent
sudo ./capture.sh <stack> <condition> [rate]
```

| arg | values |
|---|---|
| `stack` | `ocudu` \| `oai` |
| `condition` | `A` \| `B` \| `C` \| `D` |
| `rate` | required for `D`, e.g. `100M`, `200M` |

**It sets the correct environment per stack automatically:**

| | OCUDU | OAI |
|---|---|---|
| process (`GNB_COMM`) | `gnb` | `nr-softmodem` |
| `ENABLE_PERF` / `ENABLE_UNCORE` | **true** | **false** — OAI monopolises the PMU with its own `perf stat` O1 telemetry |
| config archived | `/mnt/gnb-runtime/gnb-config.yml` | none (OAI uses `du.conf`; save it manually) |
| metrics obtained | energy, occupancy, IPC/MPKI, memory BW | energy, occupancy (no PMU metrics) |

**It verifies the system state BEFORE capturing and refuses if it doesn't match:**

- the *other* stack is running → abort (mixing stacks invalidates the ladder)
- condition `A` but a gNB process exists → abort
- conditions `B`/`C`/`D` but no gNB process → abort
- `B`/`C` while iperf runs locally → abort
- `C`/`D` → interactive confirmation of UE attachment / live traffic

**It then runs 3 captures and prints the result immediately:**

```
run                                          pkg1(W)    pkg0(W)
20260724T...-B-ocudu-noUE-a1b2c3               76.83      64.20
20260724T...-B-ocudu-noUE-d4e5f6               76.82      64.19
20260724T...-B-ocudu-noUE-g7h8i9               76.85      64.21
--------------------------------------------------------------
MEAN of 3 runs                                 76.83      64.20
```

Overrides: `RUNS=5 WINDOW=60 sudo -E ./capture.sh ocudu B`

Labels are generated consistently: `A-ocudu-no-gnb`, `B-oai-noUE`,
`C-ocudu-ue-noTraffic`, `D-ocudu-iperf-200M`.

## 2B. Full workflow — both stacks

Run one stack's ladder end to end, then switch. **Never both deployed at once**
(the script will refuse).

```bash
cd ~/ocloud-telemetry-agent

# ---------- OCUDU ladder ----------
# (OCUDU deployed; UE detached; no iperf)
sudo ./capture.sh ocudu A          # expect ~66 W   (bring gNB DOWN first)
sudo ./capture.sh ocudu B          # expect ~77 W   (gNB up, UE detached)
sudo ./capture.sh ocudu C          # expect  > B    (attach the UE, no traffic)

# start a LONG iperf on the core/UE host first, e.g.
#   iperf3 -c <UE-IP> -p 8889 -u -b 100M -t 150 -l 1200
sudo ./capture.sh ocudu D 100M     # expect  > C    (captures fire inside the iperf window)
sudo ./capture.sh ocudu D 200M     # (restart iperf at 200M first)

./analyze.sh ocudu                 # review the OCUDU ladder

# ---------- switch stacks ----------
# stop OCUDU completely, deploy OAI, verify: pgrep -x gnb  returns nothing

# ---------- OAI ladder ----------
sudo ./capture.sh oai A
sudo ./capture.sh oai B
sudo ./capture.sh oai C
sudo ./capture.sh oai D 100M

./analyze.sh                       # both ladders side by side
```

**Check the mean printed after each condition before moving on.** If it looks
wrong (e.g. `C ≈ B`, or `pkg0` drifted off ~64 W), stop and investigate — far
cheaper than discovering it at the end.

## 2C. analyze.sh — summarise everything captured

```bash
./analyze.sh            # all conditions, both stacks
./analyze.sh ocudu      # only ocudu runs
./analyze.sh oai        # only oai runs
./analyze.sh 20260724   # only that date
```

Prints, per condition: **every run's mean**, then the group mean with sample
count and spread; then a ladder summary sorted by energy.

**How the numbers are computed** — two levels of averaging, both correct:
1. within a run: mean of **all** samples in that run's `power.csv`
2. across runs: mean of the per-run means

Run it **after completing a stack's ladder**, not after every capture —
`capture.sh` already reports each condition as it finishes.

## 2D. Validity checks

| Check | Why |
|---|---|
| **Ladder increases: A < B < C ≤ D** | two conditions with the same value means the state didn't actually change |
| **`pkg0` (control socket) stays ~64 W** in every condition | proves the `pkg1` deltas are the DU, not system-wide drift |
| **Spread within a condition is small** (~0.1–0.3 W) | a wide spread means the state changed during capture |
| **One config per ladder** | changing radio/config/log-level mid-ladder makes conditions incomparable |

## 2E. What the scripts cannot verify — you must

- **The iperf rate.** iperf runs on another host; the agent never sees it. The
  rate in the label is whatever you type — type the truth.
- **UE attachment.** Confirmed interactively only. Verify from the gNB side
  (UE count > 0) before answering `y`.
- **OAI's config.** Not auto-archived. Save it once per ladder:
  ```bash
  PID=$(pgrep -x nr-softmodem | head -1)
  sudo cp /proc/$PID/root/tmp/du.conf ~/oai-du.conf.$(date +%Y%m%d-%H%M%S)
  ```

---

# PART 3 — ALTERNATIVE: run_campaign.sh (auto-detect)

For **unattended or continuous** capture where you don't want to declare the
condition, `run_campaign.sh` detects the state itself:

```bash
cd ~/ocloud-telemetry-agent
sudo ./tools/run_campaign.sh            # one 30 s snapshot, auto-labelled
sudo ./tools/run_campaign.sh --watch    # a run every ~30 s; Ctrl-C when done
```

Auto-detected labels:

| Label | Meaning |
|---|---|
| `A-idle-no-gnb` | no gNB running |
| `B-gnb-idle` | gNB up, no UE |
| `B-ue-gnb-attached` | gNB up, UE attached, no traffic |
| `C-iperf` | gNB up, iperf running (driven by the r-App) |

> **Use `capture.sh` for controlled experiments** (it verifies state and gives
> clean 3-run condition means). Use `run_campaign.sh --watch` only while a test
> is actively running, and **Ctrl-C when it ends** — left running it produces a
> run every 30 s and floods the bucket with idle captures.

Each run creates `/mnt/debugging-logs/runs/<run_id>/` containing:
`cores.csv power.csv perf.csv freq_delivered.csv cstate_hw.csv
membw_socket.csv thermal.csv cpufreq.csv manifest.json health.json
summary.json gnb-config.yml`.

---

# PART 4 — SHIPPING & VIEWING

## 4A. galileo ships automatically (nothing to run)

Within ~60 s of a run finishing on joule, the `ocloud-shipper` service:
1. `rsync`-pulls new run dirs from joule → `runs-mirror/`
   (skips `gnb-config.yml` — gNB owns it; kept on joule),
2. converts each run's CSVs to InfluxDB line protocol,
3. writes to `infra-telemetry`, tagged `run_id` + `condition` + `node` +
   `socket`/`cpu`,
4. marks the run shipped (won't re-ship).

Watch it (optional):
```bash
journalctl -u ocloud-shipper -f     # "shipped <run_id> (N points) -> infra-telemetry"; Ctrl-C to stop watching
```
Ship immediately instead of waiting up to 60 s:
```bash
cd ~/ocloud-telemetry-agent/influxDB
./ship_influx.sh once
```

## 4B. View / consume

**InfluxDB UI:** Data Explorer → **time range "Past 7 days"** (data is stamped
at capture time, not ship time; the default 1 h window hides it) → bucket
`infra-telemetry` → measurement e.g. `ocloud_power`.

**Grafana:** datasource URL `http://192.168.8.69:30138`, org `ravi-ric`, bucket
`infra-telemetry`, a **read** token. Panel: `ocloud_power`, field `watts`,
`name=package-1`, group by `condition`. Set the panel time range to your
capture dates.

**rApp:** reads the same bucket via the InfluxDB API using `run_id`/`condition`
tags, and/or polls the agent's `/health`. No change needed on joule or galileo —
the tags are the contract.

---

# PART 5 — REFERENCE

## Measurements written (what to query)

| measurement | tags | key fields |
|---|---|---|
| `ocloud_power` | run_id, node, condition, domain, name, socket | watts, joules |
| `ocloud_cpu` | run_id, node, condition, cpu, isolated | ps_busy_pct, idle_busy_pct |
| `ocloud_perf` | run_id, node, condition | ipc, mpki, instructions, cache_misses |
| `ocloud_freq` | run_id, node, condition | delivered_ratio, aperf, mperf |
| `ocloud_cstate` | run_id, node, condition | c1_residency, c6_residency |
| `ocloud_membw` | run_id, node, condition, socket | read_mib, write_mib, total_mib |
| `ocloud_thermal` | run_id, node, condition, zone | temp_c |

Compare conditions: `ocloud_power` / `watts` / `name=package-1`, filter
`condition=B` vs `condition=A` → the DU energy cost.

> **Note:** OAI runs carry no `ocloud_perf` / `ocloud_membw` data (PMU
> collectors disabled — see 2A).

## File / path reference

| Host | Item | Path |
|---|---|---|
| joule | agent | `~/ocloud-telemetry-agent/` |
| joule | experiment capture | `sudo ./capture.sh <stack> <A\|B\|C\|D> [rate]` |
| joule | analysis | `./analyze.sh [ocudu\|oai\|<date>]` |
| joule | auto-detect capture | `sudo ./tools/run_campaign.sh [--watch]` |
| joule | runs out | `/mnt/debugging-logs/runs/<run_id>/` |
| joule | OCUDU config source | `/mnt/gnb-runtime/gnb-config.yml` |
| galileo | shipper code | `~/ocloud-telemetry-agent/influxDB/influx.py` |
| galileo | launcher | `~/ocloud-telemetry-agent/influxDB/ship_influx.sh` |
| galileo | config | `/etc/ocloud-shipper.env` (mode 600) |
| galileo | service | `/etc/systemd/system/ocloud-shipper.service` |
| galileo | mirror | `~/ocloud-telemetry-agent/influxDB/runs-mirror/` |
| influxdb | target | `192.168.8.69:30138`, org `ravi-ric`, bucket `infra-telemetry` |

## Service control (galileo)

```bash
sudo systemctl status  ocloud-shipper     # state
sudo systemctl stop    ocloud-shipper     # pause shipping
sudo systemctl start   ocloud-shipper     # resume
sudo systemctl restart ocloud-shipper     # after editing /etc/ocloud-shipper.env
journalctl -u ocloud-shipper -e           # recent logs / errors
```

## After a reboot

- **galileo:** nothing to do — `ocloud-shipper` is enabled, auto-starts, resumes.
- **joule:** nothing persistent runs; just `capture.sh` when you next test.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ERROR: agent dir not found: /root/...` | `sudo` resets `$HOME`. Fixed in current `capture.sh` (resolves its own location); otherwise `sudo AGENT_DIR=$PWD ./capture.sh …` |
| `ModuleNotFoundError: No module named 'agent'` | wrong directory → `cd ~/ocloud-telemetry-agent` first |
| Agent hangs at startup on OAI | stuck OAI perf processes → `sudo pkill -9 -f "perf stat -e instructions"`; always use the `oai` stack arg (disables perf/uncore) |
| `threads SKIPPED: no process matching (...)` | only means *the process named in `GNB_COMM`* was absent — **not** that no gNB was running. Use the right `stack` arg. |
| Two conditions read the same watts | the state didn't actually change (UE didn't attach / traffic didn't flow). Verify and recapture. |
| `WARN: no gnb-config.yml` | expected for OAI (uses `du.conf`); for OCUDU check `/mnt/gnb-runtime/gnb-config.yml` exists |
| `missing env` (galileo) | env not loaded → use `ship_influx.sh` or the service (both read `/etc/ocloud-shipper.env`) |
| UI "No tag keys found" | set time range "Past 7 days" — data is at capture time |
| rsync permission denied `gnb-config.yml` | expected — gNB owns it, excluded from ship, kept on joule. Not an error. |
| rsync asks for password | SSH key missing → redo B2 |
| tests fail after clone | stale copy — re-clone; confirm `agent/collectors/uncore.py` exists |

---

## Quick reference

```bash
# joule — controlled experiment (per condition, 3 runs, state-verified):
cd ~/ocloud-telemetry-agent
sudo ./capture.sh ocudu A          # then B, C, D 100M, D 200M
./analyze.sh ocudu                 # review the ladder

# joule — unattended/continuous instead:
sudo ./tools/run_campaign.sh --watch      # Ctrl-C when the test ends

# galileo: already shipping via systemd — nothing to run
# view: InfluxDB/Grafana, bucket infra-telemetry, time range "Past 7 days"
```
