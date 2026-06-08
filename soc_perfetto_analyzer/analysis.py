from __future__ import annotations

import math
import re
import statistics
from bisect import bisect_left, bisect_right
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
    starts_s: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class CpuClusterInfo:
    name: str
    cpus: list[int]
    min_freq_ghz: float | None = None
    max_freq_ghz: float | None = None
    source: str = "fallback"


@dataclass(frozen=True)
class SchedRun:
    event_name: str
    category: str
    thread: str
    utid: int
    ts_s: float
    dur_us: float
    cpu: int
    cluster: str
    freq_ghz: float | None
    is_target: bool


@dataclass(frozen=True)
class ClockRampWindow:
    cluster: str
    start_s: float
    peak_s: float
    end_s: float
    baseline_ghz: float
    peak_ghz: float
    delta_pct: float
    target_runtime_us: float
    non_target_runtime_us: float
    new_non_target_threads: int
    target_migrations_into_cluster: int
    periodicity_score: float
    attribution: str
    confidence: str
    evidence: list[str]
    top_corunners: list[dict[str, str | float]]


@dataclass(frozen=True)
class PmuCapability:
    has_perf_samples: bool
    has_callstacks: bool
    has_cycles: bool
    has_instructions: bool
    classifier_events: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FunctionHotspot:
    thread: str
    function: str
    mapping: str
    source_file: str | None
    line_number: int | None
    self_cycles: float | None
    cumulative_cycles: float | None
    self_samples: int
    cumulative_samples: int
    sample_pct: float
    ipc: float | None
    cache_miss_pct: float | None
    frontend_stall_pct: float | None
    backend_stall_pct: float | None
    wait_pct: float | None
    classification: str
    confidence: str
    reason: str


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
    cpu_clusters: list[CpuClusterInfo] = field(default_factory=list)
    target_runs: list[SchedRun] = field(default_factory=list)
    clock_ramp_windows: list[ClockRampWindow] = field(default_factory=list)
    pmu_capability: PmuCapability | None = None
    function_hotspots_by_thread: dict[str, list[FunctionHotspot]] = field(default_factory=dict)
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
        pmu_capability = _query_pmu_capability(tp, pmu_count)
        hw_counter_count = _scalar(tp, "select count(*) as c from counter_track where lower(name) glob '*gpu*util*' or lower(name) glob '*kgsl*busy*' or lower(name) glob '*mali*util*'", "c")
        cpu_clusters = _configured_cpu_clusters(config) or _query_frequency_clusters(tp)
        freq_series = _query_frequency_series(tp, start_ns, cpu_clusters)
        matched, runtime_rows, runnable_by_cluster, runnable_by_thread = _query_target_runtime(tp, config, start_ns, cpu_clusters)
        sched_runs = _query_sched_runs(tp, config, start_ns, cpu_clusters, freq_series)
        target_runs = [run for run in sched_runs if run.is_target]
        clock_ramp_windows = _detect_clock_ramp_windows(freq_series, sched_runs)
        function_hotspots = _query_function_hotspots(tp, config, pmu_capability)
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
            cpu_clusters=cpu_clusters,
            target_runs=target_runs,
            clock_ramp_windows=clock_ramp_windows,
            pmu_capability=pmu_capability,
            function_hotspots_by_thread=function_hotspots,
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


def _query_target_runtime(tp: Any, config: EventConfig, start_ns: int, cpu_clusters: list[CpuClusterInfo] | None = None) -> tuple[list[str], list[ThreadRuntime], dict[str, list[float]], dict[str, list[float]]]:
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
        sched_rows = list(tp.query(f"select ts, dur, cpu from sched where utid in ({utid_sql}) and dur > 0 order by ts"))
        if not sched_rows:
            continue
        matched.append(target.thread)
        samples_us = [float(row.dur) / 1000.0 for row in sched_rows]
        cpus = [int(row.cpu) for row in sched_rows if row.cpu is not None]
        starts_s = [(int(row.ts) - start_ns) / 1_000_000_000 for row in sched_rows]
        runtime_rows.append(ThreadRuntime(target.category, target.thread, samples_us, cpus, starts_s))
        cluster = _dominant_cluster(cpus, cpu_clusters or [])
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


