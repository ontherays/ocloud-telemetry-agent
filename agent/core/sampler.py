"""The sampling loop.

Invariants, each one paid for:

  * The window is MEASURED, midpoint-to-midpoint. Reading 32 cores takes real
    time; sleep(10) is not a 10.000 s window. Assuming it produced negative
    "busy" percentages and a 64x-wrong energy figure earlier in this project.
  * Counters are differenced, never reported cumulatively.
  * Collector failure degrades the run; it never kills it.
"""
import threading
import time

from ..util import utc_now


class Sampler(threading.Thread):
    daemon = True

    def __init__(self, collectors, interval, on_sample, perf=None,
                 perf_window=1.0, uncore=None):
        super().__init__(name="sampler")
        self.collectors = collectors
        self.interval = max(0.2, float(interval))
        self.on_sample = on_sample
        self.perf = perf
        self.perf_window = perf_window
        self.uncore = uncore
        # NOT _stop: threading.Thread._stop is an internal CPython method
        # that join() calls. Shadowing it breaks join() with
        # "TypeError: 'Event' object is not callable".
        self._halt = threading.Event()
        self.errors = []

    def stop(self):
        self._halt.set()

    def _snap_all(self):
        out = {}
        for c in self.collectors:
            try:
                out[c.name] = c.snapshot()
            except Exception as e:  # a bad collector must not kill the run
                self.errors.append("%s.snapshot: %r" % (c.name, e))
                out[c.name] = {}
        return out

    def run(self):
        while not self._halt.is_set():
            ta0 = time.monotonic()
            s0 = self._snap_all()
            ta1 = time.monotonic()

            if self._halt.wait(self.interval):
                break

            tb0 = time.monotonic()
            s1 = self._snap_all()
            tb1 = time.monotonic()

            # midpoint-to-midpoint: the reads themselves take time
            dt = ((tb0 + tb1) / 2.0) - ((ta0 + ta1) / 2.0)
            if dt <= 0:
                continue

            sample = {"t_utc": utc_now(), "elapsed_s": round(dt, 5), "data": {}}
            for c in self.collectors:
                try:
                    sample["data"][c.name] = c.delta(s0.get(c.name, {}),
                                                     s1.get(c.name, {}), dt)
                except Exception as e:
                    self.errors.append("%s.delta: %r" % (c.name, e))

            if self.perf is not None:
                try:
                    sample["data"]["perf"] = self.perf.measure(self.perf_window)
                    sample["data"]["perf"]["window_s"] = self.perf_window
                except Exception as e:
                    self.errors.append("perf.measure: %r" % (e,))

            if self.uncore is not None:
                try:
                    sample["data"]["uncore"] = self.uncore.measure()
                except Exception as e:
                    self.errors.append("uncore.measure: %r" % (e,))

            try:
                self.on_sample(sample)
            except Exception as e:
                self.errors.append("on_sample: %r" % (e,))
