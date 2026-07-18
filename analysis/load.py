#!/usr/bin/env python3
"""Load a run directory into pandas.

    from analysis.load import load_run
    r = load_run("runs/20260717T140000Z-B-gnb-idle")
    r["cores"].query("isolated and not idle_stale").busy_pct.describe()

Rule enforced here so it cannot be forgotten in a notebook: rows with
idle_stale == True are NOT measurements. Drop them; never average them.
"""
import json
import os

import pandas as pd

TABLES = ("cores", "power", "threads", "perf", "redfish")


def load_run(d):
    out = {}
    mp = os.path.join(d, "manifest.json")
    if os.path.exists(mp):
        with open(mp) as f:
            out["manifest"] = json.load(f)
    sp = os.path.join(d, "summary.json")
    if os.path.exists(sp):
        with open(sp) as f:
            out["summary"] = json.load(f)
    for t in TABLES:
        p = os.path.join(d, "%s.csv" % t)
        if os.path.exists(p):
            out[t] = pd.read_csv(p)
    cp = os.path.join(d, "gnb-config.yml")
    if os.path.exists(cp):
        with open(cp) as f:
            out["gnb_config"] = f.read()
    return out


def package_watts(run, socket=None):
    p = run["power"]
    p = p[p.name.str.startswith("package")]
    if socket is not None:
        p = p[p.socket == socket]
    return p.groupby("name").watts.agg(["mean", "std", "min", "max", "count"])


def isolated_busy(run):
    c = run["cores"]
    c = c[(c.isolated == True) & (c.idle_stale != True)]  # noqa: E712
    return c.groupby("cpu").idle_busy_pct.agg(["mean", "std", "count"])


def ab_delta(run_a, run_b, socket=1):
    a = package_watts(run_a, socket)["mean"].sum()
    b = package_watts(run_b, socket)["mean"].sum()
    return {"a_watts": round(a, 3), "b_watts": round(b, 3),
            "delta_watts": round(b - a, 3)}
