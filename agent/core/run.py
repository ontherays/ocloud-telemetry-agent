"""Run lifecycle and the immutable manifest.

The manifest exists because of a specific failure: two OCUDU deployments on
joule showed +20.83 W and +10.71 W over the same idle baseline, and the cause
was unrecoverable because the rendered gnb-config.yml was not captured with the
measurement. Every run therefore archives:

  * the rendered gnb-config.yml the gNB actually ran
  * kernel cmdline, topology, isolated set, idle states, cpufreq driver
  * phc2sys/ptp4l argv  (the -O value silently sets the clock epoch and has
    already changed once mid-project)
  * RAPL domain inventory and collector availability

Correlation uses monotonic offset from t0, never wall clock.
"""
import json
import os
import shutil
import time
import uuid

from ..util import utc_from


class Run:
    def __init__(self, root, run_id, label, meta=None):
        self.run_id = run_id
        self.label = label
        self.dir = os.path.join(root, run_id)
        os.makedirs(self.dir, exist_ok=True)
        self.t0_wall = time.time()
        self.t0_mono = time.monotonic()
        self.meta = meta or {}
        self.n_samples = 0
        self.stopped = False

    def offset(self):
        return time.monotonic() - self.t0_mono

    def path(self, name):
        return os.path.join(self.dir, name)

    def archive(self, src, name=None):
        if not src or not os.path.exists(src):
            return None
        dst = self.path(name or os.path.basename(src))
        try:
            shutil.copy2(src, dst)
            return dst
        except OSError:
            return None

    def write_manifest(self, static, extra=None):
        m = {
            "run_id": self.run_id,
            "label": self.label,
            "t0_utc": utc_from(self.t0_wall),
            "t0_epoch": self.t0_wall,
            "meta": self.meta,
            "node": static,
        }
        if extra:
            m.update(extra)
        with open(self.path("manifest.json"), "w") as f:
            json.dump(m, f, indent=2, default=str)
        return m

    def finalize(self, summary):
        self.stopped = True
        summary = dict(summary or {})
        summary.update({
            "run_id": self.run_id,
            "label": self.label,
            "t0_utc": utc_from(self.t0_wall),
            "t_end_utc": utc_from(time.time()),
            "duration_s": round(self.offset(), 3),
            "n_samples": self.n_samples,
        })
        with open(self.path("summary.json"), "w") as f:
            json.dump(summary, f, indent=2, default=str)
        return summary


def new_run_id(label):
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (label or "run"))
    return "%s-%s-%s" % (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
                         safe[:40], uuid.uuid4().hex[:6])
