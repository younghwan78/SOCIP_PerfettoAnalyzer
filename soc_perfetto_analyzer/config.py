from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ThreadTarget:
    event_name: str
    thread: str
    category: str
    merge_gap_ms: float | None = None


@dataclass(frozen=True)
class CpuCluster:
    name: str
    cpus: list[int]
    role: str = ""
    freq_hint_ghz: tuple[float | None, float | None] = (None, None)


@dataclass(frozen=True)
class CpuTopology:
    source: str
    clusters: list[CpuCluster]

    def cluster_for_cpu(self, cpu: int) -> str | None:
        for cluster in self.clusters:
            if cpu in cluster.cpus:
                return cluster.name
        return None


@dataclass(frozen=True)
class ClockChangeFilterConfig:
    baseline: str = "duration_weighted_mean"
    ramp_delta_pct: float = 15.0
    drop_delta_pct: float = 15.0
    min_duration_ms: float = 5.0
    merge_gap_ms: float = 3.0
    max_rows: int = 20
    include_unknown: bool = False


@dataclass(frozen=True)
class ReportFilters:
    clock_change: ClockChangeFilterConfig = field(default_factory=ClockChangeFilterConfig)


@dataclass(frozen=True)
class EventConfig:
    path: Path
    description: str
    config_version: str
    event_count: int
    thread_targets: list[ThreadTarget]
    raw: dict[str, Any]
    cpu_topology: CpuTopology = field(default_factory=lambda: CpuTopology(source="absent", clusters=[]))
    report_filters: ReportFilters = field(default_factory=ReportFilters)


def load_event_config(path: Path | str) -> EventConfig:
    config_path = Path(path)
    raw = _load_mapping(config_path)
    events = raw.get("events") or []
    targets = _extract_thread_targets(events)
    return EventConfig(
        path=config_path,
        description=str(raw.get("description") or ""),
        config_version=str(raw.get("config_version") or "unknown"),
        event_count=len(events),
        thread_targets=targets,
        cpu_topology=_extract_cpu_topology(raw.get("cpu_topology")),
        report_filters=_extract_report_filters(raw.get("report_filters")),
        raw=raw,
    )


def load_cpu_topology_config(path: Path | str) -> CpuTopology:
    raw = _load_mapping(Path(path))
    value = raw.get("cpu_topology") if "cpu_topology" in raw else raw
    return _extract_cpu_topology(value)


def _load_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml
        except ModuleNotFoundError as exc:
            raise RuntimeError("PyYAML is required to read YAML event configs") from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON/YAML object")
    return data


def _extract_thread_targets(events: list[dict[str, Any]]) -> list[ThreadTarget]:
    targets: list[ThreadTarget] = []
    seen: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("event_type") != "Task":
            continue
        event_name = str(event.get("event_name") or "unnamed")
        merge_gap = _to_float(event.get("merge_gap_msec"))
        for thread in _condition_threads(event):
            if thread in seen:
                continue
            seen.add(thread)
            targets.append(
                ThreadTarget(
                    event_name=event_name,
                    thread=thread,
                    category=_infer_category(event_name, thread),
                    merge_gap_ms=merge_gap,
                )
            )
    return targets


def _condition_threads(event: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("wake_condition", "start_condition", "end_condition"):
        for cond in _iter_conditions(event.get(key)):
            match_field = str(cond.get("match_field") or "")
            if match_field not in {"comm", "next_comm", "prev_comm", "newcomm", "oldcomm"}:
                continue
            value = cond.get("match_value")
            if isinstance(value, str) and value:
                values.append(value)
    return values


def _iter_conditions(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _infer_category(event_name: str, thread: str) -> str:
    text = f"{event_name} {thread}".lower()
    if any(token in text for token in ("isp", "eis", "gdc", "sensor", "camera", "cam", "personal_im", "image")):
        return "camera_hal"
    if any(token in text for token in ("codec", "c2", "omx", "mfc", "apv")):
        return "codec_hal"
    if any(token in text for token in ("hwc", "composer", "display", "vsync")):
        return "hwc"
    return "configured_task"


def _to_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _extract_cpu_topology(value: Any) -> CpuTopology:
    if not isinstance(value, dict):
        return CpuTopology(source="absent", clusters=[])
    clusters = []
    for cluster in value.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        name = str(cluster.get("name") or "").strip()
        cpus = _to_int_list(cluster.get("cpus"))
        if not name or not cpus:
            continue
        freq_hint = cluster.get("freq_hint_ghz") or {}
        clusters.append(
            CpuCluster(
                name=name,
                cpus=cpus,
                role=str(cluster.get("role") or ""),
                freq_hint_ghz=(_to_float(freq_hint.get("min")), _to_float(freq_hint.get("max")))
                if isinstance(freq_hint, dict)
                else (None, None),
            )
        )
    return CpuTopology(source=str(value.get("source") or "event_config"), clusters=clusters)


def _extract_report_filters(value: Any) -> ReportFilters:
    if not isinstance(value, dict):
        return ReportFilters()
    return ReportFilters(clock_change=_extract_clock_change_filter(value.get("clock_change")))


def _extract_clock_change_filter(value: Any) -> ClockChangeFilterConfig:
    if not isinstance(value, dict):
        return ClockChangeFilterConfig()
    default = ClockChangeFilterConfig()
    return ClockChangeFilterConfig(
        baseline=str(value.get("baseline") or default.baseline),
        ramp_delta_pct=_to_float(value.get("ramp_delta_pct")) or default.ramp_delta_pct,
        drop_delta_pct=_to_float(value.get("drop_delta_pct")) or default.drop_delta_pct,
        min_duration_ms=_to_float(value.get("min_duration_ms")) or default.min_duration_ms,
        merge_gap_ms=_to_float(value.get("merge_gap_ms")) or default.merge_gap_ms,
        max_rows=_to_positive_int(value.get("max_rows"), default.max_rows),
        include_unknown=_to_bool(value.get("include_unknown"), default.include_unknown),
    )


def _to_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    cpus: list[int] = []
    for item in value:
        try:
            cpus.append(int(item))
        except (TypeError, ValueError):
            continue
    return cpus


def _to_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _to_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    return default
