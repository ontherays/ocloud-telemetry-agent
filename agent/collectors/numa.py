"""NUMA placement + IMC-to-socket mapping.

CORRECTION to an earlier finding: the uncore PMU is NOT absent on joule. The
"uncore_upi" error was a wrong event NAME (correct: uncore_upi_0). joule exposes
uncore_imc_0..15, uncore_upi_0..2, uncore_cha_*, uncore_pcu -- the full fabric.
Live memory-bandwidth counting lives in the `uncore` collector; this collector
owns the STATIC placement question that governs correctness:

  For a 7.2x DU with 500us deadlines, a core on socket 1 reaching memory or a
  NIC on socket 0 pays a UPI-crossing penalty on every access.

Verified on joule 2026-07-17: SR-IOV VF 0000:ca:01.3 -> numa_node 1, same
socket as the isolated (odd) cores. Fronthaul path is NUMA-aligned.
"""
import os

from ..util import CPU_ROOT, cpu_list, parse_cpuset, read_int, read_text

NODE_ROOT = "/sys/devices/system/node"


class NumaCollector:
    name = "numa"
    optional = True

    def __init__(self, watch_pci=None):
        self.watch_pci = list(watch_pci or [])
        self._nodes = self._discover_nodes()

    def _discover_nodes(self):
        nodes = []
        if not os.path.isdir(NODE_ROOT):
            return nodes
        try:
            for e in sorted(os.listdir(NODE_ROOT)):
                if e.startswith("node") and e[4:].isdigit():
                    nodes.append(int(e[4:]))
        except OSError:
            pass
        return nodes

    def available(self):
        return bool(self._nodes)

    def unavailable_reason(self):
        return "%s absent (no NUMA topology)" % NODE_ROOT

    def _node_cpus(self, n):
        return parse_cpuset(read_text("%s/node%d/cpulist" % (NODE_ROOT, n)))

    def _node_mem(self, n):
        info = read_text("%s/node%d/meminfo" % (NODE_ROOT, n)) or ""
        out = {}
        for line in info.splitlines():
            parts = line.split()
            if "MemTotal:" in line:
                try:
                    out["mem_total_kb"] = int(parts[-2])
                except (ValueError, IndexError):
                    pass
            elif "MemFree:" in line:
                try:
                    out["mem_free_kb"] = int(parts[-2])
                except (ValueError, IndexError):
                    pass
        return out

    def _hugepages(self, n):
        base = "%s/node%d/hugepages" % (NODE_ROOT, n)
        out = {}
        if not os.path.isdir(base):
            return out
        try:
            for e in sorted(os.listdir(base)):
                out[e] = {"nr": read_int("%s/%s/nr_hugepages" % (base, e)),
                          "free": read_int("%s/%s/free_hugepages" % (base, e))}
        except OSError:
            pass
        return out

    @staticmethod
    def _pci_node(addr):
        return read_int("/sys/bus/pci/devices/%s/numa_node" % addr)

    def _imc_map(self):
        """uncore_imc_N -> socket, via cpumask. 16 IMCs on joule."""
        base = "/sys/devices"
        cpu_socket = {c: read_int("%s/cpu%d/topology/physical_package_id"
                                  % (CPU_ROOT, c)) for c in cpu_list()}
        out = {}
        try:
            for e in sorted(os.listdir(base)):
                if e.startswith("uncore_imc_") and "free_running" not in e:
                    mask = read_text("%s/%s/cpumask" % (base, e))
                    sock = None
                    if mask:
                        first = mask.split(",")[0].split("-")[0]
                        try:
                            sock = cpu_socket.get(int(first))
                        except ValueError:
                            pass
                    # sock here is the READER cpu's socket, NOT the IMC socket
                    out[e] = {"cpumask": mask, "reader_cpu_socket": sock}
        except OSError:
            pass
        return out

    @staticmethod
    def _uncore_present():
        try:
            return sorted(e for e in os.listdir("/sys/devices")
                          if e.startswith("uncore_"))
        except OSError:
            return []

    def static(self):
        isolated = parse_cpuset(read_text("%s/isolated" % CPU_ROOT))
        nodes = {}
        for n in self._nodes:
            cpus = self._node_cpus(n)
            nodes[n] = {
                "cpus": sorted(cpus),
                "isolated_here": sorted(cpus & isolated),
                "meminfo": self._node_mem(n),
                "hugepages": self._hugepages(n),
            }
        du_nodes = {n for n, v in nodes.items() if v["isolated_here"]}

        pci, alignment = {}, {}
        for addr in self.watch_pci:
            node = self._pci_node(addr)
            drv_link = "/sys/bus/pci/devices/%s/driver" % addr
            aligned = (node in du_nodes) if node is not None else None
            pci[addr] = {
                "numa_node": node,
                "driver": os.path.basename(os.path.realpath(drv_link))
                          if os.path.exists(drv_link) else None,
                "aligned_with_du": aligned,
            }
            alignment[addr] = aligned

        return {
            "nodes": nodes,
            "du_nodes": sorted(du_nodes),
            "pci": pci,
            "fronthaul_numa_aligned": alignment,
            # NOTE: the cpumask below is the PMU READER cpu, not the IMC's
            # socket (all IMCs report 0-1 on joule). Do NOT infer socket from
            # it. Per-socket memory bandwidth comes from the uncore collector
            # via `perf --per-node`. Kept here as raw record only.
            "imc_cpumask_raw": self._imc_map(),
            "uncore_pmus": self._uncore_present(),
        }

    def snapshot(self):
        return {}

    def delta(self, s0, s1, dt):
        return {}
