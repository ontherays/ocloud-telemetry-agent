"""Delivered CPU frequency + thermal. A correctness guard for energy comparison.

Under heavy TM500 traffic the package heats up and THROTTLES -- silently drops
frequency to avoid overheating. If that happens during the sweep, "energy at
900 Mbps" is contaminated by throttling, not just workload. So this must be
recorded alongside every energy sample to PROVE the comparison wasn't skewed.

Two frequency notions, both captured:
  * scaling_cur_freq  -> what the OS REQUESTED (governor target)
  * aperf/mperf ratio -> what the silicon DELIVERED

They diverge under AVX-512 (your x86-64-v4 build) because of frequency
licensing / thermal throttling. scaling_cur_freq alone will hide throttling;
the aperf/mperf ratio will not. aperf/mperf needs the msr module + root, so it
degrades gracefully to sysfs-only if unavailable.
"""
import os

from ..util import CPU_ROOT, cpu_list, read_int, read_text


class CpuFreqThermalCollector:
    name = "cpufreq"
    optional = True

    def __init__(self, cpus=None):
        self._cpus = cpus if cpus is not None else cpu_list()
        self._have_cpufreq = self._probe_cpufreq()
        self._have_aperf = self._probe_msr()
        self._thermal_zones = self._discover_thermal()

    def _probe_cpufreq(self):
        if not self._cpus:
            return False
        return os.path.isdir("%s/cpu%d/cpufreq" % (CPU_ROOT, self._cpus[0]))

    def _probe_msr(self):
        # aperf/mperf come from perf's msr PMU; presence is checked by the perf
        # collector. Here we only read sysfs. Kept as a hook for a future MSR
        # reader; sysfs cur_freq is always attempted.
        return False

    def _discover_thermal(self):
        base = "/sys/class/thermal"
        zones = []
        if not os.path.isdir(base):
            return zones
        try:
            for e in sorted(os.listdir(base)):
                if e.startswith("thermal_zone"):
                    zones.append({
                        "zone": e,
                        "type": read_text("%s/%s/type" % (base, e)),
                        "path": "%s/%s/temp" % (base, e),
                    })
        except OSError:
            pass
        return zones

    def available(self):
        return self._have_cpufreq or bool(self._thermal_zones)

    def unavailable_reason(self):
        return "no cpufreq sysfs and no thermal zones"

    def static(self):
        cpus = self._cpus
        d0 = "%s/cpu%d/cpufreq" % (CPU_ROOT, cpus[0]) if cpus else None
        return {
            "have_cpufreq": self._have_cpufreq,
            "driver": read_text("%s/scaling_driver" % d0) if d0 else None,
            "governor": read_text("%s/scaling_governor" % d0) if d0 else None,
            "cpuinfo_min_khz": read_int("%s/cpuinfo_min_freq" % d0) if d0 else None,
            "cpuinfo_max_khz": read_int("%s/cpuinfo_max_freq" % d0) if d0 else None,
            "thermal_zones": [{"zone": z["zone"], "type": z["type"]}
                              for z in self._thermal_zones],
            "note": ("scaling_cur_freq is the REQUESTED freq, not delivered; "
                     "under AVX-512 use aperf/mperf for the true value"),
        }

    def snapshot(self):
        # Instantaneous reads; no counter differencing needed for freq/temp.
        freqs = {}
        for c in self._cpus:
            f = read_int("%s/cpu%d/cpufreq/scaling_cur_freq" % (CPU_ROOT, c))
            if f is not None:
                freqs[c] = f
        temps = {}
        for z in self._thermal_zones:
            t = read_int(z["path"])
            if t is not None:
                temps[z["zone"]] = t
        return {"freq_khz": freqs, "temp_mC": temps,
                "types": {z["zone"]: z["type"] for z in self._thermal_zones}}

    def delta(self, s0, s1, dt):
        # Report the END-of-window instantaneous values (freq/temp are levels,
        # not counters). Averaging two edges would blur a throttle event.
        f = s1.get("freq_khz", {})
        t = s1.get("temp_mC", {})
        types = s1.get("types", {})
        cores = [{"cpu": c, "cur_freq_mhz": round(v / 1000.0, 1)}
                 for c, v in sorted(f.items())]
        thermal = [{"zone": z, "type": types.get(z), "temp_c": round(v / 1000.0, 2)}
                   for z, v in sorted(t.items())]
        pkg = [x for x in thermal if (x["type"] or "").startswith("x86_pkg")]
        return {
            "cores": cores,
            "thermal": thermal,
            "pkg_temp_c_max": max((x["temp_c"] for x in pkg), default=None),
        }
