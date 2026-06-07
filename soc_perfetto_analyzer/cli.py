from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from .analysis import analyze_trace
from .config import load_cpu_topology_config, load_event_config
from .quality_gate import evaluate_phase9_quality_gate
from .report.html import render_report
from .report.json_report import write_output_bundle
from .report.model import build_report_model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="soc_perfetto_analyzer")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="Analyze a Perfetto trace and render a report bundle")
    analyze.add_argument("--trace", required=True, type=Path)
    analyze.add_argument("--event-config", "--scenario", dest="event_config", required=True, type=Path)
    analyze.add_argument("--topology-config", type=Path)
    analyze.add_argument("--out", required=True, type=Path)

    render = sub.add_parser("render-report", help="Render report.html from a trace and event config")
    render.add_argument("--trace", required=True, type=Path)
    render.add_argument("--event-config", "--scenario", dest="event_config", required=True, type=Path)
    render.add_argument("--topology-config", type=Path)
    render.add_argument("--out", required=True, type=Path)

    check = sub.add_parser("check-trace", help="Run capability audit and Phase 9 report model gate")
    check.add_argument("--trace", required=True, type=Path)
    check.add_argument("--event-config", "--scenario", dest="event_config", required=True, type=Path)
    check.add_argument("--topology-config", type=Path)

    args = parser.parse_args(argv)
    if args.command == "analyze":
        return _cmd_analyze(args.trace, args.event_config, args.out, args.topology_config)
    if args.command == "render-report":
        return _cmd_render(args.trace, args.event_config, args.out, args.topology_config)
    if args.command == "check-trace":
        return _cmd_check(args.trace, args.event_config, args.topology_config)
    return 2


def _load_config(event_config: Path, topology_config: Path | None = None):
    config = load_event_config(event_config)
    if topology_config is None:
        return config
    return replace(config, cpu_topology=load_cpu_topology_config(topology_config))


def _cmd_analyze(trace: Path, event_config: Path, out_dir: Path, topology_config: Path | None = None) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    config = _load_config(event_config, topology_config)
    analysis = analyze_trace(trace, config)
    model = build_report_model(analysis, config)
    html_path = render_report(model, out_dir / "report.html")
    result = evaluate_phase9_quality_gate(model, html_path=html_path)
    write_output_bundle(out_dir, model, analysis, config, result)
    print(f"wrote {out_dir / 'report.html'}")
    print(f"wrote {out_dir / 'report.json'}")
    print(f"phase9_quality_gate={'PASS' if result.passed else 'FAIL'}")
    if not result.passed:
        for failure in result.failures:
            print(f"- {failure}")
        return 1
    return 0


def _cmd_render(trace: Path, event_config: Path, out_path: Path, topology_config: Path | None = None) -> int:
    config = _load_config(event_config, topology_config)
    analysis = analyze_trace(trace, config)
    model = build_report_model(analysis, config)
    render_report(model, out_path)
    print(f"wrote {out_path}")
    return 0


def _cmd_check(trace: Path, event_config: Path, topology_config: Path | None = None) -> int:
    config = _load_config(event_config, topology_config)
    analysis = analyze_trace(trace, config)
    model = build_report_model(analysis, config)
    result = evaluate_phase9_quality_gate(model)
    print(f"phase9_quality_gate={'PASS' if result.passed else 'FAIL'}")
    for check in result.checks:
        print(f"[check] {check}")
    for failure in result.failures:
        print(f"[fail] {failure}")
    if not analysis.capability.pmu:
        print("recommendation: add linux.perf with HW_CPU_CYCLES and HW_INSTRUCTIONS follower counters.")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
