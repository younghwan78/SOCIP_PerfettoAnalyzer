from __future__ import annotations

from pathlib import Path
from typing import Any

from soc_perfetto_analyzer import __version__
from soc_perfetto_analyzer.analysis import AnalysisResult
from soc_perfetto_analyzer.config import EventConfig


def build_report_json(model: dict[str, Any], analysis: AnalysisResult, config: EventConfig) -> dict[str, Any]:
    return {
        "metadata": {
            "scenario": model["meta"]["scenario"],
            "device": model["meta"]["device"],
            "soc": model["meta"]["soc"],
            "trace_duration_ms": round(analysis.trace_duration_s * 1000) if analysis.trace_duration_s is not None else None,
            "analysis_window_ms": None,
            "generated_at": model["meta"]["generated"],
            "analyzer_version": __version__,
            "trace_processor_version": analysis.trace_processor_version,
            "trace_path": str(analysis.trace_path),
            "event_config": str(config.path),
        },
        "capability": model["capability"]["sources"],
        "integrity": model["capability"]["integrity"],
        "hardware_usage": model["hw_usage"],
        "sw_portion": model["portion"]["rows"],
        "thread_runtime": model["runtime"]["rows"],
        "thread_function_bottlenecks": model["pmu"]["function_bottlenecks"],
        "wakeup_jitter": model["jitter"]["rows"],
        "periods": [],
        "cpu_clock": model["clock"],
        "cluster_clock_attribution": model["clock"].get("ramp_rows", []),
        "contention": model["contention"]["corunners"],
        "issues": model["top_issues"],
        "caveats": model["appendix"]["caveats"],
        "appendix": {
            "unmatched": model["appendix"]["unmatched"],
            "ambiguous": model["appendix"]["ambiguous"],
            "versions": model["appendix"]["versions"],
            "matched_threads": analysis.matched_threads,
            "raw_string_hits": analysis.raw_string_hits,
        },
    }


def build_trace_inventory(analysis: AnalysisResult, config: EventConfig) -> dict[str, Any]:
    return {
        "trace_path": str(analysis.trace_path),
        "trace_size_bytes": analysis.trace_size_bytes,
        "trace_duration_s": analysis.trace_duration_s,
        "trace_processor": analysis.capability.trace_processor,
        "configured_thread_targets": [target.thread for target in config.thread_targets],
        "matched_threads": analysis.matched_threads,
        "raw_string_hits": analysis.raw_string_hits,
    }


def write_output_bundle(out_dir: Path, model: dict[str, Any], analysis: AnalysisResult, config: EventConfig, gate: Any) -> None:
    import json
    from dataclasses import asdict

    metrics_dir = out_dir / "metrics"
    appendix_dir = out_dir / "appendix"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    appendix_dir.mkdir(parents=True, exist_ok=True)

    report_json = build_report_json(model, analysis, config)
    _write_json(out_dir / "report.json", report_json)
    _write_json(out_dir / "trace_inventory.json", build_trace_inventory(analysis, config))
    _write_json(out_dir / "capability.json", {"sources": model["capability"]["sources"], "integrity": model["capability"]["integrity"]})
    _write_json(metrics_dir / "hardware_usage.json", model["hw_usage"])
    _write_json(metrics_dir / "sw_portion.json", model["portion"])
    _write_json(metrics_dir / "thread_runtime.json", model["runtime"]["rows"])
    _write_json(metrics_dir / "thread_function_bottlenecks.json", model["pmu"]["function_bottlenecks"])
    _write_json(metrics_dir / "wakeup_jitter.json", model["jitter"]["rows"])
    _write_json(metrics_dir / "periods.json", report_json["periods"])
    _write_json(metrics_dir / "cpu_clock.json", model["clock"])
    _write_json(metrics_dir / "cluster_clock_attribution.json", report_json["cluster_clock_attribution"])
    _write_json(metrics_dir / "contention.json", model["contention"])
    _write_json(out_dir / "quality_gate.json", asdict(gate))

    _write_csv(appendix_dir / "matched_threads.csv", ["thread"], [[thread] for thread in analysis.matched_threads])
    unmatched = [[thread] for thread in model["appendix"]["unmatched"]]
    _write_csv(appendix_dir / "unmatched_patterns.csv", ["pattern"], unmatched)
    ambiguous = [[value] for value in model["appendix"]["ambiguous"]]
    _write_csv(appendix_dir / "ambiguous_matches.csv", ["pattern"], ambiguous)
    (appendix_dir / "sql_queries.txt").write_text(
        "TraceProcessor SQL was unavailable; no SQL queries were executed.\n",
        encoding="utf-8",
    )


def _write_json(path: Path, value: Any) -> None:
    import json

    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    lines = [",".join(header)]
    lines.extend(",".join(_escape_csv(cell) for cell in row) for row in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _escape_csv(value: str) -> str:
    if any(ch in value for ch in [",", '"', "\n"]):
        return '"' + value.replace('"', '""') + '"'
    return value
