"""Per-core occupancy from /proc/stat.

/proc/stat is NOT namespaced: inside a container it reports host-wide values,
so this needs no hostPID and no nsenter.

Self-normalising -- busy is a ratio of deltas, so it is immune to window-length
error. That makes it the cross-check against cpuidle, whose absolute counters
are not immune. If the two disagree, cpuidle is the suspect.
"""
FIELDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")


class ProcStatCollector:
    name = "procstat"
    optional = False

    def available(self):
        try:
            with open("/proc/stat"):
                return True
        except OSError:
            return False

    def unavailable_reason(self):
        return "/proc/stat unreadable"

    def static(self):
        return {}

    def snapshot(self):
        out = {}
        try:
            with open("/proc/stat") as f:
                for line in f:
                    if not line.startswith("cpu"):
                        continue
                    p = line.split()
                    if p[0] == "cpu":
                        continue  # aggregate; derived instead
                    try:
                        out[int(p[0][3:])] = [int(x) for x in p[1:9]]
                    except (ValueError, IndexError):
                        continue
        except OSError:
            pass
        return out

    def delta(self, s0, s1, dt):
        rows = []
        for c in sorted(s0):
            if c not in s1:
                continue
            d = [s1[c][i] - s0[c][i] for i in range(8)]
            tot = sum(d)
            if tot <= 0:
                continue
            idx = dict(zip(FIELDS, d))
            pct = lambda v: round(100.0 * v / tot, 3)
            idle_all = idx["idle"] + idx["iowait"]
            rows.append({
                "cpu": c,
                "busy_pct": round(100.0 * (tot - idle_all) / tot, 3),
                "user": pct(idx["user"]),
                "nice": pct(idx["nice"]),
                "system": pct(idx["system"]),
                "idle": pct(idx["idle"]),
                "iowait": pct(idx["iowait"]),
                "irq": pct(idx["irq"]),
                "softirq": pct(idx["softirq"]),
                "steal": pct(idx["steal"]),
            })
        return {"cores": rows}
