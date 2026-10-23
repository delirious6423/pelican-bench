"""Low-overhead host/GPU/storage sampler for the Strata evaluation (1 s interval), plus safety checks.

One background thread reads a persistent `nvidia-smi -lms` stream (no per-sample process spawn) and /proc files; a
second checks the kernel log for new storage errors every 15 s. Samples go to a JSONL file and an in-memory ring so a
request's window [t0, t1] (wall-clock epoch) can be summarized and attached to that request's record.

Safety signals (never acted on by killing anything; the runner pauses/marks/stops ITS OWN work):
  storage_alarm   data NVMe controller not 'live', or new kernel lines matching I/O/NVMe/ext4 errors
  foreign_gpu     compute processes on the GPU that are not in our server's cgroup, with their memory
  hot             GPU temperature at/above the pause threshold
"""
from __future__ import annotations

import collections
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

GPU_FIELDS = ["timestamp", "utilization.gpu", "utilization.memory", "memory.used", "memory.total", "memory.reserved",
              "temperature.gpu", "power.draw", "clocks.sm", "clocks.mem", "clocks_event_reasons.active", "pstate",
              "pcie.link.gen.current", "fan.speed"]
DATA_DEV = os.environ.get("PELICAN_DATA_DEV", "nvme0n1")        # disk holding the model files (lsblk name)
SYS_DEV = os.environ.get("PELICAN_SYS_DEV", "nvme0n1")
NVME_STATE = Path(os.environ.get("PELICAN_NVME_STATE", "/sys/class/nvme/nvme0/state"))  # controller of DATA_DEV


def nvme_state() -> str:
    """'live' when healthy; 'n/a' when the state file does not exist (not an NVMe disk or not watched)."""
    return (_read(NVME_STATE, "n/a") or "n/a").strip() if NVME_STATE.exists() else "n/a"
KERNEL_BAD = re.compile(r"(nvme\d?\S*:.*(reset|timeout|abort|disabl|fail|error|dead))|I/O error|EXT4-fs error|"
                        r"EXT4-fs warning|blk_update_request|Buffer I/O error|critical (medium|target) error|"
                        r"journal (aborted|has aborted)|Remounting filesystem read-only|shutdown requested", re.I)
SECTOR = 512
CLK_TCK = os.sysconf("SC_CLK_TCK")


def _read(p, default=None):
    try:
        return Path(p).read_text()
    except OSError:
        return default


def meminfo() -> dict:
    out = {}
    for line in (_read("/proc/meminfo", "") or "").splitlines():
        k, _, v = line.partition(":")
        if k in ("MemTotal", "MemAvailable", "MemFree", "Cached", "SwapTotal", "SwapFree", "Dirty", "Mapped"):
            out[k] = int(v.split()[0]) * 1024
    return out


def vmstat() -> dict:
    out = {}
    for line in (_read("/proc/vmstat", "") or "").splitlines():
        k, _, v = line.partition(" ")
        if k in ("pswpin", "pswpout", "pgmajfault"):
            out[k] = int(v)
    return out


def diskstats() -> dict:
    out = {}
    for line in (_read("/proc/diskstats", "") or "").splitlines():
        f = line.split()
        if len(f) > 9 and f[2] in (DATA_DEV, SYS_DEV):
            out[f[2]] = {"read_bytes": int(f[5]) * SECTOR, "write_bytes": int(f[9]) * SECTOR, "reads": int(f[3])}
    return out


def cpu_times():
    f = (_read("/proc/stat", "") or "").splitlines()[0].split()[1:]
    v = [int(x) for x in f]
    idle = v[3] + v[4]
    return sum(v), idle


def cpu_package_temp() -> float | None:
    for hw in Path("/sys/class/hwmon").glob("hwmon*"):
        if (_read(hw / "name", "") or "").strip() != "coretemp":
            continue
        for lab in hw.glob("temp*_label"):
            if "Package" in (_read(lab, "") or ""):
                t = _read(str(lab).replace("_label", "_input"))
                return int(t) / 1000 if t else None
    return None


