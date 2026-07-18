"""One-shot node facts for the run manifest.

Everything here is provenance: if it changes between runs, the runs are not
comparable. Two deployments on joule differed by 10 W and the cause was only
recoverable because the config was archived.
"""
import os
import platform

from ..util import CPU_ROOT, cpu_list, parse_cpuset, read_int, read_text


class TopologyCollector:
    name = "topology"
    optional = False

    def available(self):
        return os.path.isdir(CPU_ROOT)

    def unavailable_reason(self):
        return "%s missing" % CPU_ROOT

    def _sockets(self):
        m = {}
        for c in cpu_list():
            pkg = read_int("%s/cpu%d/topology/physical_package_id" % (CPU_ROOT, c))
            core = read_int("%s/cpu%d/topology/core_id" % (CPU_ROOT, c))
            sibs = read_text("%s/cpu%d/topology/thread_siblings_list" % (CPU_ROOT, c))
            m[c] = {"socket": pkg, "core_id": core, "siblings": sibs}
        return m

    def _idle_states(self):
        cpus = cpu_list()
        if not cpus:
            return []
        d = "%s/cpu%d/cpuidle" % (CPU_ROOT, cpus[0])
        if not os.path.isdir(d):
            return []
        out = []
        try:
            for e in sorted(os.listdir(d)):
                if e.startswith("state") and e[5:].isdigit():
                    out.append({
                        "index": int(e[5:]),
                        "name": read_text("%s/%s/name" % (d, e)),
                        "desc": read_text("%s/%s/desc" % (d, e)),
                        "latency_us": read_int("%s/%s/latency" % (d, e)),
                        "disable": read_int("%s/%s/disable" % (d, e)),
                    })
        except OSError:
            pass
        return out

    def _cpufreq(self):
        cpus = cpu_list()
        if not cpus:
            return {}
        d = "%s/cpu%d/cpufreq" % (CPU_ROOT, cpus[0])
        if not os.path.isdir(d):
            return {"present": False}
        return {
            "present": True,
            "driver": read_text("%s/scaling_driver" % d),
            "governor": read_text("%s/scaling_governor" % d),
            "available_governors": read_text("%s/scaling_available_governors" % d),
            "cpuinfo_min_khz": read_int("%s/cpuinfo_min_freq" % d),
            "cpuinfo_max_khz": read_int("%s/cpuinfo_max_freq" % d),
        }

    def _hugepages(self):
        out = {}
        base = "/sys/kernel/mm/hugepages"
        if not os.path.isdir(base):
            return out
        try:
            for e in sorted(os.listdir(base)):
                out[e] = {
                    "nr": read_int("%s/%s/nr_hugepages" % (base, e)),
                    "free": read_int("%s/%s/free_hugepages" % (base, e)),
                }
        except OSError:
            pass
        return out

    def _ptp(self):
        """phc2sys/ptp4l argv. The -O value silently decides the clock epoch;
        it changed under us once already. Archive it with every run."""
        out = {}
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            comm = read_text("/proc/%s/comm" % pid)
            if comm not in ("phc2sys", "ptp4l"):
                continue
            raw = read_text("/proc/%s/cmdline" % pid)
            if raw:
                out.setdefault(comm, []).append(raw.replace("\x00", " ").strip())
        return out

    def static(self):
        cpus = cpu_list()
        isolated = parse_cpuset(read_text("%s/isolated" % CPU_ROOT))
        return {
            "hostname": platform.node(),
            "kernel": platform.release(),
            "cmdline": read_text("/proc/cmdline"),
            "ncpu": len(cpus),
            "cpus": cpus,
            "online": read_text("%s/online" % CPU_ROOT),
            "offline": read_text("%s/offline" % CPU_ROOT),
            "isolated": sorted(isolated),
            "nohz_full": read_text("%s/nohz_full" % CPU_ROOT),
            "smt_active": read_text("%s/smt/active" % CPU_ROOT),
            "smt_control": read_text("%s/smt/control" % CPU_ROOT),
            "topology": self._sockets(),
            "idle_states": self._idle_states(),
            "cpufreq": self._cpufreq(),
            "hugepages": self._hugepages(),
            "ptp": self._ptp(),
            "perf_event_paranoid": read_int("/proc/sys/kernel/perf_event_paranoid"),
        }

    def snapshot(self):
        return {}

    def delta(self, s0, s1, dt):
        return {}
