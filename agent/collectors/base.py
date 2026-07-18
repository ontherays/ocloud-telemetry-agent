"""Collector contract.

Every collector implements the same three-phase shape:

    static()            one-shot facts -> run manifest
    snapshot()          raw counters at an instant
    delta(s0, s1, dt)   derived values over a MEASURED window

Nothing derives a rate without an explicit, measured `dt`. Every bug this
project hit came from assuming the window.
"""


class Collector:
    name = "base"
    optional = False

    def available(self):
        return True

    def unavailable_reason(self):
        return ""

    def static(self):
        return {}

    def snapshot(self):
        return {}

    def delta(self, s0, s1, dt):
        return {}
