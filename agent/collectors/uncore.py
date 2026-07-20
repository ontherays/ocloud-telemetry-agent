"""Uncore + MSR counters via perf. Validated readable on joule 2026-07-18.

Three groups, each probed and confirmed on the target before this was written
(the earlier "uncore PMU absent" conclusion was a wrong event NAME, not a
missing PMU -- uncore_upi_0, uncore_imc_N and the msr PMU are all present):

  1. Delivered frequency   msr/aperf/, msr/mperf/, msr/tsc/
       aperf/mperf = delivered/base freq ratio. Under AVX-512 (x86-64-v4)
       this diverges from scaling_cur_freq, which is only the REQUESTED freq.
       Throttle-proofs the energy comparison during the traffic sweep.

  2. Hardware C-states      cstate_core/c1-residency/, /c6-residency/
       An INDEPENDENT path to residency, not the sysfs cpuidle/time counters
       that went stale on isolated tickless cores. Cross-check for finding M1.
       Confirmed on joule: cpu5 c1=22316784, c6=0 (C6 genuinely never entered).

  3. Memory bandwidth       uncore_imc_N/cas_count_read/, /cas_count_write/
       Per-IMC. joule has 16 IMC instances, each cpumask = a 2-CPU group, so
       per-socket bandwidth = sum of the IMCs whose cpumask lands on that
       socket. Reported BOTH raw (per IMC) and summed (per socket).

Needs CAP_PERFMON (the DaemonSet has it) or root. Fully optional: if perf or
any event is unreadable, the run proceeds and the manifest records why.

NOT collected here (probe-only, off by default, see PROBE_EVENTS):
  * UPI cross-socket  -- fronthaul VF is NUMA-aligned (F13); confirm it matters
                         under load before instrumenting.
  * AVX-512 clipping  -- reads 0 at idle; wire only if nonzero under the sweep.
"""
import os
import shutil
import subprocess


FREQ_EVENTS = ["msr/aperf/", "msr/mperf/", "msr/tsc/"]
CSTATE_EVENTS = ["cstate_core/c1-residency/", "cstate_core/c6-residency/"]

# Probe-only. Not measured unless UNCORE_PROBE=1 and confirmed relevant.
PROBE_EVENTS = {
    "upi_tx": "uncore_upi_0/event=0x2,umask=0x0f/",
    "avx512_clip": "uncore_pcu/event=0x74/",
}


def _perf_ok():
    return shutil.which("perf") is not None


def _run(events, window, cpus=None):
    """One `perf stat -x , -e ... -- sleep window`. Returns {event: float}."""
    cmd = ["perf", "stat", "-x", ","]
    if cpus:
        cmd += ["-C", ",".join(str(c) for c in cpus)]
    else:
        cmd += ["-a"]
    for e in events:
        cmd += ["-e", e]
    cmd += ["--", "sleep", str(window)]
    try:
        rc = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=window + 8)
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"__error__": repr(e)}
    out = {}
    for line in rc.stderr.splitlines():
        p = line.split(",")
        if len(p) < 3:
            continue
        raw, name = p[0], p[2]
        if raw in ("<not counted>", "<not supported>", ""):
            continue
        try:
            out[name] = float(raw)
        except ValueError:
            continue
    if not out and rc.returncode != 0:
        out["__error__"] = (rc.stderr.strip().splitlines() or ["perf failed"])[-1]
    return out


def _run_per_node(events, window):
    """`perf stat -x , -a --per-node ...`. Returns {node_int: {event: value}}.

    Verified format on joule 2026-07-18 (kernel 6.6-rt):
        N0,1,199.87,MiB,uncore_imc/cas_count_read/,12074958183,100.00,,
        field[0]=node id, field[2]=value, field[4]=event name
    perf's --per-node uses its OWN authoritative IMC->node map. The sysfs
    cpumask does NOT give socket (it names the reader CPU: all IMCs showed
    cpumask 0-1). So we let perf do the split rather than mapping ourselves.
    """
    cmd = ["perf", "stat", "-x", ",", "-a", "--per-node"]
    for e in events:
        cmd += ["-e", e]
    cmd += ["--", "sleep", str(window)]
    try:
        rc = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=window + 8)
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"__error__": repr(e)}
    out = {}
    for line in rc.stderr.splitlines():
        p = line.split(",")
        if len(p) < 5:
            continue
        node_tag = p[0].strip()
        if not (node_tag.startswith("N") and node_tag[1:].isdigit()):
            continue
        raw, name = p[2], p[4]
        if raw in ("<not counted>", "<not supported>", ""):
            continue
        try:
            val = float(raw)
        except ValueError:
            continue
        out.setdefault(int(node_tag[1:]), {})[name] = val
    if not out and rc.returncode != 0:
        out["__error__"] = (rc.stderr.strip().splitlines() or ["perf failed"])[-1]
    return out