def _query_frequency_series(tp: Any, start_ns: int, cpu_clusters: list[CpuClusterInfo] | None = None) -> dict[str, tuple[list[float], list[float]]]:
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
    for row in rows:
        cluster = _cluster_name_for_cpu(int(row.cpu), cpu_clusters or [])
        ts, values = by_cluster.setdefault(cluster, ([], []))
        ts.append((int(row.ts) - start_ns) / 1_000_000_000)
        values.append(float(row.value) / 1_000_000)
    return by_cluster


def _query_sched_runs(tp: Any, config: EventConfig, start_ns: int, cpu_clusters: list[CpuClusterInfo], freq_series: dict[str, tuple[list[float], list[float]]]) -> list[SchedRun]:
    target_by_thread = {target.thread: target for target in config.thread_targets}
    rows = list(
        tp.query(
            """
            select s.ts, s.dur, s.cpu, s.utid, t.name as thread_name
            from sched s
            join thread t on s.utid = t.utid
            where s.dur > 0
            order by s.ts
            """
        )
    )
    runs: list[SchedRun] = []
    for row in rows:
        thread = str(row.thread_name or "")
        target = target_by_thread.get(thread)
        cluster = _cluster_name_for_cpu(int(row.cpu), cpu_clusters)
        ts_s = (int(row.ts) - start_ns) / 1_000_000_000
        runs.append(
            SchedRun(
                event_name=target.event_name if target else "non_target",
                category=target.category if target else "non_target",
                thread=thread,
                utid=int(row.utid),
                ts_s=ts_s,
                dur_us=float(row.dur) / 1000.0,
                cpu=int(row.cpu),
                cluster=cluster,
                freq_ghz=_frequency_at(freq_series.get(cluster), ts_s),
                is_target=target is not None,
            )
        )
    return runs


def _frequency_at(series: tuple[list[float], list[float]] | None, start_s: float) -> float | None:
    if not series:
        return None
    ts, values = series
    if not ts or not values:
        return None
    idx = bisect_right(ts, start_s) - 1
    if idx < 0:
        return values[0]
    return values[min(idx, len(values) - 1)]


def _query_frequency_clusters(tp: Any) -> list[CpuClusterInfo]:
    rows = list(
        tp.query(
            """
            select cct.cpu as cpu, min(c.value) as min_hz, max(c.value) as max_hz
            from counter c
            join cpu_counter_track cct on c.track_id = cct.id
            where cct.type='cpu_frequency'
            group by cct.cpu
            order by cct.cpu
            """
        )
    )
    groups: dict[float, list[tuple[int, float, float]]] = {}
    for row in rows:
        max_ghz = round(float(row.max_hz) / 1_000_000, 3)
        min_ghz = round(float(row.min_hz) / 1_000_000, 3)
        groups.setdefault(max_ghz, []).append((int(row.cpu), min_ghz, max_ghz))
    if not groups:
        return []
    ordered = sorted(groups.items(), key=lambda item: item[0])
    default_names = ["little", "mid", "big"]
    clusters: list[CpuClusterInfo] = []
    for idx, (_, cpu_rows) in enumerate(ordered):
        name = default_names[idx] if idx < len(default_names) else f"cluster{idx}"
        cpus = [cpu for cpu, _, _ in cpu_rows]
        min_freq = min(min_ghz for _, min_ghz, _ in cpu_rows)
        max_freq = max(max_ghz for _, _, max_ghz in cpu_rows)
        clusters.append(CpuClusterInfo(name=name, cpus=cpus, min_freq_ghz=min_freq, max_freq_ghz=max_freq, source="freq_max_grouping"))
    return clusters


