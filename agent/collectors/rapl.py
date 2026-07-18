"""Intel RAPL energy -> ES TR Table 6.2-2 EC_node,* metrics.

Three rules:

1. Key domains by SYSFS DIRECTORY, never by the `name` file. joule exposes
   'dram' twice (intel-rapl:0:0 and intel-rapl:1:0); keying by name silently
   loses socket 0.

2. Wraparound uses max_energy_range_uj (262143328850 ~= 2^38 on this Xeon),
   not 2^32. The 2^32 constant is wrong by 64x.

3. energy_uj is 0400 root:root on kernels >= 5.10 (CVE-2020-8694 / PLATYPUS).
   Non-root reads raise PermissionError. Fail loudly at startup, never
   silently emit {}.

Mapping to O-RAN.WG6.TR.O-CLOUD-ES Table 6.2-2, as measured on joule:
    package-N        -> EC_node,package     available
    dram (N:M)       -> EC_node,dram        available
    core (PP0)       -> EC_node,core        ABSENT on this Xeon
    uncore           -> EC_node,uncore      ABSENT
    psys / platform  -> EC_node,platform    ABSENT -> use Redfish

EC_node,core being unavailable matters: the TR names it as the metric to use
for CPU-bound workloads. Server-class Xeons do not expose PP0.
"""
import os

from ..util import read_int, read_text

POWERCAP = "/sys/class/powercap"


class RaplCollector:
    name = "rapl"
    optional = False

    def __init__(self):
        self.domains = {}
        self.errors = []
        self._discover()

    def _discover(self):
        if not os.path.isdir(POWERCAP):
            self.errors.append("%s absent (needs bare metal + intel_rapl_msr)" % POWERCAP)
            return
        try:
            entries = sorted(os.listdir(POWERCAP))
        except OSError as e:
            self.errors.append("cannot list %s: %s" % (POWERCAP, e))
            return
        for e in entries:
            if not e.startswith("intel-rapl:"):
                continue
            d = os.path.join(POWERCAP, e)
            ep = os.path.join(d, "energy_uj")
            if not os.path.exists(ep):
                continue
            if read_text(ep) is None:
                self.errors.append(
                    "%s unreadable: energy_uj is 0400 root:root on kernels >=5.10 "
                    "(CVE-2020-8694); run as uid 0" % ep)
                continue
            self.domains[e] = {
                "name": read_text(os.path.join(d, "name")) or e,
                "path": ep,
                "max": read_int(os.path.join(d, "max_energy_range_uj")),
                "socket": self._socket_of(e),
            }

    @staticmethod
    def _socket_of(entry):
        # intel-rapl:1 -> 1 ; intel-rapl:1:0 -> 1
        try:
            return int(entry.split(":")[1])
        except (IndexError, ValueError):
            return None

    def available(self):
        return bool(self.domains)

    def unavailable_reason(self):
        return "; ".join(self.errors) or "no readable RAPL domains"

    def static(self):
        return {
            "domains": {k: {"name": v["name"], "max_energy_range_uj": v["max"],
                            "socket": v["socket"]}
                        for k, v in self.domains.items()},
            "errors": self.errors,
        }

    def snapshot(self):
        out = {}
        for k, v in self.domains.items():
            t = read_int(v["path"])
            if t is not None:
                out[k] = t
        return out

    def delta(self, s0, s1, dt):
        rows = []
        for k in sorted(s0):
            if k not in s1 or dt <= 0:
                continue
            d = s1[k] - s0[k]
            wrapped = False
            if d < 0:
                mx = self.domains[k]["max"]
                if not mx:
                    continue  # uncorrectable without the range: drop, never guess
                d += mx
                wrapped = True
            rows.append({
                "domain": k,
                "name": self.domains[k]["name"],
                "socket": self.domains[k]["socket"],
                "joules": round(d / 1e6, 4),
                "watts": round((d / 1e6) / dt, 4),
                "wrapped": wrapped,
            })
        return {"domains": rows}
