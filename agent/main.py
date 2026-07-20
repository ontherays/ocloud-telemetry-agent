"""Agent entrypoint.

Two modes:
    python3 -m agent.main serve            # long-running, REST-driven (DaemonSet)
    python3 -m agent.main once --label X   # one-shot A/B baseline, no Flask

The one-shot mode exists so the A/B protocol can run before the DaemonSet is
deployed, using exactly the same collectors as the served mode. Same instrument,
same numbers.
"""
import argparse
import json
import os
import sys
import threading
import time

from .config import Config
from .collectors.cpufreq import CpuFreqThermalCollector
from .collectors.cpuidle import CpuIdleCollector
from .collectors.numa import NumaCollector
from .collectors.perf import PerfCollector
from .collectors.procstat import ProcStatCollector
from .collectors.rapl import RaplCollector
from .collectors.redfish import RedfishCollector
from .collectors.threads import ThreadCollector
from .collectors.topology import TopologyCollector
from .collectors.uncore import UncoreCollector
from .core.run import Run, new_run_id
from .core.sampler import Sampler
from .sinks.filesink import FileSink
from .util import utc_now


def log(msg):
    print("[%s] %s" % (utc_now(), msg), flush=True)


def build(cfg):
    topo = TopologyCollector()
    static = topo.static() if topo.available() else {}
    isolated = static.get("isolated", [])

    cols = [CpuIdleCollector(), ProcStatCollector(), RaplCollector()]
    if cfg.enable_threads:
        cols.append(ThreadCollector(cfg.gnb_comms))
    if cfg.enable_cpufreq:
        cols.append(CpuFreqThermalCollector())
    if cfg.enable_numa:
        cols.append(NumaCollector(watch_pci=cfg.watch_pci))
    if cfg.redfish_url:
        cols.append(RedfishCollector(cfg.redfish_url, cfg.redfish_user,
                                     cfg.redfish_pass, cfg.redfish_chassis))

    avail, skipped = [], {}
    for c in cols:
        try:
            ok = c.available()
        except Exception as e:
            ok, reason = False, repr(e)
        else:
            reason = "" if ok else c.unavailable_reason()
        if ok:
            avail.append(c)
        else:
            skipped[c.name] = reason
            if not c.optional:
                log("WARN: required collector %s unavailable: %s" % (c.name, reason))

    perf = None
    if cfg.enable_perf and isolated:
        p = PerfCollector(cpus=isolated)
        if p.available():
            perf = p
        else:
            skipped["perf"] = p.unavailable_reason()

    uncore = None
    if cfg.enable_uncore:
        u = UncoreCollector(window=cfg.uncore_window, target_cpus=isolated,
                            enable_probe=cfg.uncore_probe)
        if u.available():
            uncore = u
        else:
            skipped["uncore"] = u.unavailable_reason()

    return topo, static, avail, perf, uncore, skipped


