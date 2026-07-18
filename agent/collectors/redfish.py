"""Redfish -> ES TR Table 6.2-1 power metrics and EC_node,platform.

The TR names DMTF Redfish (DSP2046 / DSP2051) as the source for physical
O-Cloud Resource power. It is the ONLY source for EC_node,platform, since this
Xeon exposes no psys RAPL domain.

Runs OFF-NODE. joule cannot route to its own BMC; 192.168.8.78 gets HTTP 200.
So this collector is driven from wherever the BMC is reachable and its samples
are joined to the run by run_id. Nothing here assumes co-location.

stdlib only (urllib) -- `requests` is not guaranteed in the base image.
"""
import base64
import json
import ssl
import urllib.error
import urllib.request


class RedfishCollector:
    name = "redfish"
    optional = True

    def __init__(self, base_url=None, user=None, password=None,
                 chassis="1", timeout=5.0, verify=False):
        self.base = (base_url or "").rstrip("/")
        self.user = user
        self.password = password
        self.chassis = chassis
        self.timeout = timeout
        self._ctx = None if verify else ssl._create_unverified_context()
        self._err = ""

    def _get(self, path):
        url = "%s%s" % (self.base, path)
        req = urllib.request.Request(url)
        if self.user is not None:
            tok = base64.b64encode(
                ("%s:%s" % (self.user, self.password)).encode()).decode()
            req.add_header("Authorization", "Basic " + tok)
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=self.timeout,
                                    context=self._ctx) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def available(self):
        if not self.base:
            self._err = "no redfish base_url configured"
            return False
        try:
            self._get("/redfish/v1/")
            return True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                ValueError) as e:
            self._err = "redfish unreachable from this host: %r" % (e,)
            return False

    def unavailable_reason(self):
        return self._err

    def static(self):
        try:
            return {"chassis_index": self._get("/redfish/v1/Chassis")}
        except Exception as e:
            return {"error": repr(e)}

    def read_power(self):
        """Try the legacy Power schema, then the newer PowerSubsystem."""
        for path, parse in (
            ("/redfish/v1/Chassis/%s/Power" % self.chassis, self._parse_power),
            ("/redfish/v1/Chassis/%s/PowerSubsystem" % self.chassis,
             self._parse_subsystem),
        ):
            try:
                return parse(self._get(path))
            except (urllib.error.HTTPError, urllib.error.URLError, OSError,
                    ValueError, KeyError, IndexError):
                continue
        return {"error": "no readable power resource on chassis %s" % self.chassis}

    @staticmethod
    def _parse_power(doc):
        pc = (doc.get("PowerControl") or [{}])[0]
        return {
            "watts": pc.get("PowerConsumedWatts"),
            "capacity_watts": pc.get("PowerCapacityWatts"),
            "avg_watts": (pc.get("PowerMetrics") or {}).get("AverageConsumedWatts"),
            "max_watts": (pc.get("PowerMetrics") or {}).get("MaxConsumedWatts"),
            "min_watts": (pc.get("PowerMetrics") or {}).get("MinConsumedWatts"),
            "schema": "Power",
        }

    @staticmethod
    def _parse_subsystem(doc):
        return {
            "capacity_watts": doc.get("CapacityWatts"),
            "watts": ((doc.get("PowerSupplies") or {}) or {}).get("Members@odata.count"),
            "schema": "PowerSubsystem",
            "note": "PowerSubsystem present; drill into /Metrics for watts",
        }

    def snapshot(self):
        return self.read_power()

    def delta(self, s0, s1, dt):
        w0, w1 = s0.get("watts"), s1.get("watts")
        if w0 is None or w1 is None or dt <= 0:
            return {}
        avg = (w0 + w1) / 2.0
        return {"avg_watts": round(avg, 3), "joules": round(avg * dt, 3)}
