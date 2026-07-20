#!/usr/bin/env python3
"""InfluxDB shipper for O-Cloud telemetry runs. Runs ON GALILEO.

Topology (verified 2026-07-19):
  * joule (192.168.206.82) runs the agent, writes run files, CANNOT reach
    InfluxDB (192.168.8.69:30138 times out).
  * galileo (192.168.8.35) CAN reach InfluxDB and CAN ssh to joule.
So galileo pulls joule's run files and ships them to InfluxDB. The agent on
joule is untouched; files remain authoritative there.

Flow:
  rsync joule:/mnt/debugging-logs/runs/  ->  local mirror
  for each run not yet shipped:
      parse CSVs + manifest -> line protocol -> POST /api/v2/write
      mark shipped
Idempotent: points carry measurement-time timestamps + run_id/condition tags,
so re-shipping overwrites identically (safe to retry).

Config via env (NEVER hard-code the token):
  INFLUX_URL      e.g. http://192.168.8.69:30138
  INFLUX_ORG      e.g. ravi-ric
  INFLUX_BUCKET   e.g. infra-telemetry
  INFLUX_TOKEN    write token (set on galileo only)
  JOULE_RUNS      remote runs dir      (default sysadmin@192.168.206.82:/mnt/debugging-logs/runs/)
  MIRROR_DIR      local mirror         (default ./runs-mirror)
  NODE_NAME       tag value for node   (default joule)
"""
import csv
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

URL = os.environ.get("INFLUX_URL", "").rstrip("/")
ORG = os.environ.get("INFLUX_ORG", "")
BUCKET = os.environ.get("INFLUX_BUCKET", "")
TOKEN = os.environ.get("INFLUX_TOKEN", "")
JOULE_RUNS = os.environ.get(
    "JOULE_RUNS", "sysadmin@192.168.206.82:/mnt/debugging-logs/runs/")
MIRROR = os.environ.get("MIRROR_DIR", "./runs-mirror")
NODE = os.environ.get("NODE_NAME", "joule")

SHIPPED_MARK = ".shipped"      # per-run marker file in the mirror


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)


# ---- line protocol helpers ------------------------------------------------
def esc_tag(v):
    return str(v).replace("\\", "\\\\").replace(" ", "\\ ").replace(
        ",", "\\,").replace("=", "\\=")


