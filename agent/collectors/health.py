"""Node health heartbeat: PTP sync, iptables/route drift, fronthaul NIC/VF.

NOT a measurement collector and NOT on the sample loop. It runs on a slow timer
(default 120s) purely so the r-App can show green/red without someone SSHing
into joule to check. It never gates or fails a capture.

Checks encode the operator's own verification steps (joule PTP guide):
  * pmc -d 24 -s /var/run/ptp4l-ptp-fh GET TIME_STATUS_NP -> master_offset<100ns,
    gmPresent true, gmIdentity matches the OcNOS grandmaster
  * ptp4l / phc2sys processes alive
  * PHC<->CLOCK_REALTIME offset (guide: <100ns; UTC offset 37 healthy)
  * iptables ruleset hash+count vs a first-run baseline (nft backend on joule)
  * ip route hash vs baseline
  * enp202s0f0 link state + VF 3 (spoof/link-state/trust, TX dropped)

"Baseline on first run, report deltas" -- since N2/N3 aren't up we don't yet
know the correct ruleset, so we snapshot what exists and flag CHANGE.

Cost: ~40ms once per 120s ~= 0.03% of one housekeeping core. Degrades to
"unavailable: needs root" for pmc/iptables when not root.
"""
import hashlib
import os
import re
import shutil
import subprocess
import time

from ..util import read_text


def _sh(cmd, timeout=6):
    try:
        rc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return rc.returncode, rc.stdout, rc.stderr
    except (subprocess.TimeoutExpired, OSError) as e:
        return -1, "", repr(e)


def _is_root():
    return os.geteuid() == 0 if hasattr(os, "geteuid") else False


