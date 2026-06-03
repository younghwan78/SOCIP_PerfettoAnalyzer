from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

from .config import EventConfig


@dataclass(frozen=True)
class CapabilitySummary:
    sched_switch: bool
    sched_waking: bool
    cpu_frequency: bool
    irq_events: bool
    hw_counters: bool
    pmu: bool
    trace_processor: bool
    caveats: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ThreadRuntime:
    category: str
    thread: str
    samples_us: list[float]
    cpus: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class AnalysisResult:
    trace_path: Path
    trace_size_bytes: int
    trace_duration_s: float | None
    capability: CapabilitySummary
    matched_threads: list[str]
    runtime_rows: list[ThreadRuntime] = field(default_factory=list)
    wakeup_samples_by_cluster: dict[str, list[float]] = field(default_factory=dict)
    runnable_wait_by_thread: dict[str, list[float]] = field(default_factory=dict)
    freq_series: dict[str, tuple[list[float], list[float]]] = field(default_factory=dict)
    raw_string_hits: dict[str, int] = field(default_factory=dict)
    trace_processor_version: str = "unavailable"


class TraceProcessorAnalysisError(RuntimeError):
    pass


def analyze_trace(trace_path: Path | str, config: EventConfig) -> AnalysisResult:
    path = Path(trace_path)
    if not path.exists():
        raise FileNotFoundError(path)
    tp_error: str | None = None
    try:
        tp_result = _try_trace_processor_analysis(path, config)
    except TraceProcessorAnalysisError as exc:
        tp_result = None
        tp_error = str(exc)
    if tp_result is not None:
        return tp_result
    payload = _read_prefix(path)
    hits = _scan_thread_strings(payload, [target.thread for target in config.thread_targets])
    matched = [thread for thread, count in hits.items() if count > 0]
    tp_caveat = f"TraceProcessor analysis failed: {tp_error}" if tp_error else None
    capability = _detect_capabilities(payload, trace_processor_caveat=tp_caveat)
    return AnalysisResult(
        trace_path=path,
        trace_size_bytes=path.stat().st_size,
        trace_duration_s=None,
        capability=capability,
        matched_threads=matched,
        runtime_rows=[],
        wakeup_samples_by_cluster={},
        runnable_wait_by_thread={},
        freq_series={},
        raw_string_hits=hits,
    )


def _try_trace_processor_analysis(path: Path, config: EventConfig) -> AnalysisResult | None:
    try:
        from perfetto.trace_processor import TraceProcessor
    except Exception:
        return None
    tp: Any | None = None
    try:
        tp = TraceProcessor(trace=str(path))
        start_ns, end_ns = _trace_bounds(tp)
        counts = _table_counts(tp)
        pmu_count = _scalar(tp, "select count(*) as c from counter_track where lower(name) like '%cycles%' or lower(name) like '%instructions%' or lower(name) like '%linux.perf%'", "c")
        hw_counter_count = _scalar(tp, "select count(*) as c from counter_track where lower(name) glob '*gpu*util*' or lower(name) glob '*kgsl*busy*' or lower(name) glob '*mali*util*'", "c")
        matched, runtime_rows, runnable_by_cluster, runnable_by_thread = _query_target_runtime(tp, config)
        freq_series = _query_frequency_series(tp, start_ns)
        caveats = []
        if not pmu_count:
            caveats.append("linux.perf PMU samples absent or incomplete; cycle/inst/IPC marked N/A.")
        if not matched:
            caveats.append("No configured event_config target threads matched TraceProcessor thread table.")
        return AnalysisResult(
            trace_path=path,
            trace_size_bytes=path.stat().st_size,
            trace_duration_s=(end_ns - start_ns) / 1_000_000_000,
            capability=CapabilitySummary(
                sched_switch=counts["sched"] > 0,
                sched_waking=counts["waker"] > 0,
                cpu_frequency=counts["cpu_frequency"] > 0,
                irq_events=counts["ftrace"] > 0,
                hw_counters=hw_counter_count > 0,
                pmu=pmu_count > 0,
                trace_processor=True,
                caveats=caveats,
            ),
            matched_threads=matched,
            runtime_rows=runtime_rows,
            wakeup_samples_by_cluster=runnable_by_cluster,
            runnable_wait_by_thread=runnable_by_thread,
            freq_series=freq_series,
            raw_string_hits={target.thread: 1 if target.thread in matched else 0 for target in config.thread_targets},
            trace_processor_version=_perfetto_api_version(),
        )
    except Exception as exc:
        raise TraceProcessorAnalysisError(str(exc)) from exc
    finally:
        if tp is not None:
            try:
                tp.close()
            except Exception:
                pass


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * pct
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    weight = pos - lo
    return ordered[lo] * (1 - weight) + ordered[hi] * weight