def to_ns(t_utc):
    """'2026-07-19T03:00:35Z' -> epoch ns. Returns None if unparseable."""
    if not t_utc:
        return None
    try:
        dt = datetime.strptime(t_utc, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        return int(dt.timestamp() * 1e9)
    except ValueError:
        return None


def line(measurement, tags, fields, ts_ns):
    """Build one line-protocol record. Skips if no numeric fields."""
    fparts = []
    for k, v in fields.items():
        if v is None or v == "":
            continue
        try:
            fparts.append("%s=%s" % (k, float(v)))   # all our fields numeric
        except (ValueError, TypeError):
            fparts.append('%s="%s"' % (k, str(v)))    # fallback string field
    if not fparts:
        return None
    tparts = ",".join("%s=%s" % (esc_tag(k), esc_tag(v))
                      for k, v in tags.items() if v not in (None, ""))
    head = measurement + ("," + tparts if tparts else "")
    tail = (" %d" % ts_ns) if ts_ns else ""
    return "%s %s%s" % (head, ",".join(fparts), tail)


# ---- run parsing ----------------------------------------------------------
def read_manifest(run_dir):
    import json
    p = os.path.join(run_dir, "manifest.json")
    try:
        with open(p) as f:
            m = json.load(f)
        return m
    except (OSError, ValueError):
        return {}


def condition_of(label):
    # label like 'A-idle-no-gnb', 'B-gnb-idle', 'B-ue-gnb-attached', 'C-iperf'
    if not label:
        return "unknown"
    return label.split("-")[0]        # A / B / C  (B-ue starts 'B')


def rows(run_dir, name):
    p = os.path.join(run_dir, name)
    if not os.path.exists(p):
        return
    with open(p) as f:
        for r in csv.DictReader(f):
            yield r


def build_lines(run_dir):
    m = read_manifest(run_dir)
    run_id = m.get("run_id") or os.path.basename(run_dir)
    label = m.get("label", "")
    cond = condition_of(label)
    base = {"run_id": run_id, "node": NODE, "condition": cond}
    out = []

    # power.csv -> ocloud_power
    for r in rows(run_dir, "power.csv"):
        ts = to_ns(r.get("t_utc"))
        out.append(line("ocloud_power",
                        dict(base, domain=r.get("domain"), name=r.get("name"),
                             socket=r.get("socket")),
                        {"watts": r.get("watts"), "joules": r.get("joules")}, ts))

    # cores.csv -> ocloud_cpu  (skip stale idle rows for idle_busy_pct)
    for r in rows(run_dir, "cores.csv"):
        ts = to_ns(r.get("t_utc"))
        f = {"ps_busy_pct": r.get("ps_busy_pct")}
        if str(r.get("idle_stale")).lower() not in ("true", "1"):
            f["idle_busy_pct"] = r.get("idle_busy_pct")
        out.append(line("ocloud_cpu",
                        dict(base, cpu=r.get("cpu"), isolated=r.get("isolated")),
                        f, ts))

    # perf.csv -> ocloud_perf
    for r in rows(run_dir, "perf.csv"):
        ts = to_ns(r.get("t_utc"))
        out.append(line("ocloud_perf", dict(base),
                        {"ipc": r.get("ipc"), "mpki": r.get("mpki"),
                         "instructions": r.get("instructions"),
                         "cache_misses": r.get("cache_misses"),
                         "context_switches": r.get("context_switches")}, ts))

    # freq_delivered.csv -> ocloud_freq
    for r in rows(run_dir, "freq_delivered.csv"):
        ts = to_ns(r.get("t_utc"))
        out.append(line("ocloud_freq", dict(base),
                        {"delivered_ratio": r.get("delivered_ratio"),
                         "aperf": r.get("aperf"), "mperf": r.get("mperf")}, ts))

    # cstate_hw.csv -> ocloud_cstate
    for r in rows(run_dir, "cstate_hw.csv"):
        ts = to_ns(r.get("t_utc"))
        out.append(line("ocloud_cstate", dict(base),
                        {"c1_residency": r.get("c1_residency"),
                         "c6_residency": r.get("c6_residency")}, ts))

    # membw_socket.csv -> ocloud_membw
    for r in rows(run_dir, "membw_socket.csv"):
        ts = to_ns(r.get("t_utc"))
        out.append(line("ocloud_membw", dict(base, socket=r.get("socket")),
                        {"read_mib": r.get("read_mib"),
                         "write_mib": r.get("write_mib"),
                         "total_mib": r.get("total_mib")}, ts))

    # thermal.csv -> ocloud_thermal
    for r in rows(run_dir, "thermal.csv"):
        ts = to_ns(r.get("t_utc"))
        out.append(line("ocloud_thermal",
                        dict(base, zone=r.get("zone"), type=r.get("type")),
                        {"temp_c": r.get("temp_c")}, ts))

    return [x for x in out if x]


# ---- influx write ---------------------------------------------------------
def write_influx(lines):
    if not lines:
        return True, "no lines"
    body = "\n".join(lines).encode()
    url = ("%s/api/v2/write?org=%s&bucket=%s&precision=ns"
           % (URL, urllib.parse.quote(ORG), urllib.parse.quote(BUCKET)))
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", "Token " + TOKEN)
    req.add_header("Content-Type", "text/plain; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return (resp.status in (200, 204)), "HTTP %s" % resp.status
    except urllib.error.HTTPError as e:
        return False, "HTTP %s: %s" % (e.code, e.read(300).decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError) as e:
        return False, repr(e)


import urllib.parse  # noqa: E402  (used above)


# ---- orchestration --------------------------------------------------------
def rsync_pull():
    os.makedirs(MIRROR, exist_ok=True)
    # Respect RSYNC_RSH if the user set a custom key; else default to a
    # non-interactive ssh (BatchMode so a missing key fails fast instead of
    # hanging on a password prompt).
    cmd = ["rsync", "-a", "--timeout=30"]
    if not os.environ.get("RSYNC_RSH"):
        cmd += ["-e", "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"]
    cmd += [JOULE_RUNS, MIRROR + "/"]
    rc = subprocess.run(cmd, capture_output=True, text=True)
    if rc.returncode != 0:
        log("rsync failed: %s" % (rc.stderr.strip() or rc.returncode))
        log("  (need key-based SSH galileo->joule: BatchMode blocks password prompts)")
        return False
    return True


def ship_all(force=False):
    if not os.path.isdir(MIRROR):
        log("no mirror dir yet")
        return
    shipped = 0
    for name in sorted(os.listdir(MIRROR)):
        run_dir = os.path.join(MIRROR, name)
        if not os.path.isdir(run_dir):
            continue
        mark = os.path.join(run_dir, SHIPPED_MARK)
        if os.path.exists(mark) and not force:
            continue
        lines = build_lines(run_dir)
        ok, msg = write_influx(lines)
        if ok:
            open(mark, "w").write(time.strftime("%Y-%m-%dT%H:%M:%SZ"))
            log("shipped %s (%d points) -> %s" % (name, len(lines), BUCKET))
            shipped += 1
        else:
            log("FAILED %s: %s" % (name, msg))
    if shipped == 0:
        log("nothing new to ship")


def main():
    if not (URL and ORG and BUCKET and TOKEN):
        log("missing env: need INFLUX_URL, INFLUX_ORG, INFLUX_BUCKET, INFLUX_TOKEN")
        return 2
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "once":
        if rsync_pull():
            ship_all()
    elif mode == "watch":
        interval = float(os.environ.get("SHIP_INTERVAL", 60))
        log("watch mode, every %ss" % interval)
        while True:
            if rsync_pull():
                ship_all()
            time.sleep(interval)
    elif mode == "reship":       # force re-ship everything (idempotent)
        ship_all(force=True)
    elif mode == "local":        # ship an already-local dir, no rsync (testing)
        ship_all()
    else:
        log("usage: influx_ship.py [once|watch|reship|local]")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())