"""步骤级 monitoring 侧车。"""

from __future__ import annotations

import json
import os
import time
import tracemalloc
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional

import psutil

from .config import Paths, PipelineParams


class StepMonitor:
    def __init__(self, paths: Paths, params: PipelineParams, step: int, mode: str) -> None:
        self._paths = paths
        self._step = step
        self._mode = mode
        self._enabled = bool(params.monitoring.enabled)
        self._level = params.monitoring.level
        self._proc = psutil.Process(os.getpid())
        self._root_pid = self._proc.pid
        self._label = ""
        self._start_time: Optional[float] = None
        self._start_cpu = None
        self._start_rss = 0
        self._start_peak_rss = 0
        self._start_tree_rss = 0
        self._start_tree_peak_rss = 0
        self._start_tree_cpu = 0.0
        self._tracemalloc_started_here = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def output_path(self) -> Path:
        suffix = "smoke" if self._mode == "smoke" else "full"
        return self._paths.metadata_dir / "monitoring" / f"step{self._step}_{suffix}.json"

    def start(self, label: str) -> None:
        if not self._enabled:
            return
        self._label = label
        self._start_time = time.perf_counter()
        self._start_cpu = self._proc.cpu_times()
        self._start_rss, self._start_peak_rss = self._memory_snapshot()
        self._start_tree_rss, self._start_tree_peak_rss, self._start_tree_cpu = self._process_tree_snapshot()
        if self._level == "full" and not tracemalloc.is_tracing():
            tracemalloc.start()
            self._tracemalloc_started_here = True

    def finish(self, body_error: Optional[BaseException] = None) -> None:
        if not self._enabled or self._start_time is None:
            return

        try:
            end_time = time.perf_counter()
            end_cpu = self._proc.cpu_times()
            end_rss, end_peak_rss = self._memory_snapshot()
            end_tree_rss, end_tree_peak_rss, end_tree_cpu = self._process_tree_snapshot()
            cpu_seconds = None
            if self._start_cpu is not None:
                cpu_seconds = (
                    (end_cpu.user - self._start_cpu.user)
                    + (end_cpu.system - self._start_cpu.system)
                )
            tree_cpu_seconds = max(0.0, end_tree_cpu - self._start_tree_cpu)
            peak_rss_bytes = int(max(self._start_peak_rss, end_peak_rss, end_rss))
            tree_peak_rss_bytes = int(
                max(
                    peak_rss_bytes,
                    self._start_tree_rss,
                    self._start_tree_peak_rss,
                    end_tree_rss,
                    end_tree_peak_rss,
                )
            )

            payload: Dict[str, object] = {
                "step": self._step,
                "mode": self._mode,
                "section": self._label,
                "level": self._level,
                "pid": self._root_pid,
                "wall_seconds": round(max(0.0, end_time - self._start_time), 3),
                "cpu_seconds": round(cpu_seconds, 3) if cpu_seconds is not None else None,
                "tree_cpu_seconds": round(tree_cpu_seconds, 3),
                "rss_bytes_start": int(self._start_rss),
                "rss_bytes_end": int(end_rss),
                "rss_bytes_delta": int(end_rss - self._start_rss),
                "peak_rss_bytes": peak_rss_bytes,
                "tree_rss_bytes": int(end_tree_rss),
                "tree_peak_rss_bytes": tree_peak_rss_bytes,
            }
            if body_error is not None:
                payload["body_exception_type"] = type(body_error).__name__
                payload["body_exception_message"] = str(body_error)
            if self._level == "full" and tracemalloc.is_tracing():
                current, peak = tracemalloc.get_traced_memory()
                snapshot = tracemalloc.take_snapshot()
                payload["tracemalloc_current_bytes"] = int(current)
                payload["tracemalloc_peak_bytes"] = int(peak)
                payload["top_allocations"] = [
                    {
                        "trace": str(stat.traceback).splitlines()[-1].strip(),
                        "size_bytes": int(stat.size),
                        "count": int(stat.count),
                    }
                    for stat in snapshot.statistics("lineno")[:10]
                ]
            self._write_payload(payload)
        except Exception as exc:
            print(f"monitoring sidecar 写入失败 {self.output_path.name}: {exc}")
        finally:
            if self._tracemalloc_started_here and tracemalloc.is_tracing():
                tracemalloc.stop()
            self._start_time = None
            self._start_cpu = None
            self._start_rss = 0
            self._start_peak_rss = 0
            self._start_tree_rss = 0
            self._start_tree_peak_rss = 0
            self._start_tree_cpu = 0.0
            self._tracemalloc_started_here = False

    def _memory_snapshot(self) -> tuple[int, int]:
        info = self._proc.memory_info()
        rss = int(getattr(info, "rss", 0))
        peak_rss = int(getattr(info, "peak_wset", rss))
        return rss, peak_rss

    def _process_tree_snapshot(self) -> tuple[int, int, float]:
        processes = [self._proc]
        try:
            processes.extend(self._proc.children(recursive=True))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

        total_rss = 0
        total_peak_rss = 0
        total_cpu = 0.0
        seen_pids = set()
        for proc in processes:
            pid = getattr(proc, "pid", None)
            if pid in seen_pids:
                continue
            seen_pids.add(pid)
            try:
                info = proc.memory_info()
                cpu_times = proc.cpu_times()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

            rss = int(getattr(info, "rss", 0))
            peak_rss = int(getattr(info, "peak_wset", rss))
            total_rss += rss
            total_peak_rss += peak_rss if peak_rss > 0 else rss
            total_cpu += float(getattr(cpu_times, "user", 0.0) + getattr(cpu_times, "system", 0.0))

        if total_peak_rss < total_rss:
            total_peak_rss = total_rss
        return total_rss, total_peak_rss, total_cpu

    def _write_payload(self, payload: Dict[str, object]) -> None:
        path = self.output_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@contextmanager
def monitor_step(
    paths: Paths,
    params: PipelineParams,
    step: int,
    mode: str,
    label: str,
) -> Iterator[Optional[StepMonitor]]:
    monitor = StepMonitor(paths, params, step, mode)
    if not monitor.enabled:
        yield None
        return

    body_error: Optional[BaseException] = None
    monitor.start(label)
    try:
        yield monitor
    except BaseException as exc:
        body_error = exc
        raise
    finally:
        monitor.finish(body_error=body_error)