def covariance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = statistics.mean(values)
    return 0.0 if avg == 0 else statistics.pstdev(values) / avg


def mad(values: list[float]) -> float:
    if not values:
        return 0.0
    med = statistics.median(values)
    return statistics.median([abs(value - med) for value in values])


def _read_prefix(path: Path) -> bytes:
    with path.open("rb") as fh:
        return fh.read()


def _perfetto_api_version() -> str:
    try:
        return f"perfetto-python {metadata.version('perfetto')}"
    except Exception:
        return "perfetto-python available"


def _trace_bounds(tp: Any) -> tuple[int, int]:
    row = _first(tp, "select start_ts, end_ts from trace_bounds")
    return int(row.start_ts), int(row.end_ts)


def _table_counts(tp: Any) -> dict[str, int]:
    row = _first(
        tp,
        """
        select
          (select count(*) from sched) as sched,
          (select count(*) from thread_state) as thread_state,
          (select count(*) from thread_state where waker_utid is not null) as waker,
          (select count(*) from counter_track where type='cpu_frequency') as cpu_frequency,
          (select count(*) from ftrace_event) as ftrace
        """,
    )
    return {
        "sched": int(row.sched or 0),
        "thread_state": int(row.thread_state or 0),
        "waker": int(row.waker or 0),
        "cpu_frequency": int(row.cpu_frequency or 0),
        "ftrace": int(row.ftrace or 0),
    }


def _query_target_runtime(tp: Any, config: EventConfig) -> tuple[list[str], list[ThreadRuntime], dict[str, list[float]], dict[str, list[float]]]:
    matched: list[str] = []
    runtime_rows: list[ThreadRuntime] = []
    runnable_by_cluster: dict[str, list[float]] = {}
    runnable_by_thread: dict[str, list[float]] = {}
    for target in config.thread_targets:
        thread_rows = list(tp.query(f"select utid, tid, name from thread where name={_sql_string(target.thread)}"))
        if not thread_rows:
            continue
        utids = [int(row.utid) for row in thread_rows]
        utid_sql = ",".join(str(utid) for utid in utids)
        sched_rows = list(tp.query(f"select dur, cpu from sched where utid in ({utid_sql}) and dur > 0 order by ts"))
        if not sched_rows:
            continue
        matched.append(target.thread)
        samples_us = [float(row.dur) / 1000.0 for row in sched_rows]
        cpus = [int(row.cpu) for row in sched_rows if row.cpu is not None]
        runtime_rows.append(ThreadRuntime(target.category, target.thread, samples_us, cpus))
        cluster = _dominant_cluster(cpus)
        runnable_rows = list(
            tp.query(
                f"select dur from thread_state where utid in ({utid_sql}) "
                "and state in ('R', 'R+') and dur > 0 order by ts"
            )
        )
        runnable_samples = [float(row.dur) / 1000.0 for row in runnable_rows]
        runnable_by_thread[target.thread] = runnable_samples
        runnable_by_cluster.setdefault(cluster, []).extend(runnable_samples)
    return matched, runtime_rows, {key: value for key, value in runnable_by_cluster.items() if value}, runnable_by_thread


def _query_frequency_series(tp: Any, start_ns: int) -> dict[str, tuple[list[float], list[float]]]:
    rows = list(
        tp.query(
            """
            select c.ts, c.value, cct.cpu
            from counter c
            join cpu_counter_track cct on c.track_id = cct.id
            where cct.type='cpu_frequency'
            order by c.ts
            """
        )
    )
    by_cluster: dict[str, tuple[list[float], list[float]]] = {}
    counts: dict[str, int] = {}
    for row in rows:
        cluster = _cpu_cluster(int(row.cpu))
        counts[cluster] = counts.get(cluster, 0) + 1
        if counts[cluster] > 600:
            continue
        ts, values = by_cluster.setdefault(cluster, ([], []))
        ts.append((int(row.ts) - start_ns) / 1_000_000_000)
        values.append(float(row.value) / 1_000_000)
    return by_cluster


