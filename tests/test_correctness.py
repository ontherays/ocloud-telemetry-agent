"""Regression tests for logic that cannot be exercised without the real node.

Every case here is a bug that actually occurred on joule.
Run:  python3 -m tests.test_correctness
"""
import sys

from agent.collectors.cpuidle import STALE_RATIO, CpuIdleCollector
from agent.collectors.rapl import RaplCollector
from agent.util import parse_cpuset, parse_stat

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s  %s" % (name, detail))
        FAIL.append(name)


def test_stale_guard():
    print("\nstale-counter guard (joule 2026-07-17: 28.000s/32.000s constants)")
    c = CpuIdleCollector.__new__(CpuIdleCollector)
    c._cpus = [3, 5, 9]
    c._states = [(0, "POLL"), (1, "C1")]
    c._isolated = {3, 5, 9}

    W = 30.0682
    us = lambda s: int(s * 1e6)
    s0 = {3: {0: 0, 1: 0}, 5: {0: 0, 1: 0}, 9: {0: 0, 1: 0}}
    s1 = {
        3: {0: 0, 1: us(14.196)},   # real work: 52.8% busy
        5: {0: 0, 1: us(28.080)},   # the 28.0 constant -> stale
        9: {0: 0, 1: us(30.239)},   # idle > window -> stale (was negative busy)
    }
    rows = {r["cpu"]: r for r in c.delta(s0, s1, W)["cores"]}

    check("real work reports a busy figure", rows[3]["busy_pct"] is not None)
    check("real work value correct",
          abs(rows[3]["busy_pct"] - 52.80) < 0.05, rows[3]["busy_pct"])
    check("28.0s constant flagged stale", rows[5]["stale"] is True)
    check("28.0s constant reports no busy figure", rows[5]["busy_pct"] is None)
    check("idle>window flagged stale", rows[9]["stale"] is True)
    check("idle>window never yields negative busy", rows[9]["busy_pct"] is None)
    check("STALE_RATIO between real (0.47) and idle (0.93)",
          0.47 < STALE_RATIO <= 0.94, STALE_RATIO)


def test_all_idle_states_count():
    print("\nall idle states count, not just C1")
    c = CpuIdleCollector.__new__(CpuIdleCollector)
    c._cpus = [0]
    c._states = [(0, "POLL"), (1, "C1")]
    c._isolated = set()
    W = 10.0
    # 6s POLL + 3s C1 = 9s idle -> 10% busy. Counting only C1 gives 70%.
    s0 = {0: {0: 0, 1: 0}}
    s1 = {0: {0: 6_000_000, 1: 3_000_000}}
    r = c.delta(s0, s1, W)["cores"][0]
    check("POLL is counted as idle", abs(r["busy_pct"] - 10.0) < 0.01, r["busy_pct"])
    check("not the C1-only answer (70%)", abs(r["busy_pct"] - 70.0) > 1)


def test_rapl_wrap():
    print("\nRAPL wraparound uses max_energy_range_uj (2^38), not 2^32")
    c = RaplCollector.__new__(RaplCollector)
    MAX = 262143328850            # joule's actual value
    c.domains = {"intel-rapl:1": {"name": "package-1", "path": "/dev/null",
                                  "max": MAX, "socket": 1},
                 "intel-rapl:9": {"name": "nomax", "path": "/dev/null",
                                  "max": None, "socket": 9}}
    c.errors = []
    s0 = {"intel-rapl:1": MAX - 1_000_000, "intel-rapl:9": 500}
    s1 = {"intel-rapl:1": 1_000_000, "intel-rapl:9": 100}   # both wrapped
    rows = {r["domain"]: r for r in c.delta(s0, s1, 1.0)["domains"]}
    check("wrap corrected", rows["intel-rapl:1"]["wrapped"] is True)
    check("wrap value = 2 J", abs(rows["intel-rapl:1"]["joules"] - 2.0) < 1e-6,
          rows["intel-rapl:1"]["joules"])
    check("wrap with 2^32 would be wrong",
          abs(rows["intel-rapl:1"]["joules"] - (-1_000_000 + 2**32) / 1e6) > 1)
    check("no max -> sample dropped, never guessed", "intel-rapl:9" not in rows)


