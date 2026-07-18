"""Shared primitives. No third-party imports, no subprocess, no assumptions."""
import os
import time

CPU_ROOT = "/sys/devices/system/cpu"


def read_text(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def read_int(path):
    v = read_text(path)
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_cpuset(spec):
    """'0-3,8,10-11' -> {0,1,2,3,8,10,11}"""
    out = set()
    if not spec:
        return out
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                out.update(range(int(a), int(b) + 1))
            except ValueError:
                continue
        else:
            try:
                out.add(int(part))
            except ValueError:
                continue
    return out


def cpu_list():
    """Every CPU the kernel exposes.

    Never nproc / os.cpu_count(): with kthread_cpus= on the cmdline the calling
    process is confined to a subset and nproc under-reports (joule: 5 of 32).
    """
    out = []
    try:
        for e in os.listdir(CPU_ROOT):
            if e.startswith("cpu") and e[3:].isdigit():
                out.append(int(e[3:]))
    except OSError:
        pass
    return sorted(out)


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def utc_from(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def proc_pids():
    out = []
    try:
        for e in os.listdir("/proc"):
            if e.isdigit():
                out.append(int(e))
    except OSError:
        pass
    return out


def parse_stat(raw):
    """Parse /proc/<pid>/stat or /proc/<pid>/task/<tid>/stat.

    comm (field 2) is parenthesised and may contain spaces or ')'. split() on
    the whole line is wrong; anchor on the LAST ')'.
    Returns (comm, fields) where fields[0] == field 3 (state).
      utime  = field 14 -> fields[11]
      stime  = field 15 -> fields[12]
      processor = field 39 -> fields[36]
    """
    if not raw:
        return None, None
    lp = raw.find("(")
    rp = raw.rfind(")")
    if lp < 0 or rp < 0 or rp < lp:
        return None, None
    return raw[lp + 1:rp], raw[rp + 2:].split()
