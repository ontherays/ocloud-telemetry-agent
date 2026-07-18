"""Per-core idle residency, with a stale-counter guard.

Two hard-won rules live here.

1. ALL idle states count, not just C1. POLL (state0) is idle too. Computing
   busy = 100 - C1% silently counts POLL as work.

2. The cpuidle `time` counter only advances when a core EXITS an idle state.
   A deeply idle core (nohz_full + rcu_nocbs, no wakeups) leaves it stale, and
   the delta then reads as a suspiciously round constant -- on joule, exactly
   28.000s or 32.000s across many unrelated cores, and sometimes MORE than the
   wall-clock window, yielding negative "busy". Such a core is idle; it is not
   a measurement. Flag it STALE and refuse to report a busy figure.

   Observed on joule 2026-07-17 across three independent runs.
"""
import os

from ..util import CPU_ROOT, cpu_list, parse_cpuset, read_int, read_text

# If summed idle >= window * this, the counter is not tracking. Cores doing
# real work on joule sat at 0.45-0.76 of the window; idle ones at >=0.93.
STALE_RATIO = 0.93


class CpuIdleCollector:
    name = "cpuidle"
    optional = False

    def __init__(self):
        self._cpus = cpu_list()
        self._states = self._discover_states()
        self._isolated = parse_cpuset(read_text("%s/isolated" % CPU_ROOT))

    def _discover_states(self):
        if not self._cpus:
            return []
        d = "%s/cpu%d/cpuidle" % (CPU_ROOT, self._cpus[0])
        if not os.path.isdir(d):
            return []
        out = []
        try:
            for e in sorted(os.listdir(d)):
                if e.startswith("state") and e[5:].isdigit():
                    out.append((int(e[5:]), read_text("%s/%s/name" % (d, e)) or e))
        except OSError:
            pass
        return out

    def available(self):
        return bool(self._states)

    def unavailable_reason(self):
        return "no cpuidle states exposed (idle driver disabled?)"

    def static(self):
        return {"states": [{"index": i, "name": n} for i, n in self._states]}

    def snapshot(self):
        snap = {}
        for c in self._cpus:
            d = "%s/cpu%d/cpuidle" % (CPU_ROOT, c)
            st = {}
            for idx, _ in self._states:
                v = read_int("%s/state%d/time" % (d, idx))
                if v is not None:
                    st[idx] = v
            snap[c] = st
        return snap

    def delta(self, s0, s1, dt):
        rows = []
        for c in self._cpus:
            a, b = s0.get(c, {}), s1.get(c, {})
            per = {}
            total = 0.0
            for idx, name in self._states:
                if idx in a and idx in b:
                    v = (b[idx] - a[idx]) / 1e6
                    if v < 0:
                        v = 0.0
                    per[name] = round(v, 4)
                    total += v
            stale = (dt <= 0) or (total >= dt * STALE_RATIO)
            rows.append({
                "cpu": c,
                "isolated": c in self._isolated,
                "idle_s": per,
                "idle_total_s": round(total, 4),
                "busy_pct": None if stale else round(100.0 * (1.0 - total / dt), 3),
                "stale": stale,
            })
        return {"cores": rows}
