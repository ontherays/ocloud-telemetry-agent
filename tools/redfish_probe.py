#!/usr/bin/env python3
"""Off-node Redfish sampler -> EC_node,platform (ES TR Table 6.2-1/6.2-2).

joule cannot route to its own BMC; 192.168.8.78 gets HTTP 200. So this runs
where the BMC is reachable and its CSV is joined to the agent's run by run_id.

    python3 redfish_probe.py --url https://192.168.8.222 --user root \
        --run-id 20260717T140000Z-B-gnb-idle --window 30 --interval 1 \
        --out ./redfish.csv

Password comes from REDFISH_PASSWORD, never argv (argv is world-readable in ps).
"""
import argparse
import base64
import csv
import getpass
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request


def get(url, user, pw, timeout=5.0):
    req = urllib.request.Request(url)
    if user:
        tok = base64.b64encode(("%s:%s" % (user, pw)).encode()).decode()
        req.add_header("Authorization", "Basic " + tok)
    req.add_header("Accept", "application/json")
    ctx = ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def read_power(base, chassis, user, pw):
    try:
        doc = get("%s/redfish/v1/Chassis/%s/Power" % (base, chassis), user, pw)
        pc = (doc.get("PowerControl") or [{}])[0]
        pm = pc.get("PowerMetrics") or {}
        return {
            "watts": pc.get("PowerConsumedWatts"),
            "capacity_watts": pc.get("PowerCapacityWatts"),
            "avg_watts": pm.get("AverageConsumedWatts"),
            "max_watts": pm.get("MaxConsumedWatts"),
            "min_watts": pm.get("MinConsumedWatts"),
        }
    except (urllib.error.HTTPError, urllib.error.URLError, OSError,
            ValueError, KeyError, IndexError) as e:
        return {"error": repr(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="https://<bmc-ip>  (no trailing /)")
    ap.add_argument("--user", default="root")
    ap.add_argument("--chassis", default="1")
    ap.add_argument("--run-id", required=True, help="must match the agent's run_id")
    ap.add_argument("--window", type=float, default=30.0)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--out", default="redfish.csv")
    a = ap.parse_args()

    pw = os.environ.get("REDFISH_PASSWORD") or getpass.getpass("Redfish password: ")
    base = a.url.rstrip("/")

    try:
        get("%s/redfish/v1/" % base, a.user, pw)
    except Exception as e:
        print("cannot reach %s: %r" % (base, e), file=sys.stderr)
        return 1

    fields = ["run_id", "t_utc", "t_epoch", "watts", "avg_watts",
              "max_watts", "min_watts", "capacity_watts"]
    new = not os.path.exists(a.out)
    n = 0
    with open(a.out, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        end = time.monotonic() + a.window
        while time.monotonic() < end:
            p = read_power(base, a.chassis, a.user, pw)
            if "error" in p:
                print("WARN %s" % p["error"], file=sys.stderr)
            else:
                now = time.time()
                w.writerow(dict(p, run_id=a.run_id, t_epoch=now,
                                t_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                    time.gmtime(now))))
                fh.flush()
                n += 1
            time.sleep(a.interval)
    print("wrote %d samples -> %s" % (n, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