class Agent:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.run = None
        self.sink = None
        self.sampler = None
        (self.topo, self.static, self.collectors, self.perf, self.uncore,
         self.skipped) = build(cfg)
        for k, v in self.skipped.items():
            log("collector %-9s SKIPPED: %s" % (k, v))
        log("collectors active: %s%s%s"
            % (", ".join(c.name for c in self.collectors),
               " + perf" if self.perf else "",
               " + uncore" if self.uncore else ""))

    # ---------------------------------------------------------------- rows
    @staticmethod
    def _core_rows(s):
        idle = {r["cpu"]: r for r in s["data"].get("cpuidle", {}).get("cores", [])}
        ps = {r["cpu"]: r for r in s["data"].get("procstat", {}).get("cores", [])}
        rows = []
        for cpu in sorted(set(idle) | set(ps)):
            i, p = idle.get(cpu, {}), ps.get(cpu, {})
            rows.append({
                "cpu": cpu,
                "isolated": i.get("isolated"),
                "idle_total_s": i.get("idle_total_s"),
                "idle_busy_pct": i.get("busy_pct"),
                "idle_stale": i.get("stale"),
                "ps_busy_pct": p.get("busy_pct"),
                "ps_user": p.get("user"), "ps_system": p.get("system"),
                "ps_irq": p.get("irq"), "ps_softirq": p.get("softirq"),
                "ps_iowait": p.get("iowait"), "ps_steal": p.get("steal"),
            })
        return rows

    def _on_sample(self, s):
        with self.lock:
            run, sink = self.run, self.sink
        if not run or not sink:
            return
        base = {"t_utc": s["t_utc"], "t_offset_s": round(run.offset(), 4),
                "elapsed_s": s["elapsed_s"]}

        sink.write("cores", [dict(base, **r) for r in self._core_rows(s)])
        sink.write("power", [dict(base, **r)
                             for r in s["data"].get("rapl", {}).get("domains", [])])

        th = s["data"].get("threads", {}).get("threads", [])
        if self.cfg.thread_min_pct > 0:
            th = [t for t in th if t["cpu_pct"] >= self.cfg.thread_min_pct]
        sink.write("threads", [dict(base, **t) for t in th])

        rf = s["data"].get("redfish") or {}
        if rf and "error" not in rf:
            sink.write("redfish", [dict(base, **rf)])

        cf = s["data"].get("cpufreq") or {}
        if cf:
            sink.write("cpufreq", [dict(base, **r) for r in cf.get("cores", [])])
            sink.write("thermal", [dict(base, **r) for r in cf.get("thermal", [])])

        pf = s["data"].get("perf") or {}
        if pf and "error" not in pf:
            raw = pf.get("raw", {})
            sink.write("perf", [{
                "t_utc": s["t_utc"], "t_offset_s": round(run.offset(), 4),
                "window_s": pf.get("window_s"),
                "ipc": pf.get("ipc"), "mpki": pf.get("mpki"),
                "instructions": raw.get("instructions"), "cycles": raw.get("cycles"),
                "cache_misses": raw.get("cache-misses"),
                "context_switches": raw.get("context-switches"),
                "cpu_migrations": raw.get("cpu-migrations"),
            }])
        uc = s["data"].get("uncore") or {}
        if uc and "error" not in uc:
            w = uc.get("window_s")
            b2 = {"t_utc": s["t_utc"], "t_offset_s": round(run.offset(), 4),
                  "window_s": w}
            fr = uc.get("freq") or {}
            if fr:
                sink.write("freq_delivered", [dict(b2, **{
                    "aperf": fr.get("aperf"), "mperf": fr.get("mperf"),
                    "tsc": fr.get("tsc"),
                    "delivered_ratio": fr.get("delivered_ratio")})])
            cs = uc.get("cstate") or {}
            if cs:
                sink.write("cstate_hw", [dict(b2, **{
                    "c1_residency": cs.get("c1_residency"),
                    "c6_residency": cs.get("c6_residency")})])
            mb = uc.get("mem_bw") or {}
            soc_rows = []
            for sock, v in (mb.get("per_socket") or {}).items():
                soc_rows.append(dict(b2, socket=sock,
                                     read_mib=v.get("read_mib"),
                                     write_mib=v.get("write_mib"),
                                     total_mib=v.get("total_mib")))
            sink.write("membw_socket", soc_rows)

        run.n_samples += 1

    # ---------------------------------------------------------------- api
    def start(self, label, meta=None):
        with self.lock:
            if self.run and not self.run.stopped:
                raise RuntimeError("run %s already active" % self.run.run_id)
            rid = new_run_id(label)
            run = Run(self.cfg.runs_dir, rid, label, meta)
            sink = FileSink(run.dir)

            archived = run.archive(self.cfg.gnb_config_path, "gnb-config.yml")
            static = dict(self.static)
            static["threads"] = next(
                (c.static() for c in self.collectors if c.name == "threads"), {})
            static["rapl"] = next(
                (c.static() for c in self.collectors if c.name == "rapl"), {})
            static["numa"] = next(
                (c.static() for c in self.collectors if c.name == "numa"), {})
            static["cpufreq_thermal"] = next(
                (c.static() for c in self.collectors if c.name == "cpufreq"), {})
            static["uncore"] = self.uncore.static() if self.uncore else {}
            run.write_manifest(static, {
                "config": self.cfg.as_dict(),
                "collectors_active": [c.name for c in self.collectors]
                                     + (["perf"] if self.perf else [])
                                     + (["uncore"] if self.uncore else []),
                "collectors_skipped": self.skipped,
                "gnb_config_archived": bool(archived),
                "gnb_config_source": self.cfg.gnb_config_path,
            })
            if not archived:
                log("WARN: no gnb-config.yml at %s - run is NOT reproducible"
                    % self.cfg.gnb_config_path)

            self.run, self.sink = run, sink
            self.sampler = Sampler(self.collectors, self.cfg.interval,
                                   self._on_sample, self.perf, self.cfg.perf_window,
                                   self.uncore)
            self.sampler.start()
            log("run started: %s (label=%s)" % (rid, label))
            return {"run_id": rid, "dir": run.dir,
                    "gnb_config_archived": bool(archived)}

    def stop(self):
        with self.lock:
            if not self.run or self.run.stopped:
                raise RuntimeError("no active run")
            self.sampler.stop()
            sampler, run, sink = self.sampler, self.run, self.sink
        sampler.join(timeout=self.cfg.interval * 3 + 5)
        summary = run.finalize({"sampler_errors": sampler.errors[:50]})
        sink.close()
        with self.lock:
            self.run, self.sink, self.sampler = None, None, None
        log("run stopped: %s (%d samples)" % (summary["run_id"],
                                              summary["n_samples"]))
        return summary

    def status(self):
        with self.lock:
            r = self.run
            return {
                "active": bool(r and not r.stopped),
                "run_id": r.run_id if r else None,
                "label": r.label if r else None,
                "offset_s": round(r.offset(), 2) if r else None,
                "n_samples": r.n_samples if r else 0,
                "collectors": [c.name for c in self.collectors]
                              + (["perf"] if self.perf else [])
                              + (["uncore"] if self.uncore else []),
                "skipped": self.skipped,
            }


def cmd_once(cfg, args):
    agent = Agent(cfg)
    agent.start(args.label, {"mode": "once"})
    time.sleep(args.window)
    s = agent.stop()
    d = os.path.join(cfg.runs_dir, s["run_id"])
    print(json.dumps(s, indent=2, default=str))
    print("\nrun dir: %s" % d)
    for f in sorted(os.listdir(d)):
        print("  %s" % f)
    return 0


def cmd_serve(cfg, args):
    from .api.rest import serve
    return serve(Agent(cfg), cfg)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ocloud-telemetry-agent")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="long-running REST-driven agent")
    s.set_defaults(fn=cmd_serve)

    o = sub.add_parser("once", help="one-shot timed capture")
    o.add_argument("--label", required=True)
    o.add_argument("--window", type=float, default=30.0)
    o.set_defaults(fn=cmd_once)

    args = ap.parse_args(argv)
    return args.fn(Config(), args)


if __name__ == "__main__":
    sys.exit(main())