def test_rapl_keyed_by_dir():
    print("\nRAPL keyed by sysfs dir, not name (dram appears once per socket)")
    c = RaplCollector.__new__(RaplCollector)
    c.domains = {"intel-rapl:0:0": {"name": "dram", "path": "", "max": 1 << 38, "socket": 0},
                 "intel-rapl:1:0": {"name": "dram", "path": "", "max": 1 << 38, "socket": 1}}
    c.errors = []
    out = c.delta({"intel-rapl:0:0": 0, "intel-rapl:1:0": 0},
                  {"intel-rapl:0:0": 1_000_000, "intel-rapl:1:0": 2_000_000},
                  1.0)["domains"]
    check("both dram domains survive", len(out) == 2, len(out))
    check("sockets distinguished", {r["socket"] for r in out} == {0, 1})


def test_parse_stat():
    print("\n/proc/stat parsing anchors on the LAST ')'")
    # fields 3..13 = state,ppid,pgrp,session,tty_nr,tpgid,flags,minflt,
    # cminflt,majflt,cmajflt  -> 11 entries, so utime (field 14) is index 11.
    f = ["S", "1", "1", "0", "-1", "4194560"] + ["0"] * 5   # idx 0..10
    f += ["1234", "5678"]                                    # idx 11,12 utime,stime
    f += ["0"] * 23                                          # idx 13..35
    f += ["17"]                                              # idx 36 processor
    raw = "42 (weird (name) here) " + " ".join(f)
    comm, fields = parse_stat(raw)
    check("comm with spaces and parens", comm == "weird (name) here", comm)
    check("utime at fields[11]", fields[11] == "1234", fields[11])
    check("stime at fields[12]", fields[12] == "5678", fields[12])
    check("processor at fields[36]", fields[36] == "17", fields[36])
    check("naive split() would be wrong", raw.split()[1] != comm, raw.split()[1])


def test_cpuset():
    print("\ncpuset parsing (joule isolated=3,5,...,31)")
    check("ranges + singles", parse_cpuset("0-3,8,10-11") == {0, 1, 2, 3, 8, 10, 11})
    check("joule isolated", len(parse_cpuset("3,5,7,9,11,13,15,17,19,21,23,25,27,29,31")) == 15)
    check("empty is empty", parse_cpuset("") == set())
    check("None is empty", parse_cpuset(None) == set())


def test_membw_per_node():
    print("\nmemory bandwidth via perf --per-node (verified format, F17 fixed)")
    from agent.collectors.uncore import UncoreCollector
    import agent.collectors.uncore as U

    # Exact -x , --per-node output captured from joule 2026-07-18:
    real_lines = [
        "N0,1,199.87,MiB,uncore_imc/cas_count_read/,12074958183,100.00,,",
        "N0,1,89.92,MiB,uncore_imc/cas_count_write/,12070278197,100.00,,",
        "N1,1,293.57,MiB,uncore_imc/cas_count_read/,12020030181,100.00,,",
        "N1,1,226.15,MiB,uncore_imc/cas_count_write/,12015468348,100.00,,",
    ]

    class FakeProc:
        returncode = 0
        stderr = "\n".join(real_lines)
    real_run = U.subprocess.run
    U.subprocess.run = lambda *a, **k: FakeProc()
    try:
        nodes = U._run_per_node(
            ["uncore_imc/cas_count_read/", "uncore_imc/cas_count_write/"], 1.0)
    finally:
        U.subprocess.run = real_run

    check("both nodes parsed", set(nodes.keys()) == {0, 1}, nodes)
    check("N0 read = 199.87",
          abs(nodes[0]["uncore_imc/cas_count_read/"] - 199.87) < 1e-6)
    check("N1 read = 293.57 (socket 1, the gNB socket)",
          abs(nodes[1]["uncore_imc/cas_count_read/"] - 293.57) < 1e-6)
    check("N1 write = 226.15",
          abs(nodes[1]["uncore_imc/cas_count_write/"] - 226.15) < 1e-6)

    # And the full measure() assembly, freq + membw together
    def fake_run(events, window, cpus=None):
        j = "".join(events)
        if "aperf" in j:
            return {"msr/aperf/": 1480.0, "msr/mperf/": 1000.0, "msr/tsc/": 1000.0}
        if "c1-residency" in j:
            return {"cstate_core/c1-residency/": 22316784.0,
                    "cstate_core/c6-residency/": 0.0}
        return {}
    def fake_per_node(events, window):
        return {0: {"uncore_imc/cas_count_read/": 199.87,
                    "uncore_imc/cas_count_write/": 89.92},
                1: {"uncore_imc/cas_count_read/": 293.57,
                    "uncore_imc/cas_count_write/": 226.15}}
    rr, rpn = U._run, U._run_per_node
    U._run, U._run_per_node = fake_run, fake_per_node
    c = UncoreCollector.__new__(UncoreCollector)
    c.window = 1.0; c.target_cpus = [5]; c.enable_probe = False; c._n_imc = 12; c._err = ""
    try:
        r = c.measure(1.0)
    finally:
        U._run, U._run_per_node = rr, rpn
    ps = r["mem_bw"]["per_socket"]
    check("socket 1 total = 293.57+226.15 = 519.72",
          abs(ps[1]["total_mib"] - 519.72) < 1e-6, ps[1])
    check("delivered ratio 1.48", abs(r["freq"]["delivered_ratio"] - 1.48) < 1e-6)
    check("c6 residency 0", r["cstate"]["c6_residency"] == 0.0)