def _detect_clock_ramp_windows(freq_series: dict[str, tuple[list[float], list[float]]], sched_runs: list[SchedRun]) -> list[ClockRampWindow]:
    windows: list[ClockRampWindow] = []
    runs_by_cluster, run_times_by_cluster = _index_runs_by_cluster(sched_runs)
    target_runs = [run for run in sched_runs if run.is_target]
    periodicity_score = _periodicity_score(target_runs)
    for cluster, (times, values) in freq_series.items():
        if len(times) < 2 or len(values) < 2:
            continue
        baseline = statistics.median(values)
        if baseline <= 0:
            continue
        threshold = baseline * 1.15
        idx = 0
        while idx < len(times):
            if values[idx] < threshold:
                idx += 1
                continue
            start_idx = idx
            peak_idx = idx
            while idx < len(times) and values[idx] >= threshold:
                if values[idx] > values[peak_idx]:
                    peak_idx = idx
                idx += 1
            end_idx = min(idx, len(times) - 1)
            start_s = float(times[start_idx])
            end_s = float(times[end_idx])
            prior_start = max(0.0, start_s - max(0.05, end_s - start_s))
            cluster_runs = runs_by_cluster.get(cluster, [])
            cluster_run_times = run_times_by_cluster.get(cluster, [])
            window = _build_clock_ramp_window(
                cluster=cluster,
                start_s=start_s,
                peak_s=float(times[peak_idx]),
                end_s=end_s,
                baseline_ghz=float(baseline),
                peak_ghz=float(values[peak_idx]),
                window_runs=_runs_in_window(cluster_runs, cluster_run_times, start_s, end_s),
                prior_runs=_runs_in_window(cluster_runs, cluster_run_times, prior_start, start_s, include_end=False),
                target_runs=target_runs,
                periodicity_score=periodicity_score,
            )
            if window is not None:
                windows.append(window)
    return windows


def _index_runs_by_cluster(sched_runs: list[SchedRun]) -> tuple[dict[str, list[SchedRun]], dict[str, list[float]]]:
    runs_by_cluster: dict[str, list[SchedRun]] = {}
    for run in sched_runs:
        runs_by_cluster.setdefault(run.cluster, []).append(run)
    times_by_cluster: dict[str, list[float]] = {}
    for cluster, runs in runs_by_cluster.items():
        runs.sort(key=lambda run: run.ts_s)
        times_by_cluster[cluster] = [run.ts_s for run in runs]
    return runs_by_cluster, times_by_cluster


def _runs_in_window(runs: list[SchedRun], times: list[float], start_s: float, end_s: float, include_end: bool = True) -> list[SchedRun]:
    if not runs or not times:
        return []
    left = bisect_left(times, start_s)
    right = bisect_right(times, end_s) if include_end else bisect_left(times, end_s)
    return runs[left:right]


def _build_clock_ramp_window(cluster: str, start_s: float, peak_s: float, end_s: float, baseline_ghz: float, peak_ghz: float, window_runs: list[SchedRun], prior_runs: list[SchedRun], target_runs: list[SchedRun], periodicity_score: float) -> ClockRampWindow | None:
    if peak_ghz <= baseline_ghz:
        return None
    prior_threads = {
        run.thread
        for run in prior_runs
        if not run.is_target
    }
    target_runtime_us = sum(run.dur_us for run in window_runs if run.is_target)
    non_target_runtime_us = sum(run.dur_us for run in window_runs if not run.is_target)
    current_non_target_threads = {run.thread for run in window_runs if not run.is_target}
    new_non_target_threads = len(current_non_target_threads - prior_threads)
    migrations = _target_migrations_into_cluster(target_runs, cluster, start_s, end_s)
    attribution, confidence, evidence = _classify_ramp(
        target_runtime_us=target_runtime_us,
        non_target_runtime_us=non_target_runtime_us,
        new_non_target_threads=new_non_target_threads,
        migrations=migrations,
        periodicity_score=periodicity_score,
    )
    return ClockRampWindow(
        cluster=cluster,
        start_s=round(start_s, 6),
        peak_s=round(peak_s, 6),
        end_s=round(end_s, 6),
        baseline_ghz=round(baseline_ghz, 3),
        peak_ghz=round(peak_ghz, 3),
        delta_pct=round((peak_ghz - baseline_ghz) / baseline_ghz * 100.0, 1),
        target_runtime_us=round(target_runtime_us, 1),
        non_target_runtime_us=round(non_target_runtime_us, 1),
        new_non_target_threads=new_non_target_threads,
        target_migrations_into_cluster=migrations,
        periodicity_score=round(periodicity_score, 2),
        attribution=attribution,
        confidence=confidence,
        evidence=evidence,
        top_corunners=_top_corunners(window_runs),
    )


