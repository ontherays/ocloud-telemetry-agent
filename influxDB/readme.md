# O-Cloud Telemetry → InfluxDB: Capture & Ship Guide

How to capture O-Cloud energy/CPU telemetry on **joule** and get it into
**InfluxDB** via **Galileo**. Covers the on-demand capture workflow, the
persistent shipper service, and recovery after a reboot.

---

## Topology (why it's split across two hosts)

| | joule (192.168.206.82) | galileo (192.168.8.35) |
|---|---|---|
| runs the agent, writes run files | ✓ | — |
| can reach InfluxDB (192.168.8.69:30138) | ✗ (times out) | ✓ |
| can ssh to joule | — | ✓ |

So: **joule captures → files → galileo ships to InfluxDB.**
InfluxDB org `ravi-ric`, bucket `infra-telemetry`.

```
joule: run_campaign.sh  ->  /mnt/debugging-logs/runs/<run_id>/*.csv
                                     |
                        galileo: rsync pulls the files
                                     |
                        galileo: influx-ship.py  ->  InfluxDB(infra-telemetry)
                                     |
                             Grafana / rApp read the bucket
```

---

## PART 1 — Capture on joule (ON DEMAND)

Capture is **on demand**, one run per experiment. Do NOT leave `--watch`
running: with the gNB down it makes an A-run every 30s and floods the bucket
with idle data.

```bash
# on joule, in the agent dir:
cd ~/ocloud-telemetry-agent
sudo ./tools/run_campaign.sh            # detect current state, capture 30s, exit
```

The framework auto-detects and labels the run:

| State | Meaning |
|---|---|
| `A-idle-no-gnb` | no gNB process |
| `B-gnb-idle` | gNB up, no UE, no traffic |
| `B-ue-gnb-attached` | gNB up, UE attached, no traffic |
| `C-iperf` | gNB up, iperf running (r-App-driven) |

Each run writes `/mnt/debugging-logs/runs/<run_id>/` with cores/power/perf/
freq/cstate/membw/thermal CSVs + manifest + health + the rendered gnb-config.

**Longer capture:** `sudo WINDOW=60 ./tools/run_campaign.sh`
**Continuous (only if you really want it):** `sudo ./tools/run_campaign.sh --watch`
(remember to Ctrl-C it — it captures every cycle forever).

---

## PART 2 — Ship to InfluxDB from galileo

### 2a. The persistent service (already set up)

A systemd service ships continuously every 60s and **survives reboots**:

```bash
sudo systemctl status ocloud-shipper       # is it running?
journalctl -u ocloud-shipper -f            # watch it ship (Ctrl-C to stop watching)
sudo systemctl stop ocloud-shipper         # stop shipping
sudo systemctl start ocloud-shipper        # resume
sudo systemctl disable ocloud-shipper      # don't auto-start on boot
sudo systemctl enable ocloud-shipper       # auto-start on boot
```

It reads config from `/etc/ocloud-shipper.env` (see Part 3) and runs
`influx-ship.py watch`. Because it's `Restart=on-failure`, a transient InfluxDB
outage self-heals.

### 2b. Manual / one-off ship (the launcher script)

When you'd rather ship by hand (e.g. right after a capture), use `ship.sh` — it
loads the env file so you never hand-set variables:

```bash
cd ~/ocloud-influx-shipper       # or wherever ship.sh lives
./ship.sh once                   # pull new runs + ship, then exit
./ship.sh watch                  # continuous (same as the service)
./ship.sh reship                 # force re-ship everything (idempotent)
```