class UncoreCollector:
    name = "uncore"
    optional = True

    def __init__(self, window=1.0, target_cpus=None, enable_probe=False):
        self.window = window
        self.target_cpus = target_cpus or []          # for cstate/freq sampling
        self.enable_probe = enable_probe
        self._n_imc = self._count_imc()
        self._err = ""

    @staticmethod
    def _count_imc():
        """Count real IMC PMUs (excluding free_running) for the manifest.
        Socket attribution is done by perf --per-node, not by us."""
        try:
            return len([e for e in os.listdir("/sys/devices")
                        if e.startswith("uncore_imc_")
                        and "free_running" not in e])
        except OSError:
            return 0

    def available(self):
        if not _perf_ok():
            self._err = "perf(1) not in PATH"
            return False
        probe = _run(["instructions"], 0.05)
        if "__error__" in probe:
            self._err = "perf denied: %s (need CAP_PERFMON or paranoid<=0)" % probe["__error__"]
            return False
        return True

    def unavailable_reason(self):
        return self._err

    def static(self):
        return {
            "freq_events": FREQ_EVENTS,
            "cstate_events": CSTATE_EVENTS,
            "n_imc": self._n_imc,
            "membw_method": "perf --per-node (authoritative IMC->node map)",
            "probe_enabled": self.enable_probe,
            "target_cpus": self.target_cpus,
        }

    def snapshot(self):
        return {}

    def measure(self, window=None):
        w = window or self.window
        result = {"window_s": w}

        # 1. delivered frequency (system-wide is fine; ratio is per-run)
        freq = _run(FREQ_EVENTS, w)
        a, m, t = freq.get("msr/aperf/"), freq.get("msr/mperf/"), freq.get("msr/tsc/")
        result["freq"] = {"aperf": a, "mperf": m, "tsc": t,
                          "delivered_ratio": round(a / m, 4) if a and m else None}

        # 2. hardware C-states, on the isolated cores if we know them
        cs = _run(CSTATE_EVENTS, w, cpus=self.target_cpus or None)
        result["cstate"] = {
            "c1_residency": cs.get("cstate_core/c1-residency/"),
            "c6_residency": cs.get("cstate_core/c6-residency/"),
        }

        # 3. memory bandwidth per SOCKET, via perf's own IMC->node map.
        #    (The sysfs cpumask names the reader CPU, not the socket -- all
        #    IMCs report cpumask 0-1 -- so we must NOT map it ourselves.)
        nodes = _run_per_node(
            ["uncore_imc/cas_count_read/", "uncore_imc/cas_count_write/"], w)
        per_socket = {}
        if "__error__" not in nodes:
            for node, ev in nodes.items():
                rd = ev.get("uncore_imc/cas_count_read/")
                wr = ev.get("uncore_imc/cas_count_write/")
                per_socket[node] = {
                    "read_mib": round(rd, 3) if rd is not None else None,
                    "write_mib": round(wr, 3) if wr is not None else None,
                    "total_mib": round((rd or 0) + (wr or 0), 3),
                }
        result["mem_bw"] = {"per_socket": per_socket}
        if "__error__" in nodes:
            result["mem_bw"]["error"] = nodes["__error__"]

        # 4. probes (off by default)
        if self.enable_probe:
            pr = _run(list(PROBE_EVENTS.values()), w)
            result["probe"] = {k: pr.get(v) for k, v in PROBE_EVENTS.items()}

        return result

    def delta(self, s0, s1, dt):
        return {}
