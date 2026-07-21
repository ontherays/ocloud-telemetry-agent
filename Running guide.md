# Running Guide — Capture O-Cloud Telemetry to InfluxDB


---

## The flow (what happens when you capture)

```
JOULE                          GALILEO                         INFLUXDB
run_campaign.sh                ocloud-shipper.service          bucket: infra-telemetry
   |  writes files                |  (systemd, always running)     ^
   v                              |  rsync pull every 60s          |
/mnt/debugging-logs/runs/  ---->  /root/.../runs-mirror/  ------>  writes points
   <run_id>/*.csv                 influx-ship.py watch            (Grafana / rApp read)
```

You run **one command on joule**. Galileo does the rest by itself.

---

## Hosts, paths, names (reference)

| Host | Item | Exact path / name |
|---|---|---|
| **joule** 192.168.206.82 | agent directory | `~/ocloud-telemetry-agent/` |
| | capture command | `sudo ./tools/run_campaign.sh` |
| | output runs | `/mnt/debugging-logs/runs/<run_id>/` |
| **galileo** 192.168.8.35 | shipper directory | `/root/ocloud-influx-shipper/` |
| | shipper script | `influx-ship.py`  *(hyphen)* |
| | env / config file | `/etc/ocloud-shipper.env`  *(mode 600)* |
| | systemd service | `ocloud-shipper.service` |
| | local mirror of runs | `/root/ocloud-influx-shipper/runs-mirror/` |
| **influxdb** 192.168.8.69:30138 | org / bucket | `ravi-ric` / `infra-telemetry` |

---

## STEP 0 — one-time check (Galileo shipper is up)

The shipper is a persistent systemd service; confirm it's running:

```bash
# on galileo
sudo systemctl status ocloud-shipper
```
Expect `active (running)` and `enabled`. If not:
```bash
sudo systemctl enable --now ocloud-shipper
```
You do this once. It survives reboots. **Leave it running.**

---

## STEP 1 — capture on joule (while your test runs)

```bash
# on joule
cd ~/ocloud-telemetry-agent
```

**One snapshot** (30 s), for a single point in a test:
```bash
sudo ./tools/run_campaign.sh
```

**Continuous** capture for the duration of a test (a run every ~30 s):
```bash
sudo ./tools/run_campaign.sh --watch
```
> Use `--watch` while actively running a test (e.g. an iperf sweep), then
> **Ctrl-C when the test ends** so it stops making runs. Longer window:
> `sudo WINDOW=60 ./tools/run_campaign.sh`.

The command auto-detects and labels each run:

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

**That's all you do on joule.** Do not run anything on Galileo — the service
already handles it.

---

## STEP 2 — Galileo ships automatically (nothing to run)

Within ~60 s of a run finishing on joule, the `ocloud-shipper` service:
1. `rsync`-pulls new run dirs from joule → `runs-mirror/`
   (skips `gnb-config.yml` — gNB owns it; kept on joule),
2. converts each run's CSVs to InfluxDB line protocol,
3. writes to `infra-telemetry`, tagged with `run_id` + `condition` + `node` +
   `socket`/`cpu`,
4. marks the run shipped (won't re-ship).

Watch it happen (optional):
```bash
# on galileo
journalctl -u ocloud-shipper -f
```
You'll see `shipped <run_id> (N points) -> infra-telemetry`. Ctrl-C stops
watching (does not stop the service).

**Want it shipped immediately** instead of waiting up to 60 s? On galileo:
```bash
cd /root/ocloud-influx-shipper
./ship.sh once
```

---

## STEP 3 — view / consume

### InfluxDB UI
Data Explorer → **time range top-right = "Past 7 days"** (data is stamped at
capture time, not ship time; the default 1 h window hides it) → bucket
`infra-telemetry` → measurement e.g. `ocloud_power`.

### Grafana
Datasource: URL `http://192.168.8.69:30138`, org `ravi-ric`, bucket
`infra-telemetry`, a **read** token. Panel query: `ocloud_power`, field `watts`,
`name=package-1`, group by `condition`. Set panel time range to your capture
dates.

### rApp
Reads the same bucket via the InfluxDB API, using `run_id` / `condition` tags,
and/or polls the agent's `/health`. No change needed on joule or galileo — the
tags are the contract.

---

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

---

## Service control (Galileo, for reference)

```bash
sudo systemctl status  ocloud-shipper     # state
sudo systemctl stop    ocloud-shipper     # pause shipping
sudo systemctl start   ocloud-shipper     # resume
sudo systemctl restart ocloud-shipper     # after editing /etc/ocloud-shipper.env
journalctl -u ocloud-shipper -e           # recent logs / errors
```

Config lives in `/etc/ocloud-shipper.env` (INFLUX_URL, INFLUX_ORG,
INFLUX_BUCKET, INFLUX_TOKEN, RSYNC_RSH, SHIP_INTERVAL). After editing, restart
the service.

---

## After a reboot

- **Galileo:** nothing to do — `ocloud-shipper` is enabled, auto-starts, resumes.
- **joule:** nothing persistent runs; just `run_campaign.sh` when you next test.

---

## Quick reference — the whole thing in 3 lines

```bash
# joule (per test):
cd ~/ocloud-telemetry-agent && sudo ./tools/run_campaign.sh          # or --watch during a sweep
# galileo: already shipping via systemd — nothing to run
# view: InfluxDB/Grafana, bucket infra-telemetry, time range "Past 7 days"
```
