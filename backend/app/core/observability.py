import time
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


JOB_STAGE_TIMING_KEYS = {
    "upstream_wait",
    "download_decode",
    "validate",
    "thumbnail",
    "db_insert",
}

_current_job_timer: ContextVar["JobStageTimer | None"] = ContextVar(
    "current_job_timer",
    default=None,
)


@dataclass
class JobStageTimer:
    timings_ms: dict[str, float] = field(default_factory=dict)

    def record(self, stage: str, elapsed_ms: float) -> None:
        if stage not in JOB_STAGE_TIMING_KEYS or elapsed_ms < 0:
            return
        self.timings_ms[stage] = self.timings_ms.get(stage, 0.0) + elapsed_ms

    def snapshot(self) -> dict[str, float]:
        return {
            stage: round(elapsed_ms, 2)
            for stage, elapsed_ms in self.timings_ms.items()
        }


@contextmanager
def use_job_stage_timer(timer: JobStageTimer) -> Iterator[JobStageTimer]:
    token = _current_job_timer.set(timer)
    try:
        yield timer
    finally:
        _current_job_timer.reset(token)


def record_job_stage_timing(stage: str, elapsed_ms: float) -> None:
    timer = _current_job_timer.get()
    if timer is not None:
        timer.record(stage, elapsed_ms)


@contextmanager
def observe_job_stage(stage: str) -> Iterator[None]:
    started_at = time.perf_counter()
    try:
        yield
    finally:
        record_job_stage_timing(stage, (time.perf_counter() - started_at) * 1000)


class MetricsStore:
    def __init__(self, max_samples: int = 2048):
        self.max_samples = max(100, int(max_samples))
        self._lock = RLock()
        self._counters: defaultdict[str, int] = defaultdict(int)
        self._samples_ms: dict[str, deque[float]] = {}

    def increment(self, name: str, value: int = 1) -> None:
        if value <= 0:
            return
        with self._lock:
            self._counters[name] += value

    def observe_ms(self, name: str, elapsed_ms: float) -> None:
        if elapsed_ms < 0:
            return
        with self._lock:
            samples = self._samples_ms.get(name)
            if samples is None:
                samples = deque(maxlen=self.max_samples)
                self._samples_ms[name] = samples
            samples.append(float(elapsed_ms))

    def observe_job_stage_timings(self, timings_ms: dict[str, float]) -> None:
        for stage, elapsed_ms in timings_ms.items():
            self.observe_ms(f"job_stage.{stage}", elapsed_ms)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(self._counters)
            samples = {
                name: list(values)
                for name, values in self._samples_ms.items()
            }
        return {
            "counters": counters,
            "timings_ms": {
                name: _summarize_samples(values)
                for name, values in sorted(samples.items())
            },
        }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._samples_ms.clear()


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(
        len(sorted_values) - 1,
        max(0, round((len(sorted_values) - 1) * percentile)),
    )
    return sorted_values[index]


def _summarize_samples(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    sorted_values = sorted(values)
    return {
        "count": len(sorted_values),
        "p50": round(_percentile(sorted_values, 0.50), 2),
        "p95": round(_percentile(sorted_values, 0.95), 2),
        "max": round(sorted_values[-1], 2),
    }


metrics = MetricsStore()