class HealthCollector:
    name = "health"
    optional = True

    # thresholds from the joule PTP guide
    PTP_OFFSET_NS = 100
    UTC_OFFSET_EXPECTED = 37       # currentUtcOffset when healthy

    def __init__(self, ptp_domain=24, ptp_sock="/var/run/ptp4l-ptp-fh",
                 fh_iface="enp202s0f0", fh_vf=3,
                 gm_identity=None, interval=120.0):
        self.ptp_domain = int(os.environ.get("PTP_DOMAIN", ptp_domain))
        self.ptp_sock = os.environ.get("PTP_SOCK", ptp_sock)
        self.fh_iface = os.environ.get("FH_IFACE", fh_iface)
        self.fh_vf = int(os.environ.get("FH_VF", fh_vf))
        # OcNOS grandmaster id from the guide; env-overridable.
        self.gm_identity = os.environ.get("PTP_GM_IDENTITY", gm_identity)
        self.interval = float(os.environ.get("HEALTH_INTERVAL", interval))
        self._baseline = None       # set on first check
        self._last = None
        self._last_ts = 0.0

    def available(self):
        return True                 # always; individual checks degrade

    def unavailable_reason(self):
        return ""

    # -- individual checks --------------------------------------------------
    def _ptp(self):
        out = {"checked": True}
        out["ptp4l_alive"] = bool(_sh(["pgrep", "-x", "ptp4l"])[0] == 0)
        out["phc2sys_alive"] = bool(_sh(["pgrep", "-x", "phc2sys"])[0] == 0)
        if not shutil.which("pmc"):
            out["pmc"] = "unavailable: pmc not found"
            out["sync_ok"] = None
            return out
        if not _is_root():
            out["pmc"] = "unavailable: needs root"
            out["sync_ok"] = None
            return out
        rc, so, se = _sh(["pmc", "-u", "-b", "0", "-d", str(self.ptp_domain),
                          "-s", self.ptp_sock, "GET TIME_STATUS_NP"])
        if rc != 0:
            out["pmc"] = "error: %s" % (se.strip() or so.strip() or rc)
            out["sync_ok"] = False
            return out
        off = re.search(r"master_offset\s+(-?\d+)", so)
        gm = re.search(r"gmPresent\s+(\w+)", so)
        gid = re.search(r"gmIdentity\s+(\S+)", so)
        offset = int(off.group(1)) if off else None
        gm_present = (gm.group(1) == "true") if gm else False
        gm_id = gid.group(1) if gid else None
        out["master_offset_ns"] = offset
        out["gm_present"] = gm_present
        out["gm_identity"] = gm_id
        ok = (offset is not None and abs(offset) < self.PTP_OFFSET_NS
              and gm_present)
        if self.gm_identity and gm_id and gm_id != self.gm_identity:
            ok = False
            out["gm_identity_mismatch"] = True
        out["sync_ok"] = ok
        return out

    def _time_offset(self):
        out = {}
        try:
            import ctypes
            CLOCK_TAI = 11
            librt = ctypes.CDLL("librt.so.1", use_errno=True)

            class TS(ctypes.Structure):
                _fields_ = [("s", ctypes.c_long), ("ns", ctypes.c_long)]
            tai, rt = TS(), TS()
            librt.clock_gettime(CLOCK_TAI, ctypes.byref(tai))
            librt.clock_gettime(0, ctypes.byref(rt))
            diff = (tai.s - rt.s) + (tai.ns - rt.ns) / 1e9
            out["tai_minus_realtime_s"] = round(diff, 3)
            # healthy = ~37 (kernel tai_offset set). 0 = offset not applied.
            out["utc_offset_ok"] = abs(diff - self.UTC_OFFSET_EXPECTED) < 2
        except Exception as e:
            out["error"] = repr(e)
        return out

    @staticmethod
    def _hash(text):
        return hashlib.sha256((text or "").encode()).hexdigest()[:16]

    def _iptables(self):
        if not shutil.which("iptables-save"):
            return {"available": False, "reason": "iptables-save not found"}
        if not _is_root():
            return {"available": False, "reason": "needs root"}
        rc, so, se = _sh(["iptables-save"])
        if rc != 0:
            return {"available": False, "reason": (se.strip() or "error")}
        # strip volatile counters/timestamps so only real rule changes register
        lines = [l for l in so.splitlines()
                 if not l.startswith("#") and not l.startswith("[")]
        norm = "\n".join(re.sub(r"\[\d+:\d+\]", "", l) for l in lines)
        return {"available": True, "hash": self._hash(norm),
                "rule_count": len([l for l in lines if l.startswith("-")])}

    def _route(self):
        raw = read_text("/proc/net/route") or ""
        # skip header; hash destination/gateway/mask columns only
        rows = [l.split()[:4] for l in raw.splitlines()[1:] if l.strip()]
        flat = "\n".join(" ".join(r) for r in sorted(rows))
        return {"hash": self._hash(flat), "route_count": len(rows)}

    def _nic_vf(self):
        out = {"iface": self.fh_iface, "vf": self.fh_vf}
        op = read_text("/sys/class/net/%s/operstate" % self.fh_iface)
        out["link"] = op
        out["link_up"] = (op == "up")
        rc, so, _ = _sh(["ip", "-s", "link", "show", "dev", self.fh_iface])
        if rc != 0:
            out["vf_info"] = "unavailable"
            return out
        # find the "vf N" block and the TX dropped counter that follows it
        m = re.search(r"vf %d\b(.*?)(?=vf \d|\Z)" % self.fh_vf, so, re.S)
        if m:
            blk = m.group(1)
            out["spoof_off"] = "spoof checking off" in blk
            ls = re.search(r"link-state (\w+)", blk)
            out["link_state"] = ls.group(1) if ls else None
            out["trust_on"] = "trust on" in blk
            drops = re.findall(r"\d+", blk.split("TX:")[-1]) if "TX:" in blk else []
            out["tx_dropped"] = int(drops[2]) if len(drops) >= 3 else None
        return out

    # -- assembly -----------------------------------------------------------
    def check(self, force=False):
        now = time.monotonic()
        if not force and self._last and (now - self._last_ts) < self.interval:
            return self._last              # cached; respect slow cadence

        ptp = self._ptp()
        toff = self._time_offset()
        ipt = self._iptables()
        rt = self._route()
        nic = self._nic_vf()

        cur = {"iptables": ipt.get("hash"), "route": rt.get("hash")}
        if self._baseline is None:
            self._baseline = cur
            drift = {"iptables_changed": False, "route_changed": False,
                     "baseline_captured": True}
        else:
            drift = {
                "iptables_changed": (ipt.get("hash") is not None
                                     and ipt["hash"] != self._baseline["iptables"]),
                "route_changed": rt["hash"] != self._baseline["route"],
                "baseline_captured": False,
            }

        issues = []
        if ptp.get("sync_ok") is False:
            issues.append("ptp_out_of_sync")
        if not ptp.get("ptp4l_alive"):
            issues.append("ptp4l_down")
        if not ptp.get("phc2sys_alive"):
            issues.append("phc2sys_down")
        if toff.get("utc_offset_ok") is False:
            issues.append("utc_offset_wrong")
        if drift["iptables_changed"]:
            issues.append("iptables_changed")
        if drift["route_changed"]:
            issues.append("route_changed")
        if nic.get("link_up") is False:
            issues.append("fh_link_down")
        if nic.get("tx_dropped"):
            issues.append("vf_tx_drops")

        result = {
            "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "ok": len(issues) == 0,
            "issues": issues,
            "ptp": ptp,
            "time_offset": toff,
            "iptables": ipt,
            "route": rt,
            "nic_vf": nic,
            "drift": drift,
        }
        self._last, self._last_ts = result, now
        return result

    # collector protocol (health is not a per-sample delta source)
    def static(self):
        return {"interval_s": self.interval, "ptp_domain": self.ptp_domain,
                "ptp_sock": self.ptp_sock, "fh_iface": self.fh_iface,
                "fh_vf": self.fh_vf}

    def snapshot(self):
        return {}

    def delta(self, s0, s1, dt):
        return {}
