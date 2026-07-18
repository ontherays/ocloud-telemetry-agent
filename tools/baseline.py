#!/usr/bin/env python3
"""
Point-in-time O-Cloud node baseline: per-core idle residency + RAPL energy.

Run as root on the O-Cloud node:
    sudo python3 baseline.py --window 10 --label idle-no-gnb --out /tmp/base

Correctness notes (each of these was a real bug in an earlier attempt):
  * ALL cpuidle states are idle, not just C1. POLL (state0) is idle too.
    busy = 1 - (sum of every state's residency) / elapsed
  * The measurement window is measured, never assumed. Reading 32 cores
    takes real time; 'sleep 10' is not a 10.00 s window.
  * RAPL wraps at max_energy_range_uj (~2^38 on Xeon), not 2^32.
  * RAPL domains are keyed by sysfs dir, not by name: 'dram' appears once
    per socket and would otherwise collide.
"""

import argparse
import json
import os
import time

CPU_ROOT = "/sys/devices/system/cpu"
POWERCAP = "/sys/class/powercap"


def read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def cpu_list():
    out = []
    for e in os.listdir(CPU_ROOT):
        if e.startswith("cpu") and e[3:].isdigit():
            out.append(int(e[3:]))
    return sorted(out)


def parse_cpuset(spec):
    s = set()
    if not spec:
        return s
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            s.update(range(int(a), int(b) + 1))
        else:
            s.add(int(part))
    return s


def idle_states(cpu):
    """[(index, name), ...] for every cpuidle state this cpu exposes."""
    d = "%s/cpu%d/cpuidle" % (CPU_ROOT, cpu)
    if not os.path.isdir(d):
        return []
    out = []
    for e in sorted(os.listdir(d)):
        if e.startswith("state") and e[5:].isdigit():
            out.append((int(e[5:]), read("%s/%s/name" % (d, e)) or e))
    return out


def snap_idle(cpus, states):
    """cpu -> {state_index: residency_us}"""
    snap = {}
    for c in cpus:
        d = "%s/cpu%d/cpuidle" % (CPU_ROOT, c)
        st = {}
        for idx, _ in states:
            v = read("%s/state%d/time" % (d, idx))
            if v is not None:
                st[idx] = int(v)
        snap[c] = st
    return snap


def rapl_domains():
    doms = {}
    if not os.path.isdir(POWERCAP):
        return doms
    for e in sorted(os.listdir(POWERCAP)):
        if not e.startswith("intel-rapl:"):
            continue
        d = os.path.join(POWERCAP, e)
        if read(os.path.join(d, "energy_uj")) is None:
            continue
        mx = read(os.path.join(d, "max_energy_range_uj"))
        doms[e] = {
            "name": read(os.path.join(d, "name")) or e,
            "path": os.path.join(d, "energy_uj"),
            "max": int(mx) if mx else None,
        }
    return doms


def snap_rapl(doms):
    out = {}
    for k, v in doms.items():
        t = read(v["path"])
        if t is not None:
            out[k] = int(t)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=float, default=10.0)
    ap.add_argument("--label", default="unlabeled")
    ap.add_argument("--out", default=None, help="write JSON here")
    a = ap.parse_args()

    cpus = cpu_list()
    states = idle_states(cpus[0]) if cpus else []
    if not states:
        print("WARN: no cpuidle states exposed - cannot compute idle residency")
    doms = rapl_domains()
    if not doms:
        print("WARN: no readable RAPL domains (need root; bare metal only)")

    isolated = parse_cpuset(read("%s/isolated" % CPU_ROOT))
    cmdline = read("/proc/cmdline") or ""

    # --- measure -----------------------------------------------------------
    ta0 = time.monotonic()
    i0 = snap_idle(cpus, states)
    e0 = snap_rapl(doms)
    ta1 = time.monotonic()

    time.sleep(a.window)

    tb0 = time.monotonic()
    i1 = snap_idle(cpus, states)
    e1 = snap_rapl(doms)
    tb1 = time.monotonic()

    # midpoint-to-midpoint: the reads themselves take time
    elapsed = ((tb0 + tb1) / 2) - ((ta0 + ta1) / 2)

    # --- cores -------------------------------------------------------------
    rows = []
    for c in cpus:
        per = {}
        tot_idle = 0.0
        for idx, name in states:
            if idx in i0[c] and idx in i1[c]:
                d = (i1[c][idx] - i0[c][idx]) / 1e6
                per[name] = round(d, 3)
                tot_idle += d
        busy = 100.0 * (1.0 - tot_idle / elapsed)
        rows.append({
            "cpu": c,
            "isolated": c in isolated,
            "idle_s": per,
            "idle_total_s": round(tot_idle, 3),
            "busy_pct": round(busy, 2),
        })

    # --- energy ------------------------------------------------------------
    power = {}
    for k in e0:
        if k not in e1:
            continue
        d = e1[k] - e0[k]
        if d < 0:
            if doms[k]["max"]:
                d += doms[k]["max"]
            else:
                continue
        power["%s(%s)" % (k, doms[k]["name"])] = {
            "joules": round(d / 1e6, 3),
            "watts": round((d / 1e6) / elapsed, 3),
        }

    result = {
        "label": a.label,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_s": round(elapsed, 4),
        "requested_window_s": a.window,
        "ncpu": len(cpus),
        "idle_states": [n for _, n in states],
        "isolated": sorted(isolated),
        "kernel_cmdline": cmdline,
        "cores": rows,
        "power": power,
    }

    # --- report ------------------------------------------------------------
    print("label=%s  window=%.4fs (requested %.1f)" % (a.label, elapsed, a.window))
    print("idle states exposed: %s" % ", ".join(n for _, n in states))
    print("isolated: %s\n" % (sorted(isolated) or "none"))

    print("cpu  iso  " + "  ".join("%9s" % n for _, n in states) + "     busy%")
    for r in rows:
        cells = "  ".join("%9.3f" % r["idle_s"].get(n, 0.0) for _, n in states)
        print("%3d  %-3s  %s  %8.2f" % (
            r["cpu"], "ISO" if r["isolated"] else "", cells, r["busy_pct"]))

    iso = [r["busy_pct"] for r in rows if r["isolated"]]
    hk = [r["busy_pct"] for r in rows if not r["isolated"]]
    print()
    if iso:
        print("isolated cores : min %.2f%%  max %.2f%%  mean %.2f%%"
              % (min(iso), max(iso), sum(iso) / len(iso)))
    if hk:
        print("housekeeping   : min %.2f%%  max %.2f%%  mean %.2f%%"
              % (min(hk), max(hk), sum(hk) / len(hk)))
    print()
    for k, v in sorted(power.items()):
        print("%-28s %10.3f J   %8.3f W" % (k, v["joules"], v["watts"]))
    if power:
        tot = sum(v["watts"] for k, v in power.items() if "package" in k)
        print("%-28s %10s     %8.3f W" % ("packages total", "", tot))

    if a.out:
        p = a.out if a.out.endswith(".json") else "%s-%s.json" % (a.out, a.label)
        with open(p, "w") as f:
            json.dump(result, f, indent=2)
        print("\nwrote %s" % p)


if __name__ == "__main__":
    main()
