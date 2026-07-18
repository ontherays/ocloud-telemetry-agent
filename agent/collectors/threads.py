"""Per-thread CPU time -> ES TR Table 6.3-2 'CPU Time', plus placement.

Requires hostPID: true. With it, /proc already shows every host process, so
nsenter is unnecessary -- and nsenter cost ~80 fork+exec per sample in the
predecessor, inflating both overhead and the sampling window.

Two distinct notions, deliberately both collected:
  * Cpus_allowed_list  -> the affinity the CONFIG asked for
  * processor (f39)    -> the core the thread LAST RAN ON
Divergence between them is thread migration. On joule the DPDK hot pair moved
between deployments (cpu13/25 -> cpu5/17) because eal_args uses grouped
--lcores syntax, which shares one mask across all lcores rather than pinning
1:1. Placement is therefore not reproducible across restarts unless the config
is changed to explicit 0@3,1@5,... mapping.
"""
import os

from ..util import parse_cpuset, proc_pids, parse_stat, read_text

CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


class ThreadCollector:
    name = "threads"
    optional = True

    def __init__(self, comms=("gnb", "nr-softmodem")):
        self.comms = tuple(comms)
        self._comm_cache = {}

    def available(self):
        return bool(self.find_pids())

    def unavailable_reason(self):
        return ("no process matching %s; is shareProcessNamespace/hostPID set?"
                % (self.comms,))

    def find_pids(self):
        """Exact comm match first, substring second. comm is truncated to 15
        chars by the kernel, so exact matching alone can miss."""
        exact, loose = [], []
        for pid in proc_pids():
            c = read_text("/proc/%d/comm" % pid)
            if not c:
                continue
            if c in self.comms:
                exact.append(pid)
            elif any(t in c for t in self.comms):
                loose.append(pid)
        return exact or loose

    @staticmethod
    def container_of(pid):
        """Map pid -> container id via cgroup. Works for cgroup v1 and v2."""
        raw = read_text("/proc/%d/cgroup" % pid)
        if not raw:
            return None
        for line in raw.splitlines():
            parts = line.split(":", 2)
            if len(parts) != 3:
                continue
            path = parts[2]
            for token in reversed(path.strip("/").split("/")):
                t = token
                for pre in ("docker-", "crio-", "cri-containerd-"):
                    if t.startswith(pre):
                        t = t[len(pre):]
                if t.endswith(".scope"):
                    t = t[:-len(".scope")]
                if len(t) >= 32 and all(ch in "0123456789abcdef" for ch in t[:32]):
                    return t[:64]
        return None

    def static(self):
        out = {}
        for pid in self.find_pids():
            out[str(pid)] = {
                "comm": read_text("/proc/%d/comm" % pid),
                "cmdline": (read_text("/proc/%d/cmdline" % pid) or "").replace("\x00", " ").strip(),
                "container": self.container_of(pid),
            }
        return out

    def snapshot(self):
        snap = {}
        for pid in self.find_pids():
            tdir = "/proc/%d/task" % pid
            try:
                tids = os.listdir(tdir)
            except OSError:
                continue
            for tid in tids:
                raw = read_text("%s/%s/stat" % (tdir, tid))
                comm, f = parse_stat(raw)
                if not f:
                    continue
                try:
                    ticks = int(f[11]) + int(f[12])   # utime + stime
                    last = int(f[36])                 # processor
                except (IndexError, ValueError):
                    continue
                allowed = None
                key = (pid, tid)
                if key not in self._comm_cache:
                    st = read_text("%s/%s/status" % (tdir, tid))
                    if st:
                        for ln in st.splitlines():
                            if ln.startswith("Cpus_allowed_list:"):
                                allowed = ln.split(":", 1)[1].strip()
                                break
                    self._comm_cache[key] = allowed
                snap[(pid, tid)] = {
                    "comm": comm,
                    "ticks": ticks,
                    "last_cpu": last,
                    "allowed": self._comm_cache.get(key),
                }
        return snap

    def delta(self, s0, s1, dt):
        rows = []
        for key, b in s1.items():
            a = s0.get(key)
            if not a or dt <= 0:
                continue
            d = b["ticks"] - a["ticks"]
            if d < 0:
                continue
            pid, tid = key
            rows.append({
                "pid": pid,
                "tid": int(tid),
                "comm": b["comm"],
                "cpu_pct": round(100.0 * (d / CLK_TCK) / dt, 3),
                "last_cpu": b["last_cpu"],
                "migrated": b["last_cpu"] != a["last_cpu"],
                "allowed": b.get("allowed"),
                "delta_ticks": d,
            })
        rows.sort(key=lambda r: r["cpu_pct"], reverse=True)
        return {"threads": rows, "clk_tck": CLK_TCK}
