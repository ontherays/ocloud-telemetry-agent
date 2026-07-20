"""Append-only CSV per run. Authoritative dataset.

Files, not InfluxDB, are the thesis record: no silent drops when a network path
hiccups, immutable per run, and still readable in two years. Long format, one
row per (sample, entity), which pandas reads directly.

Never load-and-rewrite (the predecessor did load_workbook/save every batch,
making measurement overhead grow with run length). Open once, append, flush.
"""
import csv
import os

SCHEMAS = {
    "cores": ["t_utc", "t_offset_s", "elapsed_s", "cpu", "isolated",
              "idle_total_s", "idle_busy_pct", "idle_stale",
              "ps_busy_pct", "ps_user", "ps_system", "ps_irq", "ps_softirq",
              "ps_iowait", "ps_steal"],
    "power": ["t_utc", "t_offset_s", "elapsed_s", "domain", "name", "socket",
              "joules", "watts", "wrapped"],
    "threads": ["t_utc", "t_offset_s", "elapsed_s", "pid", "tid", "comm",
                "cpu_pct", "last_cpu", "migrated", "allowed"],
    "perf": ["t_utc", "t_offset_s", "window_s", "ipc", "mpki",
             "instructions", "cycles", "cache_misses", "context_switches",
             "cpu_migrations"],
    "redfish": ["t_utc", "t_offset_s", "elapsed_s", "watts", "avg_watts",
                "capacity_watts", "joules"],
    "cpufreq": ["t_utc", "t_offset_s", "elapsed_s", "cpu", "cur_freq_mhz"],
    "thermal": ["t_utc", "t_offset_s", "elapsed_s", "zone", "type", "temp_c"],
    "freq_delivered": ["t_utc", "t_offset_s", "window_s", "aperf", "mperf",
                       "tsc", "delivered_ratio"],
    "cstate_hw": ["t_utc", "t_offset_s", "window_s", "c1_residency",
                  "c6_residency"],
    "membw_socket": ["t_utc", "t_offset_s", "window_s", "socket",
                     "read_mib", "write_mib", "total_mib"],
}


class FileSink:
    def __init__(self, run_dir):
        self.dir = run_dir
        self._fh = {}
        self._w = {}

    def _writer(self, table):
        if table in self._w:
            return self._w[table]
        path = os.path.join(self.dir, "%s.csv" % table)
        new = not os.path.exists(path)
        fh = open(path, "a", newline="")
        w = csv.DictWriter(fh, fieldnames=SCHEMAS[table], extrasaction="ignore")
        if new:
            w.writeheader()
        self._fh[table] = fh
        self._w[table] = w
        return w

    def write(self, table, rows):
        if not rows:
            return
        w = self._writer(table)
        for r in rows:
            w.writerow(r)
        self._fh[table].flush()

    def close(self):
        for fh in self._fh.values():
            try:
                fh.close()
            except OSError:
                pass
        self._fh.clear()
        self._w.clear()