> If the systemd service is already running `watch`, you don't need `ship.sh
> watch` too — one continuous shipper is enough. Use `./ship.sh once` for an
> immediate push without waiting for the 60s cycle.

---

## PART 3 — Config: the env file

All connection settings live in **`/etc/ocloud-shipper.env`** (root-only, 600).
The token is NEVER in a script or shell history.

```bash
sudo tee /etc/ocloud-shipper.env >/dev/null <<'ENVEOF'
INFLUX_URL=http://192.168.8.69:30138
INFLUX_ORG=ravi-ric
INFLUX_BUCKET=infra-telemetry
INFLUX_TOKEN=<your-write-token>
RSYNC_RSH=ssh -o BatchMode=yes -i /root/.ssh/joule_key
SHIP_INTERVAL=60
JOULE_RUNS=sysadmin@192.168.206.82:/mnt/debugging-logs/runs/
MIRROR_DIR=/root/ocloud-influx-shipper/runs-mirror
NODE_NAME=joule
ENVEOF
sudo chmod 600 /etc/ocloud-shipper.env
```

After editing it, restart the service so it picks up changes:
```bash
sudo systemctl restart ocloud-shipper
```

**SSH key (one-time, enables passwordless rsync galileo→joule):**
```bash
ssh-keygen -t ed25519 -f ~/.ssh/joule_key -N ''
ssh-copy-id -i ~/.ssh/joule_key.pub sysadmin@192.168.206.82
# verify (must NOT prompt for a password):
ssh -o BatchMode=yes -i ~/.ssh/joule_key sysadmin@192.168.206.82 'echo ok'
```

---

## PART 4 — After a reboot

**Galileo reboots:** nothing to do. The `ocloud-shipper` service is `enabled`,
so it auto-starts and resumes shipping. Confirm with
`sudo systemctl status ocloud-shipper`.

**Fresh terminal / lost env vars:** irrelevant now — the service and `ship.sh`
both read `/etc/ocloud-shipper.env`, not your shell. You never export vars by
hand again.

**joule reboots:** capture is on-demand, so just run `run_campaign.sh` when you
next want data. Any runs already on joule get shipped by galileo automatically.

---

## PART 5 — Verify data landed

**Quick count (from galileo):**
```bash
set -a; . /etc/ocloud-shipper.env; set +a
curl -s -X POST "$INFLUX_URL/api/v2/query?org=$INFLUX_ORG" \
  -H "Authorization: Token $INFLUX_TOKEN" \
  -H "Accept: application/csv" -H "Content-Type: application/vnd.flux" \
  --data-binary 'from(bucket:"infra-telemetry") |> range(start:-7d)
     |> filter(fn:(r)=>r._measurement=="ocloud_power" and r._field=="watts"
                    and r.name=="package-1")
     |> last()'
```

**InfluxDB UI:** Data Explorer → set time range **"Past 7 days"** (data is
timestamped at capture time, not ship time — the default 1h window hides it) →
bucket `infra-telemetry` → measurement `ocloud_power`.

---

## Measurements in InfluxDB

| measurement | tags | key fields |
|---|---|---|
| `ocloud_power` | run_id, node, condition, domain, name, socket | watts, joules |
| `ocloud_cpu` | run_id, node, condition, cpu, isolated | ps_busy_pct, idle_busy_pct |
| `ocloud_perf` | run_id, node, condition | ipc, mpki, instructions, cache_misses |
| `ocloud_freq` | run_id, node, condition | delivered_ratio, aperf, mperf |
| `ocloud_cstate` | run_id, node, condition | c1_residency, c6_residency |
| `ocloud_membw` | run_id, node, condition, socket | read_mib, write_mib, total_mib |
| `ocloud_thermal` | run_id, node, condition, zone | temp_c |

Slice by `condition` (A/B/C) and `run_id`. e.g. package-1 watts where
condition=B vs A gives the DU static energy cost.

---

## Grafana (consumer, no agent change)

Add an InfluxDB datasource: URL `http://192.168.8.69:30138`, org `ravi-ric`,
a **read** token, bucket `infra-telemetry`. Set the panel time range to cover
your capture dates. Query `ocloud_power` / `watts` / `name=package-1`, group by
`condition`. Grafana and any rApp are pure readers — the tag model is the
contract; they need nothing from the agent or shipper.

---

## Common issues

| Symptom | Cause / fix |
|---|---|
| "missing env" | env not loaded → use `ship.sh` or the service (both read the env file); if hand-running, `export` all four INFLUX_* incl. the token |
| UI shows "No tag keys found" | time range too narrow → set "Past 7 days"; data is timestamped at capture time |
| rsync permission denied on gnb-config.yml | expected — gNB owns it; excluded from ship, kept on joule. Not an error. |
| rsync asks for password | SSH key missing → redo `ssh-copy-id` (Part 3) |
| service not shipping | `journalctl -u ocloud-shipper -e` to see why; usually env file or SSH key |