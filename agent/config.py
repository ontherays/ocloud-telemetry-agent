"""Configuration. Env-driven so the DaemonSet can retarget without a rebuild."""
import os


def _b(k, d=False):
    v = os.environ.get(k)
    return d if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _f(k, d):
    try:
        return float(os.environ.get(k, d))
    except (TypeError, ValueError):
        return d


class Config:
    def __init__(self):
        self.interval = _f("SAMPLE_INTERVAL", 1.0)
        self.runs_dir = os.environ.get("RUNS_DIR", "/mnt/debugging-logs/runs")
        self.node = os.environ.get("NODE_NAME", "")
        self.gnb_comms = tuple(
            c.strip() for c in os.environ.get("GNB_COMM", "gnb,nr-softmodem").split(",")
            if c.strip())
        # Rendered config the gNB actually ran. Archived per run: two joule
        # deployments differed by 10 W and only the config explained it.
        self.gnb_config_path = os.environ.get(
            "GNB_CONFIG_PATH", "/mnt/debugging-logs/gnb-config.yml")
        self.enable_perf = _b("ENABLE_PERF", True)
        self.perf_window = _f("PERF_WINDOW", 1.0)
        self.enable_threads = _b("ENABLE_THREADS", True)
        self.enable_cpufreq = _b("ENABLE_CPUFREQ", True)
        self.enable_numa = _b("ENABLE_NUMA", True)
        self.enable_uncore = _b("ENABLE_UNCORE", True)
        self.enable_health = _b("ENABLE_HEALTH", True)
        self.health_interval = _f("HEALTH_INTERVAL", 120.0)
        self.uncore_window = _f("UNCORE_WINDOW", 1.0)
        self.uncore_probe = _b("UNCORE_PROBE", False)  # UPI/AVX512, off by default
        # PCI addrs whose NUMA node to track (fronthaul VF).
        self.watch_pci = tuple(
            x.strip() for x in os.environ.get("WATCH_PCI", "0000:ca:01.3").split(",")
            if x.strip())
        self.thread_min_pct = _f("THREAD_MIN_PCT", 0.0)
        self.redfish_url = os.environ.get("REDFISH_URL", "")
        self.redfish_user = os.environ.get("REDFISH_USER", "")
        self.redfish_pass = os.environ.get("REDFISH_PASSWORD", "")
        self.redfish_chassis = os.environ.get("REDFISH_CHASSIS", "1")
        self.api_host = os.environ.get("API_HOST", "0.0.0.0")
        self.api_port = int(_f("API_PORT", 5010))

    def as_dict(self):
        d = dict(self.__dict__)
        if d.get("redfish_pass"):
            d["redfish_pass"] = "***redacted***"
        return d
