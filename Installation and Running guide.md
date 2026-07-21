# Installation & Running Guide

Two components on **two hosts**:

| Component | Runs on | Purpose |
|---|---|---|
| **agent** (`agent/`, `tools/`) | **joule** (workload node) | captures CPU/energy telemetry, writes run files |
| **shipper** (`influxDB/`) | **galileo** (has InfluxDB reach) | pulls run files, writes to InfluxDB |

> Why two hosts: joule runs the gNB and can read RAPL/PMU but **cannot reach
> InfluxDB**. galileo **can** reach InfluxDB and can ssh to joule. So joule
> captures, galileo ships.

```
JOULE                         GALILEO                       INFLUXDB
run_campaign.sh    --rsync-->  ocloud-shipper.service  ---->  infra-telemetry
 -> runs/<id>/*.csv            (influx.py, watch mode)        (Grafana / rApp read)
```

---

# INSTALLATION

## PART A — AGENT (on joule)

**Requirements:** Python 3.9+, `perf`, root (for RAPL/PMU). Standard library
only, no pip packages.

```bash
# on joule
git clone https://github.com/ontherays/ocloud-telemetry-agent.git
cd ocloud-telemetry-agent

python3 -m tests.test_correctness     # must end "all correctness tests passed"
bash tools/test_classify.sh           # 7 PASS
```

No configuration needed for capture — the agent auto-detects. Optional env
overrides (defaults shown): `WINDOW=30`, `ENABLE_UNCORE=true`, `NS=ravi-ns`,
`GNB_COMM=gnb`. Runs are written to `/mnt/debugging-logs/runs/<run_id>/`.

## PART B — SHIPPER (on galileo)

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

# RUNNING

## STEP 1 — capture on joule (while your test runs)

```bash
cd ~/ocloud-telemetry-agent
sudo ./tools/run_campaign.sh            # one 30s snapshot
sudo ./tools/run_campaign.sh --watch    # a run every ~30s during a test; Ctrl-C when done
```
> Use `--watch` while actively running a test (e.g. an iperf sweep), then
> **Ctrl-C when the test ends** so it stops making runs. Longer window:
> `sudo WINDOW=60 ./tools/run_campaign.sh`.

Auto-detected labels:

| Label | Meaning |
|---|---|
| `A-idle-no-gnb` | no gNB running |
| `B-gnb-idle` | gNB up, no UE |
| `B-ue-gnb-attached` | gNB up, UE attached, no traffic |
| `C-iperf` | gNB up, iperf running (driven by the r-App) |

Each run creates `/mnt/debugging-logs/runs/<run_id>/` containing:
`cores.csv power.csv perf.csv freq_delivered.csv cstate_hw.csv
membw_socket.csv thermal.csv cpufreq.csv manifest.json health.json
summary.json gnb-config.yml`.

**That's all you do on joule.** Do not run anything on galileo — the service
handles it.

## STEP 2 — galileo ships automatically (nothing to run)

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

## STEP 3 — view / consume

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

# REFERENCE

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

## File / path reference

| Host | Item | Path |
|---|---|---|
| joule | agent | `~/ocloud-telemetry-agent/` |
| joule | capture | `sudo ./tools/run_campaign.sh` |
| joule | runs out | `/mnt/debugging-logs/runs/<run_id>/` |
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
- **joule:** nothing persistent runs; just `run_campaign.sh` when you next test.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `missing env` | env not loaded → use `ship_influx.sh` or the service (both read `/etc/ocloud-shipper.env`) |
| UI "No tag keys found" | set time range "Past 7 days" — data is at capture time |
| rsync permission denied `gnb-config.yml` | expected — gNB owns it, excluded from ship, kept on joule. Not an error. |
| rsync asks for password | SSH key missing → redo B2 |
| tests fail after clone | stale copy — re-clone; confirm `agent/collectors/uncore.py` exists |

---

## Quick reference — the whole thing in 3 lines

```bash
# joule (per test):
cd ~/ocloud-telemetry-agent && sudo ./tools/run_campaign.sh    # or --watch during a sweep
# galileo: already shipping via systemd — nothing to run
# view: InfluxDB/Grafana, bucket infra-telemetry, time range "Past 7 days"
```