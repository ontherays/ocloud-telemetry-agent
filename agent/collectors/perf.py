"""PMU counters -> IPC and MPKI. The signal that discriminates real work.

perf_event_paranoid=2 on joule blocks per-CPU/system-wide events. Two ways in:
  * host: sysctl -w kernel.perf_event_paranoid=0   (global, non-persistent)
  * container: CAP_PERFMON  (kernel >=5.8 bypasses paranoid) <- preferred

This is the one collector that shells out, because perf(1) has no stable
library API. It is optional by design: if perf is missing or blocked, the run
proceeds without it and says so in the manifest.
"""
import shutil
import subprocess

EVENTS = [
    "instructions", "cycles",
    "cache-references", "cache-misses",
    "context-switches", "cpu-migrations",
]


class PerfCollector:
    name = "perf"
    optional = True

    def __init__(self, cpus=None, events=None, timeout_pad=5.0):
        self.cpus = cpus or []
        self.events = events or EVENTS
        self.timeout_pad = timeout_pad
        self._err = ""

    def available(self):
        if not shutil.which("perf"):
            self._err = "perf(1) not found in PATH"
            return False
        if not self.cpus:
            self._err = "no target cpus configured"
            return False
        rc = subprocess.run(
            ["perf", "stat", "-e", "instructions", "-C", self._cpuspec(),
             "--", "sleep", "0.05"],
            capture_output=True, text=True)
        if rc.returncode != 0:
            self._err = ("perf denied: %s (need CAP_PERFMON or "
                         "kernel.perf_event_paranoid<=0)"
                         % rc.stderr.strip().splitlines()[-1:])
            return False
        return True

    def unavailable_reason(self):
        return self._err

    def _cpuspec(self):
        return ",".join(str(c) for c in self.cpus)

    def static(self):
        return {"events": self.events, "cpus": self.cpus}

    def snapshot(self):
        return {}

    def measure(self, window):
        """perf brackets its own window; it does not fit snapshot/delta."""
        cmd = ["perf", "stat", "-x", ",", "-e", ",".join(self.events),
               "-C", self._cpuspec(), "--", "sleep", str(window)]
        try:
            rc = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=window + self.timeout_pad)
        except (subprocess.TimeoutExpired, OSError) as e:
            return {"error": repr(e)}
        vals = {}
        for line in rc.stderr.splitlines():
            p = line.split(",")
            if len(p) < 3:
                continue
            try:
                vals[p[2]] = float(p[0])
            except ValueError:
                continue
        out = {"raw": vals}
        ins, cyc = vals.get("instructions"), vals.get("cycles")
        if ins and cyc:
            out["ipc"] = round(ins / cyc, 4)
        miss = vals.get("cache-misses")
        if ins and miss is not None and ins > 0:
            out["mpki"] = round(1000.0 * miss / ins, 4)
        for k in ("context-switches", "cpu-migrations"):
            if k in vals:
                out[k.replace("-", "_")] = vals[k]
        return out

    def delta(self, s0, s1, dt):
        return {}
