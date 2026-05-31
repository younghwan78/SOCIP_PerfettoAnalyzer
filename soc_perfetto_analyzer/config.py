from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ThreadTarget:
    event_name: str
    thread: str
    category: str
    merge_gap_ms: float | None = None


@dataclass(frozen=True)
class EventConfig:
    path: Path
    description: str
    config_version: str
    event_count: int
    thread_targets: list[ThreadTarget]
    raw: dict[str, Any]


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
        raw=raw,
    )


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