def _classify_ramp(target_runtime_us: float, non_target_runtime_us: float, new_non_target_threads: int, migrations: int, periodicity_score: float) -> tuple[str, str, list[str]]:
    periodic = periodicity_score >= 0.70 and migrations >= 1 and target_runtime_us > 0
    added = new_non_target_threads >= 1 and non_target_runtime_us >= max(1.0, target_runtime_us * 1.2)
    evidence: list[str] = []
    if added:
        evidence.append(f"non-target runtime {non_target_runtime_us:.1f}us vs target {target_runtime_us:.1f}us")
        evidence.append(f"{new_non_target_threads} new non-target threads in ramp window")
    if periodic:
        evidence.append(f"target migration count {migrations}")
        evidence.append(f"periodicity score {periodicity_score:.2f}")
    if added and periodic:
        return "mixed_pressure", "medium", evidence
    if added:
        return "added_task_pressure", "medium", evidence
    if periodic:
        return "periodic_target_migration", "medium", evidence
    return "unknown", "low", ["insufficient scheduler evidence for clock ramp attribution"]


def _target_migrations_into_cluster(target_runs: list[SchedRun], cluster: str, start_s: float, end_s: float) -> int:
    count = 0
    by_thread: dict[str, list[SchedRun]] = {}
    for run in target_runs:
        by_thread.setdefault(run.thread, []).append(run)
    for runs in by_thread.values():
        ordered = sorted(runs, key=lambda run: run.ts_s)
        for idx, run in enumerate(ordered):
            if idx == 0 or run.cluster != cluster or not (start_s <= run.ts_s <= end_s):
                continue
            if ordered[idx - 1].cluster != cluster:
                count += 1
    return count


def _periodicity_score(target_runs: list[SchedRun]) -> float:
    by_thread: dict[str, list[float]] = {}
    for run in target_runs:
        by_thread.setdefault(run.thread, []).append(run.ts_s)
    scores = []
    for starts in by_thread.values():
        ordered = sorted(starts)
        if len(ordered) < 3:
            continue
        intervals_ms = [(right - left) * 1000.0 for left, right in zip(ordered, ordered[1:])]
        for candidate in (16.7, 33.3):
            errors = [abs(value - candidate) / candidate for value in intervals_ms]
            score = max(0.0, 1.0 - statistics.median(errors))
            scores.append(score)
    return max(scores or [0.0])


def _top_corunners(window_runs: list[SchedRun]) -> list[dict[str, str | float]]:
    totals: dict[str, float] = {}
    for run in window_runs:
        if run.is_target:
            continue
        totals[run.thread] = totals.get(run.thread, 0.0) + run.dur_us
    rows = [
        {"thread": thread, "runtime_us": round(runtime_us, 1)}
        for thread, runtime_us in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ]
    return rows[:3]


def _first(tp: Any, query: str) -> Any:
    rows = list(tp.query(query))
    if not rows:
        raise RuntimeError(f"query returned no rows: {query}")
    return rows[0]


def _scalar(tp: Any, query: str, column: str) -> int:
    row = _first(tp, query)
    return int(getattr(row, column) or 0)


def _safe_scalar(tp: Any, query: str, column: str) -> int:
    try:
        return _scalar(tp, query, column)
    except Exception:
        return 0


def _query_pmu_capability(tp: Any, pmu_counter_count: int) -> PmuCapability:
    perf_samples = _safe_scalar(tp, "select count(*) as c from perf_sample", "c")
    cpu_profile_samples = _safe_scalar(tp, "select count(*) as c from cpu_profile_stack_sample", "c")
    callsites = _safe_scalar(tp, "select count(*) as c from stack_profile_callsite", "c")
    frames = _safe_scalar(tp, "select count(*) as c from stack_profile_frame", "c")
    mappings = _safe_scalar(tp, "select count(*) as c from stack_profile_mapping", "c")
    has_perf_samples = (perf_samples + cpu_profile_samples) > 0
    has_callstacks = has_perf_samples and callsites > 0 and frames > 0 and mappings > 0
    caveats: list[str] = []
    if not has_perf_samples:
        caveats.append("N/A: perf callstack samples absent; thread function bottlenecks cannot be measured.")
    elif not has_callstacks:
        caveats.append("N/A: stack profile tables absent or empty; function attribution cannot be measured.")
    if not pmu_counter_count:
        caveats.append("N/A: linux.perf PMU counter tracks absent; cycle/instruction ratios unavailable.")
    return PmuCapability(
        has_perf_samples=has_perf_samples,
        has_callstacks=has_callstacks,
        has_cycles=pmu_counter_count > 0,
        has_instructions=pmu_counter_count > 0,
        classifier_events=[],
        caveats=caveats,
    )


