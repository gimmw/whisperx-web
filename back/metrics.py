"""
Container-aware CPU and GPU metrics.

Design notes (why this is not just `/proc/loadavg` + GPUtil):

* CPU: `/proc/loadavg` is (a) a run-queue average, not utilisation, (b) includes
  uninterruptible-sleep tasks, (c) smoothed over 60s, and (d) NOT namespaced --
  inside a Kubernetes pod it reports the whole *node*. Likewise `os.cpu_count()`
  ignores `resources.limits.cpu`. We instead read the cgroup CPU accounting
  (`cpu.stat` / `cpuacct.usage`) and differentiate it against wall time, which
  yields true CPU-seconds-per-second consumed by *this pod*, and divide by the
  cgroup quota rather than the node's core count.

* GPU: GPUtil forks `nvidia-smi` and parses CSV on every call (last released in
  2018). We use NVML in-process via pynvml, which is orders of magnitude cheaper
  and exposes memory + MIG-awareness.

All samples are cached with a short TTL so that N concurrent pollers cause one
collection per interval rather than N.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

CGROUP_V2_ROOT = Path("/sys/fs/cgroup")

# Metrics are recomputed at most this often, regardless of poll rate.
CACHE_TTL = 1.0

# If the gap between two CPU samples exceeds this, the average over that window
# is too coarse to be meaningful, so we report it as unknown rather than lie.
MAX_CPU_SAMPLE_WINDOW = 60.0


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# CPU
# --------------------------------------------------------------------------- #

def _cpu_usage_seconds() -> float | None:
    """Cumulative CPU time consumed by this cgroup, in seconds."""
    # cgroup v2
    stat = _read(CGROUP_V2_ROOT / "cpu.stat")
    if stat:
        for line in stat.splitlines():
            if line.startswith("usage_usec "):
                try:
                    return int(line.split()[1]) / 1e6
                except (IndexError, ValueError):
                    break

    # cgroup v1
    raw = _read(Path("/sys/fs/cgroup/cpuacct/cpuacct.usage"))
    if raw:
        try:
            return int(raw) / 1e9
        except ValueError:
            pass

    return None


def _cpu_limit_cores() -> float | None:
    """
    Number of cores this container is actually allowed to use.

    Prefers the hard CFS quota (Kubernetes `resources.limits.cpu`), then the
    cpuset, then the node's core count as a last resort.
    """
    # cgroup v2: "<quota|max> <period>"
    raw = _read(CGROUP_V2_ROOT / "cpu.max")
    if raw:
        parts = raw.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                quota, period = int(parts[0]), int(parts[1])
                if quota > 0 and period > 0:
                    return quota / period
            except ValueError:
                pass

    # cgroup v1
    quota_raw = _read(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"))
    period_raw = _read(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us"))
    if quota_raw and period_raw:
        try:
            quota, period = int(quota_raw), int(period_raw)
            if quota > 0 and period > 0:
                return quota / period
        except ValueError:
            pass

    # No quota set (BestEffort/Burstable pod with no limit) -> fall back to the
    # cpuset, which at least respects CPU manager pinning.
    cpuset = _read(CGROUP_V2_ROOT / "cpuset.cpus.effective") or _read(
        Path("/sys/fs/cgroup/cpuset/cpuset.cpus")
    )
    if cpuset:
        count = 0
        for part in cpuset.split(","):
            if not part:
                continue
            if "-" in part:
                try:
                    lo, hi = part.split("-")
                    count += int(hi) - int(lo) + 1
                except ValueError:
                    continue
            else:
                count += 1
        if count > 0:
            return float(count)

    try:
        return float(len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return float(os.cpu_count() or 0) or None


class _CpuSampler:
    """
    Differentiates the cgroup CPU accumulator against wall time.

    Sampling runs on its own cadence in a background thread rather than being
    driven by request traffic, so the window between samples stays short and
    predictable no matter how often (or seldom) anyone polls the API.
    """

    def __init__(self) -> None:
        self._prev_usage: float | None = None
        self._prev_wall: float = 0.0
        self._cores: float | None = None
        self._lock = threading.Lock()

    def tick(self) -> None:
        """Take one sample and fold it into the current reading."""
        usage = _cpu_usage_seconds()
        now = time.monotonic()

        if usage is None:
            return

        with self._lock:
            prev_usage, prev_wall = self._prev_usage, self._prev_wall
            self._prev_usage, self._prev_wall = usage, now

            if prev_usage is None:
                return

            window = now - prev_wall
            if window <= 0 or window > MAX_CPU_SAMPLE_WINDOW:
                self._cores = None
                return

            # Clamp: the accumulator can appear to go backwards across a cgroup
            # move (e.g. container restart into a fresh cgroup).
            self._cores = max(0.0, usage - prev_usage) / window

    def read(self) -> tuple[float | None, float | None]:
        """Returns (cores_used, utilisation_fraction_of_limit)."""
        with self._lock:
            cores = self._cores
        if cores is None:
            return None, None
        limit = _cpu_limit_cores()
        return cores, (cores / limit) if limit else None


_cpu_sampler = _CpuSampler()

SAMPLE_INTERVAL = 1.0


def _sampler_loop() -> None:
    while True:
        try:
            _cpu_sampler.tick()
        except Exception:
            pass
        time.sleep(SAMPLE_INTERVAL)


def start_sampler() -> None:
    """Start background CPU sampling. Safe to call once at startup."""
    _cpu_sampler.tick()  # establish a baseline immediately
    threading.Thread(target=_sampler_loop, daemon=True, name="cpu-sampler").start()


# --------------------------------------------------------------------------- #
# GPU
# --------------------------------------------------------------------------- #

_nvml_ready: bool | None = None


def _nvml_init() -> bool:
    global _nvml_ready
    if _nvml_ready is not None:
        return _nvml_ready
    try:
        import pynvml

        pynvml.nvmlInit()
        _nvml_ready = True
    except Exception:
        # No driver, no GPU, or NVIDIA_DRIVER_CAPABILITIES lacks "utility".
        _nvml_ready = False
    return _nvml_ready


def _gpu_sample() -> dict | None:
    """
    Utilisation and memory for the first *visible* device.

    NVIDIA_VISIBLE_DEVICES already scopes the container to its allocated GPU(s),
    so index 0 is this pod's device -- but note that under time-slicing or MPS
    the utilisation figure covers all tenants of the physical GPU, and under MIG
    per-instance utilisation is simply not reported by NVML.
    """
    if not _nvml_init():
        return None

    try:
        import pynvml

        if pynvml.nvmlDeviceGetCount() < 1:
            return None

        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        out: dict = {}

        try:
            name = pynvml.nvmlDeviceGetName(handle)
            out["name"] = name.decode() if isinstance(name, bytes) else name
        except Exception:
            pass

        # Not supported on MIG instances -> leave as None rather than fabricate.
        try:
            out["util"] = pynvml.nvmlDeviceGetUtilizationRates(handle).gpu / 100.0
        except Exception:
            out["util"] = None

        try:
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            out["mem_used_mb"] = mem.used / 1024 / 1024
            out["mem_total_mb"] = mem.total / 1024 / 1024
            out["mem_util"] = (mem.used / mem.total) if mem.total else None
        except Exception:
            pass

        try:
            out["temp_c"] = pynvml.nvmlDeviceGetTemperature(
                handle, pynvml.NVML_TEMPERATURE_GPU
            )
        except Exception:
            pass

        return out
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Cached public entry point
# --------------------------------------------------------------------------- #

_cache: dict | None = None
_cache_at: float = 0.0
_cache_lock = threading.Lock()


def collect() -> dict:
    """
    Current resource usage, cached for CACHE_TTL seconds.

    Any field may be None when the underlying source is unavailable; callers
    must render missing values rather than assume a number is present.
    """
    global _cache, _cache_at

    with _cache_lock:
        now = time.monotonic()
        if _cache is not None and (now - _cache_at) < CACHE_TTL:
            return _cache

        cores, cpu_util = _cpu_sampler.read()
        gpu = _gpu_sample()

        _cache = {
            "cpu_cores_used": round(cores, 3) if cores is not None else None,
            "cpu_limit_cores": _cpu_limit_cores(),
            "cpu_util": round(cpu_util, 4) if cpu_util is not None else None,
            "gpu_util": (
                round(gpu["util"], 4)
                if gpu and gpu.get("util") is not None
                else None
            ),
            "gpu_mem_util": (
                round(gpu["mem_util"], 4)
                if gpu and gpu.get("mem_util") is not None
                else None
            ),
            "gpu_mem_used_mb": (
                round(gpu["mem_used_mb"])
                if gpu and gpu.get("mem_used_mb") is not None
                else None
            ),
            "gpu_mem_total_mb": (
                round(gpu["mem_total_mb"])
                if gpu and gpu.get("mem_total_mb") is not None
                else None
            ),
            "gpu_temp_c": gpu.get("temp_c") if gpu else None,
            "gpu_name": gpu.get("name") if gpu else None,
        }
        _cache_at = now
        return _cache