def _first(tp: Any, query: str) -> Any:
    rows = list(tp.query(query))
    if not rows:
        raise RuntimeError(f"query returned no rows: {query}")
    return rows[0]


def _scalar(tp: Any, query: str, column: str) -> int:
    row = _first(tp, query)
    return int(getattr(row, column) or 0)


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _dominant_cluster(cpus: list[int]) -> str:
    if not cpus:
        return "unknown"
    counts: dict[str, int] = {}
    for cpu in cpus:
        cluster = _cpu_cluster(cpu)
        counts[cluster] = counts.get(cluster, 0) + 1
    return max(counts.items(), key=lambda item: item[1])[0]


def _cpu_cluster(cpu: int) -> str:
    if cpu <= 3:
        return "little"
    if cpu <= 6:
        return "mid"
    return "big"


def _scan_thread_strings(payload: bytes, threads: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for thread in threads:
        needle = thread.encode("utf-8", errors="ignore")
        result[thread] = payload.count(needle) if needle else 0
    return result


def _detect_capabilities(payload: bytes, trace_processor_caveat: str | None = None) -> CapabilitySummary:
    text = _ascii_view(payload)
    sched_switch = "sched_switch" in text
    sched_waking = "sched_waking" in text or "sched_wakeup" in text
    cpu_frequency = "cpu_frequency" in text or "cpufreq" in text
    irq_events = "irq" in text.lower()
    hw_counters = bool(re.search(r"gpu.*(util|busy)|kgsl.*busy|mali.*util", text, re.I | re.S))
    pmu = "linux.perf" in text or "HW_CPU_CYCLES" in text or "HW_INSTRUCTIONS" in text
    caveats: list[str] = []
    caveats.append(trace_processor_caveat or "TraceProcessor unavailable in this workspace; binary string inventory used for capability audit.")
    caveats.append("String inventory fallback does not produce runtime, wakeup, or frequency metrics.")
    if not pmu:
        caveats.append("linux.perf PMU samples absent or not discoverable; cycle/inst/IPC marked N/A.")
    if not sched_switch:
        caveats.append("sched_switch not discoverable from string inventory; runtime metrics are limited.")
    return CapabilitySummary(
        sched_switch=sched_switch,
        sched_waking=sched_waking,
        cpu_frequency=cpu_frequency,
        irq_events=irq_events,
        hw_counters=hw_counters,
        pmu=pmu,
        trace_processor=False,
        caveats=caveats,
    )


def _ascii_view(payload: bytes) -> str:
    return payload.decode("utf-8", errors="ignore")


def _synthetic_runtime_from_hits(config: EventConfig, hits: dict[str, int]) -> list[ThreadRuntime]:
    rows: list[ThreadRuntime] = []
    for target in config.thread_targets:
        count = hits.get(target.thread, 0)
        if count <= 0:
            continue
        samples = _sample_shape_from_count(count)
        rows.append(ThreadRuntime(target.category, target.thread, samples, [0, 4, 7][: min(3, len(samples))]))
    return rows


def _sample_shape_from_count(count: int) -> list[float]:
    base = max(400.0, min(2500.0, 250.0 + count * 80.0))
    n = max(3, min(40, count + 2))
    return [base * (0.72 + (idx % 5) * 0.14) for idx in range(n)]


def _synthetic_wakeup_from_runtime(rows: list[ThreadRuntime]) -> dict[str, list[float]]:
    if not rows:
        return {}
    samples: list[float] = []
    for row in rows:
        samples.extend([max(10.0, sample / 20.0) for sample in row.samples_us[:20]])
    return {"big": samples}


def _synthetic_freq_series() -> dict[str, tuple[list[float], list[float]]]:
    xs = [idx * 0.25 for idx in range(32)]
    return {"big": (xs, [2.4 if idx % 8 else 1.8 for idx, _ in enumerate(xs)])}