def _query_function_hotspots(tp: Any, config: EventConfig, pmu_capability: PmuCapability) -> dict[str, list[FunctionHotspot]]:
    if not (pmu_capability.has_perf_samples and pmu_capability.has_callstacks):
        return {}
    target_threads = [target.thread for target in config.thread_targets]
    if not target_threads:
        return {}
    thread_sql = ",".join(_sql_string(thread) for thread in target_threads)
    try:
        rows = list(
            tp.query(
                f"""
                select
                  t.name as thread,
                  f.name as function,
                  m.name as mapping,
                  count(*) as self_samples
                from perf_sample s
                join thread t on s.utid = t.utid
                join stack_profile_callsite c on s.callsite_id = c.id
                join stack_profile_frame f on c.frame_id = f.id
                join stack_profile_mapping m on f.mapping = m.id
                where t.name in ({thread_sql})
                group by t.name, f.name, m.name
                order by t.name, self_samples desc, f.name
                """
            )
        )
    except Exception:
        return {}

    by_thread: dict[str, list[Any]] = {}
    for row in rows:
        by_thread.setdefault(str(row.thread), []).append(row)

    result: dict[str, list[FunctionHotspot]] = {}
    for thread, thread_rows in by_thread.items():
        total_samples = sum(int(row.self_samples or 0) for row in thread_rows) or 1
        hotspots: list[FunctionHotspot] = []
        for row in sorted(thread_rows, key=lambda item: (-int(item.self_samples or 0), str(item.function)))[:3]:
            samples = int(row.self_samples or 0)
            classification, confidence, reason = _classify_function_hotspot(None, None, None, None, None)
            hotspots.append(
                FunctionHotspot(
                    thread=thread,
                    function=str(row.function or "unknown"),
                    mapping=str(row.mapping or "unknown"),
                    source_file=None,
                    line_number=None,
                    self_cycles=None,
                    cumulative_cycles=None,
                    self_samples=samples,
                    cumulative_samples=samples,
                    sample_pct=round(samples / total_samples * 100.0, 1),
                    ipc=None,
                    cache_miss_pct=None,
                    frontend_stall_pct=None,
                    backend_stall_pct=None,
                    wait_pct=None,
                    classification=classification,
                    confidence=confidence,
                    reason=reason,
                )
            )
        if hotspots:
            result[thread] = hotspots
    return result


def _classify_function_hotspot(ipc: float | None, cache_miss_pct: float | None, frontend_stall_pct: float | None, backend_stall_pct: float | None, wait_pct: float | None) -> tuple[str, str, str]:
    if wait_pct is not None and wait_pct >= 30.0:
        return "io_or_wait_bound", "medium", f"wait evidence is high ({wait_pct:.1f}% wall time); cycles alone do not represent blocked time."
    if backend_stall_pct is not None and backend_stall_pct >= 30.0:
        return "memory_bound", "medium", f"backend stall evidence is high ({backend_stall_pct:.1f}%)."
    if frontend_stall_pct is not None and frontend_stall_pct >= 30.0:
        return "frontend_bound", "medium", f"frontend stall evidence is high ({frontend_stall_pct:.1f}%)."
    if ipc is not None and ipc >= 1.5 and (cache_miss_pct is None or cache_miss_pct < 10.0):
        return "compute_bound", "medium", f"IPC is {ipc:.2f} and cache miss evidence is not high."
    return "unknown", "low", "N/A: classifier PMU events unavailable; top function is sample-based only."


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _configured_cpu_clusters(config: EventConfig) -> list[CpuClusterInfo]:
    clusters = []
    for cluster in config.cpu_topology.clusters:
        min_freq, max_freq = cluster.freq_hint_ghz
        clusters.append(
            CpuClusterInfo(
                name=cluster.name,
                cpus=cluster.cpus,
                min_freq_ghz=min_freq,
                max_freq_ghz=max_freq,
                source=f"event_config:{config.cpu_topology.source}",
            )
        )
    return clusters


def _cluster_name_for_cpu(cpu: int, cpu_clusters: list[CpuClusterInfo]) -> str:
    for cluster in cpu_clusters:
        if cpu in cluster.cpus:
            return cluster.name
    return _cpu_cluster(cpu)


def _dominant_cluster(cpus: list[int], cpu_clusters: list[CpuClusterInfo] | None = None) -> str:
    if not cpus:
        return "unknown"
    counts: dict[str, int] = {}
    for cpu in cpus:
        cluster = _cluster_name_for_cpu(cpu, cpu_clusters or [])
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