def test_health_parsing():
    print("\nhealth: pmc + VF parsing + drift (real joule formats)")
    from agent.collectors.health import HealthCollector
    import agent.collectors.health as H

    h = HealthCollector(gm_identity="e8c57a.fffe.9053e9")

    # --- pmc parse (from the joule PTP guide "healthy" snippet) ---
    pmc_out = ("b49691.fffe.cf0a80-0 seq 0 RESPONSE MANAGEMENT TIME_STATUS_NP\n"
               "\tmaster_offset              1\n"
               "\tingress_time               1776252134391273179\n"
               "\tgmPresent                  true\n"
               "\tgmIdentity                 e8c57a.fffe.9053e9\n")
    real_sh, real_which, real_root = H._sh, H.shutil.which, H._is_root
    H._sh = lambda cmd, timeout=6: (0, pmc_out, "") if cmd[0] == "pmc" else (0, "", "")
    H.shutil.which = lambda x: "/usr/bin/" + x
    H._is_root = lambda: True
    try:
        p = h._ptp()
    finally:
        H._sh, H.shutil.which, H._is_root = real_sh, real_which, real_root
    check("master_offset parsed", p["master_offset_ns"] == 1)
    check("gm_present true", p["gm_present"] is True)
    check("sync_ok (offset<100, gm present, id match)", p["sync_ok"] is True)

    # offset over threshold -> not ok
    bad = pmc_out.replace("master_offset              1",
                          "master_offset              350")
    H._sh = lambda cmd, timeout=6: (0, bad, "") if cmd[0] == "pmc" else (0, "", "")
    H.shutil.which = lambda x: "/usr/bin/" + x
    H._is_root = lambda: True
    try:
        p2 = h._ptp()
    finally:
        H._sh, H.shutil.which, H._is_root = real_sh, real_which, real_root
    check("offset 350ns -> sync not ok", p2["sync_ok"] is False)

    # --- VF parse (from the user's ip -s link snippet) ---
    ip_out = (
        "5: enp202s0f0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 9000\n"
        "    vf 3     link/ether 00:11:22:33:44:55 brd ff:ff:ff:ff:ff:ff, "
        "spoof checking off, link-state auto, trust on\n"
        "    RX: bytes  packets\n"
        "    349529532565    64607641\n"
        "    TX: bytes  packets  dropped\n"
        "    20241255227074    2833347437    0\n")
    real_sh2 = H._sh
    H._sh = lambda cmd, timeout=6: (0, ip_out, "")
    real_rt = H.read_text
    H.read_text = lambda p: "up" if "operstate" in p else None
    try:
        nic = h._nic_vf()
    finally:
        H._sh = real_sh2
        H.read_text = real_rt
    check("VF spoof off detected", nic["spoof_off"] is True)
    check("VF link-state auto", nic["link_state"] == "auto")
    check("VF trust on", nic["trust_on"] is True)
    check("VF tx_dropped=0", nic["tx_dropped"] == 0)

    # --- drift: first run captures baseline, no false alarm ---
    h2 = HealthCollector()
    real_sh3 = H._sh
    H._sh = lambda cmd, timeout=6: (1, "", "not root")   # iptables unavailable
    H.read_text = lambda p: "0\n" if "route" in p else None
    try:
        r1 = h2.check(force=True)
        r2 = h2.check(force=True)
    finally:
        H._sh = real_sh3
        H.read_text = real_rt
    check("first run captures baseline", r1["drift"]["baseline_captured"] is True)
    check("second run no false route drift", r2["drift"]["route_changed"] is False)

if __name__ == "__main__":
    for fn in (test_stale_guard, test_all_idle_states_count, test_rapl_wrap,
               test_rapl_keyed_by_dir, test_parse_stat, test_cpuset,
               test_membw_per_node,
               test_health_parsing):
        fn()
    print("\n%s" % ("-" * 52))
    if FAIL:
        print("FAILED: %s" % ", ".join(FAIL))
        sys.exit(1)
    print("all correctness tests passed")