def cgroup_stats(unit: str | None) -> dict | None:
    """memory.current / io.stat of a user systemd unit (our server), or None."""
    if not unit:
        return None
    base = Path(f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/app.slice/{unit}")
    if not base.exists():
        return None
    out = {"memory_current": None, "rbytes": 0, "wbytes": 0, "pids": []}
    m = _read(base / "memory.current")
    out["memory_current"] = int(m) if m else None
    for line in (_read(base / "io.stat", "") or "").splitlines():
        for kv in line.split()[1:]:
            k, _, v = kv.partition("=")
            if k in ("rbytes", "wbytes"):
                out[k] += int(v)
    procs = (_read(base / "cgroup.procs", "") or "").split()
    out["pids"] = [int(p) for p in procs]
    m3 = re.search(r"usage_usec (\d+)", _read(base / "cpu.stat", "") or "")
    out["cpu_usec"] = int(m3.group(1)) if m3 else None
    rss = 0
    for p in out["pids"]:
        s = _read(f"/proc/{p}/status", "") or ""
        m2 = re.search(r"VmRSS:\s+(\d+) kB", s)
        if m2:
            rss += int(m2.group(1)) * 1024
    out["rss"] = rss
    return out


def gpu_compute_apps() -> list[dict]:
    try:
        r = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    apps = []
    for line in r.stdout.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) >= 3 and f[0].isdigit():
            apps.append({"pid": int(f[0]), "name": f[1], "mib": int(f[2]) if f[2].isdigit() else None})
    return apps


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class Monitor:
    def __init__(self, out_path: Path, interval_ms: int = 1000, unit: str | None = None, hot_c: float = 87.0):
        self.out_path = Path(out_path)
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.interval_ms = interval_ms
        self.unit = unit
        self.hot_c = hot_c
        self.ring = collections.deque(maxlen=6 * 3600)      # 6 h at 1 s
        self.lock = threading.Lock()
        self.stop_ev = threading.Event()
        self.storage_alarm: list[str] = []
        self.last_gpu: dict = {}
        self.foreign_gpu: list[dict] = []
        self.kernel_since = time.time()
        self._proc = None
        self._threads = []
        self._fh = open(self.out_path, "a", encoding="utf-8")
        self._prev_cpu = cpu_times()

    # ------------------------------------------------------------------ lifecycle
    def start(self):
        try:
            self._proc = subprocess.Popen(["nvidia-smi", f"--query-gpu={','.join(GPU_FIELDS)}",
                                           "--format=csv,noheader,nounits", f"-lms={self.interval_ms}"],
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        except OSError:                                   # no NVIDIA GPU tools: GPU fields stay empty
            self._proc = None
        for fn in ((self._gpu_reader,) if self._proc else ()) + (self._sampler, self._kernel_watch):
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def stop(self):
        self.stop_ev.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        for t in self._threads:
            t.join(3)
        self._fh.close()

    # ------------------------------------------------------------------ threads
    def _gpu_reader(self):
        for line in self._proc.stdout:
            f = [x.strip() for x in line.split(",")]
            if len(f) != len(GPU_FIELDS):
                continue
            d = dict(zip(GPU_FIELDS, f))
            self.last_gpu = {"util": _num(d["utilization.gpu"]), "mem_util": _num(d["utilization.memory"]),
                             "mem_used_mib": _num(d["memory.used"]), "mem_total_mib": _num(d["memory.total"]),
                             "mem_reserved_mib": _num(d["memory.reserved"]), "temp_c": _num(d["temperature.gpu"]),
                             "power_w": _num(d["power.draw"]), "sm_mhz": _num(d["clocks.sm"]),
                             "mem_mhz": _num(d["clocks.mem"]), "clock_event_reasons": d["clocks_event_reasons.active"],
                             "pstate": d["pstate"], "pcie_gen": _num(d["pcie.link.gen.current"]),
                             "fan_pct": _num(d["fan.speed"])}
            if self.stop_ev.is_set():
                break

    def _sampler(self):
        n = 0
        while not self.stop_ev.wait(self.interval_ms / 1000):
            n += 1
            tot, idle = cpu_times()
            ptot, pidle = self._prev_cpu
            self._prev_cpu = (tot, idle)
            cpu_util = 100.0 * (1 - (idle - pidle) / (tot - ptot)) if tot > ptot else None
            state = nvme_state()
            if state not in ("live", "n/a"):
                self._alarm(f"{NVME_STATE} = {state!r}")
            s = {"t": time.time(), "gpu": dict(self.last_gpu), "mem": meminfo(), "vm": vmstat(), "disk": diskstats(),
                 "cpu_busy_s": round((tot - idle) / CLK_TCK, 2),
                 "cpu_util": round(cpu_util, 1) if cpu_util is not None else None, "cpu_pkg_c": cpu_package_temp(),
                 "load1": os.getloadavg()[0], "nvme_state": state, "server": cgroup_stats(self.unit)}
            if n % 10 == 1:                                   # compute-app scan every 10 s (spawns nvidia-smi)
                ours = set((s["server"] or {}).get("pids") or [])
                self.foreign_gpu = [a for a in gpu_compute_apps() if a["pid"] not in ours]
                s["foreign_gpu"] = self.foreign_gpu
            with self.lock:
                self.ring.append(s)
            self._fh.write(json.dumps(s, separators=(",", ":")) + "\n")
            if n % 30 == 0:
                self._fh.flush()

    def _kernel_watch(self):
        while not self.stop_ev.wait(15):
            self.check_kernel()

    def check_kernel(self) -> list[str]:
        since = self.kernel_since
        self.kernel_since = time.time()
        try:
            r = subprocess.run(["journalctl", "-k", "--no-pager", "-o", "short-iso", "--since", f"@{since - 1:.0f}"],
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            return [f"journalctl failed: {e}"]
        bad = [l for l in r.stdout.splitlines() if KERNEL_BAD.search(l) and "-- No entries --" not in l]
        for l in bad:
            self._alarm("kernel: " + l)
        return bad

    def _alarm(self, msg: str):
        with self.lock:
            if msg not in self.storage_alarm:
                self.storage_alarm.append(msg)

    # ------------------------------------------------------------------ queries
    def hot(self) -> bool:
        t = self.last_gpu.get("temp_c")
        return t is not None and t >= self.hot_c

    def window(self, t0: float, t1: float) -> dict:
        """Summary of samples with t in [t0, t1] (epoch seconds)."""
        with self.lock:
            xs = [s for s in self.ring if t0 <= s["t"] <= t1]
        if not xs:
            return {"n_samples": 0}

        def series(f):
            return [v for v in (f(s) for s in xs) if v is not None]

        def stats(vals):
            if not vals:
                return None
            return {"min": min(vals), "max": max(vals), "mean": round(sum(vals) / len(vals), 2)}
        first, last = xs[0], xs[-1]

        def delta(path):
            a, b = first, last
            for k in path:
                a, b = (a or {}).get(k), (b or {}).get(k)
            return (b - a) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
        wall = max(last["t"] - first["t"], 1e-9)
        busy = delta(("cpu_busy_s",))
        ours = delta(("server", "cpu_usec"))
        other_cores = round((busy - (ours or 0) / 1e6) / wall, 2) if busy is not None and len(xs) > 1 else None
        reasons = collections.Counter(s["gpu"].get("clock_event_reasons") for s in xs if s.get("gpu"))
        srv = series(lambda s: (s.get("server") or {}).get("rss"))
        srv_mem = series(lambda s: (s.get("server") or {}).get("memory_current"))
        return {
            "n_samples": len(xs), "interval_ms": self.interval_ms, "t0": first["t"], "t1": last["t"],
            "gpu_util_pct": stats(series(lambda s: s["gpu"].get("util"))),
            "vram_used_mib": stats(series(lambda s: s["gpu"].get("mem_used_mib"))),
            "gpu_temp_c": stats(series(lambda s: s["gpu"].get("temp_c"))),
            "gpu_power_w": stats(series(lambda s: s["gpu"].get("power_w"))),
            "gpu_sm_mhz": stats(series(lambda s: s["gpu"].get("sm_mhz"))),
            "gpu_mem_mhz": stats(series(lambda s: s["gpu"].get("mem_mhz"))),
            "gpu_clock_event_reasons": dict(reasons),
            "cpu_util_pct": stats(series(lambda s: s.get("cpu_util"))),
            "server_cpu_cores": round((ours or 0) / 1e6 / wall, 2) if ours is not None and len(xs) > 1 else None,
            "other_cpu_cores": other_cores,
            "cpu_pkg_c": stats(series(lambda s: s.get("cpu_pkg_c"))),
            "mem_available_gib": stats([v / 2**30 for v in series(lambda s: s["mem"].get("MemAvailable"))]),
            "swap_used_gib": stats([(s["mem"]["SwapTotal"] - s["mem"]["SwapFree"]) / 2**30 for s in xs
                                    if "SwapTotal" in s["mem"]]),
            "swap_in_pages": delta(("vm", "pswpin")), "swap_out_pages": delta(("vm", "pswpout")),
            "major_faults": delta(("vm", "pgmajfault")),
            "data_disk_read_gb": (delta(("disk", DATA_DEV, "read_bytes")) or 0) / 1e9,
            "data_disk_write_gb": (delta(("disk", DATA_DEV, "write_bytes")) or 0) / 1e9,
            "sys_disk_read_gb": (delta(("disk", SYS_DEV, "read_bytes")) or 0) / 1e9,
            "server_cgroup_read_gb": (delta(("server", "rbytes")) or 0) / 1e9,
            "server_rss_gib": stats([v / 2**30 for v in srv]),
            "server_cgroup_mem_gib": stats([v / 2**30 for v in srv_mem]),
            "load1": stats(series(lambda s: s.get("load1"))),
            "nvme_states": sorted({s.get("nvme_state") for s in xs}),
            "foreign_gpu_seen": sorted({(a["pid"], a["name"]) for s in xs for a in (s.get("foreign_gpu") or [])}),
        }
