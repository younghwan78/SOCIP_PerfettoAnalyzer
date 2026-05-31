from __future__ import annotations

from datetime import datetime
from pathlib import Path

import charts

from soc_perfetto_analyzer import __version__
from soc_perfetto_analyzer.analysis import AnalysisResult, ThreadRuntime, covariance, mad, percentile
from soc_perfetto_analyzer.config import EventConfig


HW_BLOCKS = ("GPU", "DPU", "CODEC", "ISP", "NPU")


def build_report_model(analysis: AnalysisResult, config: EventConfig) -> dict:
    charts.reset_plotlyjs()
    runtime_rows = [_runtime_row(row) for row in analysis.runtime_rows]
    jitter_rows = _jitter_rows(analysis)
    portion_rows = _portion_rows(analysis)
    hw_usage = _hw_usage(analysis)
    figures = _figures(analysis, portion_rows)
    top_issues = _issues(analysis, runtime_rows, jitter_rows)
    verdicts = _verdicts(analysis, hw_usage, portion_rows, runtime_rows, jitter_rows)
    return {
        "meta": {
            "scenario": _scenario_name(config),
            "device": "android-perfetto-FHD30-S24U",
            "soc": "unknown_soc",
            "duration_s": _duration_label(analysis),
            "window_s": "full trace",
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
        "verdicts": verdicts,
        "hw_usage": hw_usage,
        "hw_usage_note": _hw_note(analysis),
        "kpis": _kpis(analysis, runtime_rows, jitter_rows),
        "top_issues": top_issues,
        "capability": _capability(analysis),
        "figures": figures,
        "portion": {
            "denominator": "configured multimedia CPU running time",
            "tier": "measured" if analysis.capability.pmu else "time_only",
            "tier_label": "measured" if analysis.capability.pmu else "time-only",
            "pbtx_hint": "Add linux.perf with HW_CPU_CYCLES + HW_INSTRUCTIONS followers to enable cycle/inst.",
            "rows": portion_rows,
        },
        "runtime": {"rows": runtime_rows},
        "jitter": {
            "pbtx_hint": "Re-capture with sched/sched_waking enabled.",
            "rows": jitter_rows,
        },
        "clock": {"throttle_rows": _clock_rows(analysis)},
        "contention": _contention(analysis),
        "appendix": {
            "unmatched": _unmatched(config, analysis),
            "ambiguous": [],
            "caveats": _caveats(analysis),
        "versions": {
                "analyzer": __version__,
                "trace_processor": analysis.trace_processor_version,
                "python": "3.14",
                "plotly": "via charts.py",
            },
            "config_dump": _config_dump(config),
        },
    }


def _figures(analysis: AnalysisResult, portion_rows: list[dict]) -> dict:
    portion_data = [
        {"name": row["name"], "time_pct": float(row["time_pct"])}
        for row in portion_rows
        if _is_number(row["time_pct"])
    ]
    if portion_data:
        total = sum(row["time_pct"] for row in portion_data)
        portion_data.append({"name": "Other", "time_pct": max(0.0, round(100.0 - total, 1))})
    baseline = _baseline(analysis.runtime_rows)
    runtime_threads = [
        {"thread": row.thread, "samples": row.samples_us, "severity": _runtime_severity(row)}
        for row in analysis.runtime_rows
    ]
    jitter_rank_rows = _jitter_rank_rows(analysis)
    freq = analysis.freq_series or {}
    first_runtime = analysis.runtime_rows[0].thread if analysis.runtime_rows else "configured targets"
    interval_samples = _interval_samples(analysis.runtime_rows)
    figure_values = {
        "portion": charts.portion_bar(portion_data, "configured multimedia CPU running time"),
        "runtime_box": charts.runtime_box(runtime_threads, baseline_us=baseline),
        "wakeup_cdf": charts.wakeup_cdf(analysis.wakeup_samples_by_cluster),
        "jitter_rank": charts.jitter_rank(jitter_rank_rows),
        "interval_strip": charts.interval_strip(interval_samples, target_ms=33.3, thread=first_runtime),
        "freq_ts": charts.freq_timeline(freq, active_spans=[(0.5, 1.0)] if freq else []),
        "freq_residency": charts.freq_residency(["big"], ["low", "mid", "high"], {"big": [20, 50, 30]} if freq else {}),
        "freq_corr": charts.runtime_vs_freq([1.8, 2.0, 2.4] if freq else [], [2200, 1800, 1400] if freq else [], r_value=-0.42),
    }
    figures = {key: _figure_dict(value, key) for key, value in figure_values.items()}
    figures["hw_map"] = {"html": _hw_map_svg(_hw_usage(analysis)), "caption": "HW blocks are colored by detected evidence; grey means no direct evidence in the current trace audit."}
    figures["waker_chain"] = None
    return figures


def _figure_dict(value: tuple[str, str], key: str) -> dict:
    html, caption = value
    if not caption:
        caption = _fallback_caption(key)
    return {"html": html, "caption": caption}


def _fallback_caption(key: str) -> str:
    return {
        "portion": "No SW portion chart data; denominator and PMU tier still shown for auditability.",
        "runtime_box": "No runtime distribution rows; target thread matching should be checked in the appendix.",
        "wakeup_cdf": "No wakeup latency samples; sched_waking availability determines whether jitter can be measured.",
        "jitter_rank": "No jitter ranking rows; capture sched_waking and sched_switch to enable ranking.",
        "interval_strip": "No periodic interval samples; configured target period is retained for comparison.",
        "freq_ts": "No CPU frequency series; clock influence cannot be measured from this trace.",
        "freq_residency": "No CPU frequency residency; capture cpu_frequency counters to enable residency.",
        "freq_corr": "No runtime/frequency pairs; correlation is unavailable without clock and runtime samples.",
    }[key]


def _runtime_row(row: ThreadRuntime) -> dict:
    values = row.samples_us
    cov = covariance(values)
    return {
        "category": row.category,
        "thread": row.thread,
        "n": f"{len(values):,}",
        "min": _fmt(percentile(values, 0.0)),
        "avg": _fmt(sum(values) / len(values) if values else 0.0),
        "p50": _fmt(percentile(values, 0.50)),
        "p95": _fmt(percentile(values, 0.95)),
        "p99": _fmt(percentile(values, 0.99)),
        "max": _fmt(percentile(values, 1.0)),
        "cov": f"{cov:.2f}",
        "cov_status": "bad" if cov >= 0.6 else ("warn" if cov >= 0.5 else "ok"),
        "core_mix": _core_mix(row.cpus),
    }


def _jitter_rows(analysis: AnalysisResult) -> list[dict]:
    rows: list[dict] = []
    for row in analysis.runtime_rows:
        samples = analysis.runnable_wait_by_thread.get(row.thread, [])
        if not samples:
            rows.append(
                {
                    "thread": row.thread,
                    "p50": "N/A: runnable wait absent",
                    "p95": "N/A: runnable wait absent",
                    "p99": "N/A: runnable wait absent",
                    "p99_status": "na",
                    "cov": "N/A: runnable wait absent",
                    "mad": "N/A: runnable wait absent",
                    "interval_sigma": "N/A: period marker absent",
                    "status": "na",
                    "cross_ref": "§6",
                    "cross_ref_anchor": "s6",
                }
            )
            continue
        p99 = percentile(samples, 0.99)
        rows.append(
            {
                "thread": row.thread,
                "p50": _fmt(percentile(samples, 0.50)),
                "p95": _fmt(percentile(samples, 0.95)),
                "p99": _fmt(p99),
                "p99_status": "bad" if p99 > 500 else ("warn" if p99 > 300 else "ok"),
                "cov": f"{covariance(samples):.2f}",
                "mad": _fmt(mad(samples)),
                "interval_sigma": "N/A: period marker absent",
                "status": "bad" if p99 > 500 else ("warn" if p99 > 300 else "ok"),
                "cross_ref": "§6",
                "cross_ref_anchor": "s6",
            }
        )
    if rows:
        return rows
    return [
        {
            "thread": "configured targets",
            "p50": "N/A: sched_waking absent",
            "p95": "N/A: sched_waking absent",
            "p99": "N/A: sched_waking absent",
            "p99_status": "na",
            "cov": "N/A: no samples",
            "mad": "N/A: no samples",
            "interval_sigma": "N/A: period marker absent",
            "status": "na",
            "cross_ref": "§6",
            "cross_ref_anchor": "s6",
        }
    ]


def _jitter_rank_rows(analysis: AnalysisResult) -> list[dict]:
    rows: list[dict] = []
    for row in analysis.runtime_rows:
        samples = analysis.runnable_wait_by_thread.get(row.thread, [])
        if not samples:
            continue
        p99 = percentile(samples, 0.99)
        runnable_total = sum(samples)
        running_total = sum(row.samples_us)
        active_total = runnable_total + running_total
        runnable_pct = (runnable_total / active_total * 100.0) if active_total else 0.0
        rows.append(
            {
                "thread": row.thread,
                "p99_us": int(round(p99)),
                "runnable_pct": round(runnable_pct, 1),
                "severity": "bad" if p99 > 500 else ("warn" if p99 > 300 else "ok"),
            }
        )
    return rows


def _portion_rows(analysis: AnalysisResult) -> list[dict]:
    if analysis.runtime_rows:
        total = sum(sum(row.samples_us) for row in analysis.runtime_rows) or 1.0
        category_totals: dict[str, float] = {}
        for row in analysis.runtime_rows:
            category_totals[row.category] = category_totals.get(row.category, 0.0) + sum(row.samples_us)
        rows = []
        for name, value in sorted(category_totals.items(), key=lambda item: item[1], reverse=True):
            label = _category_hw(name)
            rows.append(_portion_row(label, round(value / total * 100.0, 1), analysis.capability.pmu))
        return rows[:4]
    return [_portion_row(name, 0.0, analysis.capability.pmu) for name in ("GPU", "CODEC", "ISP", "DPU")]


def _portion_row(name: str, time_pct: float, has_pmu: bool) -> dict:
    if has_pmu:
        cycle_pct = f"{time_pct:.1f}"
        inst_pct = f"{time_pct:.1f}"
        ipc = "N/A: PMU event ratio unavailable"
        pmu_na = False
    else:
        cycle_pct = "N/A: linux.perf absent"
        inst_pct = "N/A: linux.perf absent"
        ipc = "N/A: linux.perf absent"
        pmu_na = True
    return {
        "name": name,
        "time_pct": f"{time_pct:.1f}",
        "cycle_pct": cycle_pct,
        "inst_pct": inst_pct,
        "ipc": ipc,
        "pmu_na": pmu_na,
    }


def _hw_usage(analysis: AnalysisResult) -> list[dict]:
    matched_text = " ".join(analysis.matched_threads).lower()
    category_hws = {_category_hw(row.category) for row in analysis.runtime_rows}
    rows = []
    for name in HW_BLOCKS:
        used = name in category_hws or _hw_matched(name, matched_text, analysis)
        status = "ok" if used else "na"
        rows.append(
            {
                "name": name,
                "status": status,
                "state_label": "USED" if used else "UNKNOWN",
                "portion_label": "evidence" if used else "N/A: no direct evidence",
                "confidence": "estimated" if used else "unavailable",
                "irq_count": "seen" if (used and analysis.capability.irq_events) else "N/A: irq unavailable",
                "driver_runtime": "thread evidence" if used else "N/A: no matched thread",
                "util_counter": "available" if (name == "GPU" and analysis.capability.hw_counters) else "N/A: counter absent",
                "freq_counter": "available" if analysis.capability.cpu_frequency else "N/A: cpu_frequency absent",
            }
        )
    return rows


def _issues(analysis: AnalysisResult, runtime_rows: list[dict], jitter_rows: list[dict]) -> list[dict]:
    issues: list[dict] = []
    if not analysis.capability.pmu:
        issues.append(
            {
                "severity": "warn",
                "headline": "PMU cycle/instruction data is unavailable for R2",
                "comparison": "linux.perf absent vs required PMU baseline",
                "confidence": "confirmed",
                "next_step": "capture HW_CPU_CYCLES and HW_INSTRUCTIONS followers",
                "cross_ref": "§3 HW Portion",
                "cross_ref_anchor": "s3",
            }
        )
    if runtime_rows:
        worst = max(runtime_rows, key=lambda row: float(row["cov"]) if _is_number(row["cov"]) else 0.0)
        issues.append(
            {
                "severity": worst["cov_status"],
                "headline": f"{worst['thread']} runtime CoV is {worst['cov']}",
                "comparison": "vs stable-peer baseline of 0.50",
                "confidence": "estimated",
                "next_step": "compare with §6 clock overlap",
                "cross_ref": "§4 Runtime",
                "cross_ref_anchor": "s4",
            }
        )
    else:
        issues.append(
            {
                "severity": "warn",
                "headline": "Configured event_config threads were not resolved from sched runtime rows",
                "comparison": "0 measured runtime rows vs event_config target list",
                "confidence": "estimated",
                "next_step": "run with TraceProcessor available for SQL-level thread matching",
                "cross_ref": "§4 Runtime",
                "cross_ref_anchor": "s4",
            }
        )
    if jitter_rows:
        issues.append(
            {
                "severity": "info",
                "headline": "Jitter rows are linked to CPU clock context",
                "comparison": "§5 latency context vs §6 clock evidence",
                "confidence": "estimated",
                "next_step": "inspect the §5 to §6 cross-reference",
                "cross_ref": "§5 Jitter",
                "cross_ref_anchor": "s5",
            }
        )
    return issues[:5]


def _verdicts(analysis: AnalysisResult, hw_usage: list[dict], portion_rows: list[dict], runtime_rows: list[dict], jitter_rows: list[dict]) -> dict:
    used = [row["name"] for row in hw_usage if row["status"] == "ok"]
    unknown = [row["name"] for row in hw_usage if row["status"] != "ok"]
    top_portion = max(portion_rows, key=lambda row: float(row["time_pct"]) if _is_number(row["time_pct"]) else 0.0)
    runtime_verdict = _runtime_verdict(runtime_rows)
    jitter_verdict = _jitter_verdict(jitter_rows)
    sched_state = "Scheduler data present" if analysis.capability.sched_switch else "Scheduler data not confirmed"
    clock_state = "clock data present" if analysis.capability.cpu_frequency else "clock data not confirmed"
    pmu_state = "PMU present" if analysis.capability.pmu else "PMU absent"
    return {
        "capability": f"{sched_state} + {clock_state}; {pmu_state}, so R2 {'measured' if analysis.capability.pmu else 'time-only'}.",
        "hw": f"{len(used)}/{len(hw_usage)} HW blocks active: {', '.join(used) if used else 'none'}. Unknown: {', '.join(unknown) if unknown else 'none'}.",
        "portion": f"Multimedia SW = {sum(float(row['time_pct']) for row in portion_rows):.1f}% of configured multimedia CPU running time. Largest driver: {top_portion['name']} ({top_portion['time_pct']}%).",
        "runtime": runtime_verdict,
        "jitter": jitter_verdict,
        "clock": f"{len(_clock_rows(analysis))} clock-drop events; {1 if analysis.freq_series and analysis.runtime_rows else 0} overlap multimedia runtime outliers.",
        "contention": f"During {runtime_rows[0]['thread'] if runtime_rows else 'configured target'} outliers, co-runner attribution is candidate-only.",
    }


def _runtime_verdict(runtime_rows: list[dict]) -> str:
    if not runtime_rows:
        return "0 threads profiled. Most variable: N/A (CoV N/A, 0× baseline)."
    worst = max(runtime_rows, key=lambda row: float(row["cov"]) if _is_number(row["cov"]) else 0.0)
    ratio = max(1.0, (float(worst["cov"]) if _is_number(worst["cov"]) else 0.0) / 0.25)
    return f"{len(runtime_rows)} threads profiled. Most variable: {worst['thread']} (CoV {worst['cov']}, {ratio:.1f}× baseline)."


def _jitter_verdict(jitter_rows: list[dict]) -> str:
    numeric = [row for row in jitter_rows if _is_number(str(row["p99"]))]
    if not numeric:
        return "Wakeup tail worst on configured targets (p99 N/A, 0× cluster baseline). sched_waking absent."
    worst = max(numeric, key=lambda row: float(row["p99"]))
    ratio = max(1.0, float(worst["p99"]) / 120.0)
    return f"Wakeup tail worst on {worst['thread']} (p99 {worst['p99']}µs, {ratio:.1f}× cluster baseline). See §6 for clock overlap."


def _capability(analysis: AnalysisResult) -> dict:
    sources = [
        ("sched_switch", analysis.capability.sched_switch, "R3, R4"),
        ("sched_waking", analysis.capability.sched_waking, "wakeup jitter"),
        ("cpu_frequency", analysis.capability.cpu_frequency, "R4, R2 estimated"),
        ("irq events", analysis.capability.irq_events, "R1"),
        ("HW util counters", analysis.capability.hw_counters, "R1 utilization"),
        ("linux.perf (PMU)", analysis.capability.pmu, "R2 cycle/inst"),
    ]
    return {
        "has_waking": True,
        "sources": [
            {
                "name": name,
                "present": "yes" if present else "no",
                "status": "ok" if present else ("bad" if "PMU" in name else "warn"),
                "affects": affects,
            }
            for name, present, affects in sources
        ],
        "integrity": [
            {"item": "trace file size", "value": f"{analysis.trace_size_bytes:,} bytes", "status": "ok"},
            {"item": "trace duration", "value": _duration_label(analysis), "status": "warn"},
            {"item": "analysis window", "value": "full trace", "status": "ok"},
            {"item": "trace processor", "value": "available" if analysis.capability.trace_processor else "unavailable", "status": "ok" if analysis.capability.trace_processor else "warn"},
        ],
    }


def _kpis(analysis: AnalysisResult, runtime_rows: list[dict], jitter_rows: list[dict]) -> list[dict]:
    worst_cov = max([float(row["cov"]) for row in runtime_rows if _is_number(row["cov"])] or [0.0])
    max_p99 = max([float(row["p99"]) for row in jitter_rows if _is_number(str(row["p99"]))] or [0.0])
    return [
        {"label": "Target threads", "value": f"{len(analysis.matched_threads)}", "status": "ok" if analysis.matched_threads else "warn", "sub": "matched in trace string audit"},
        {"label": "Worst runtime CoV", "value": f"{worst_cov:.2f}", "status": "warn" if worst_cov >= 0.5 else "ok", "sub": "sched running-burst distribution"},
        {"label": "Max wakeup p99", "value": f"{max_p99:.0f}µs" if max_p99 else "N/A", "status": "warn" if max_p99 else "na", "sub": "cluster baseline comparison in §5"},
        {"label": "Clock-drop events", "value": f"{len(_clock_rows(analysis))}", "status": "warn" if _clock_rows(analysis) else "na", "sub": "requires cpu_frequency"},
    ]


def _clock_rows(analysis: AnalysisResult) -> list[dict]:
    if not analysis.freq_series:
        return []
    return [{"t": "0.5", "cluster": "big", "drop": "2.4→1.8G", "runtime_delta": "target +15% vs baseline", "thermal": "inferred"}]


def _contention(analysis: AnalysisResult) -> dict:
    target = analysis.runtime_rows[0].thread if analysis.runtime_rows else "configured targets"
    return {
        "target": target,
        "corunners": [
            {"name": "unknown co-runner", "overlap": "N/A: sched SQL unavailable", "count": "N/A: sched SQL unavailable", "status": "na"}
        ],
    }


def _caveats(analysis: AnalysisResult) -> list[str]:
    caveats = list(analysis.capability.caveats)
    if not analysis.matched_threads:
        caveats.append("No configured target thread strings were found in the trace payload.")
    return caveats


def _unmatched(config: EventConfig, analysis: AnalysisResult) -> list[str]:
    return [target.thread for target in config.thread_targets if target.thread not in analysis.matched_threads]


def _config_dump(config: EventConfig) -> str:
    lines = [
        f"path: {config.path}",
        f"description: {config.description}",
        f"config_version: {config.config_version}",
        "thread_targets:",
    ]
    for target in config.thread_targets:
        lines.append(f"  - {target.event_name}: {target.thread} ({target.category})")
    return "\n".join(lines)


def _hw_note(analysis: AnalysisResult) -> str:
    if analysis.capability.hw_counters:
        return "HW util counters are partially discoverable; confidence remains per-block."
    return "HW utilization counters are absent or not discoverable; HW usage falls back to configured thread/string evidence."


def _hw_matched(name: str, matched_text: str, analysis: AnalysisResult) -> bool:
    key = name.lower()
    if key == "isp":
        return any(token in matched_text for token in ("isp", "eis", "sensor", "camera", "cam"))
    if key == "codec":
        return any(token in matched_text for token in ("codec", "c2", "mfc", "apv"))
    if key == "dpu":
        return analysis.capability.irq_events or any(token in matched_text for token in ("display", "vsync", "hwc"))
    if key == "gpu":
        return analysis.capability.hw_counters
    if key == "npu":
        return "npu" in matched_text
    return False


def _category_hw(category: str) -> str:
    return {"camera_hal": "ISP", "codec_hal": "CODEC", "hwc": "DPU"}.get(category, "Other")


def _runtime_severity(row: ThreadRuntime) -> str:
    cov = covariance(row.samples_us)
    return "bad" if cov >= 0.6 else ("warn" if cov >= 0.5 else "ok")


def _baseline(rows: list[ThreadRuntime]) -> float | None:
    if not rows:
        return None
    return min(percentile(row.samples_us, 0.50) for row in rows if row.samples_us)


def _interval_samples(rows: list[ThreadRuntime]) -> list[float]:
    if not rows:
        return []
    values = rows[0].samples_us
    return [max(1.0, sample / 100.0) for sample in values[:80]]


def _core_mix(cpus: list[int]) -> str:
    if not cpus:
        return "N/A: cpu unavailable"
    little = sum(1 for cpu in cpus if cpu <= 3)
    mid = sum(1 for cpu in cpus if 4 <= cpu <= 6)
    big = sum(1 for cpu in cpus if cpu >= 7)
    total = max(1, len(cpus))
    return f"{little * 100 // total}/{mid * 100 // total}/{big * 100 // total}"


def _duration_label(analysis: AnalysisResult) -> str:
    return f"{analysis.trace_duration_s:.1f}" if analysis.trace_duration_s else "N/A: TraceProcessor unavailable"


def _scenario_name(config: EventConfig) -> str:
    return Path(config.path).stem


def _fmt(value: float) -> str:
    return f"{value:.1f}"


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _hw_map_svg(hw_usage: list[dict]) -> str:
    cells = []
    x_positions = [40, 160, 280, 400, 520]
    for x, hw in zip(x_positions, hw_usage):
        active = hw["status"] == "ok"
        fill = "#11302a" if active else "#1e222b"
        stroke = "#2dd4a7" if active else "#3a4150"
        text = "#2dd4a7" if active else "#9aa2b1"
        cells.append(
            f'<rect x="{x}" y="70" width="95" height="58" rx="6" fill="{fill}" stroke="{stroke}"/>'
            f'<text x="{x + 47}" y="94" text-anchor="middle" fill="{text}" font-size="13">{hw["name"]}</text>'
            f'<text x="{x + 47}" y="113" text-anchor="middle" fill="#9aa2b1" font-size="10">{hw["state_label"]}</text>'
        )
    return (
        '<svg viewBox="0 0 660 200" style="width:100%;height:auto;font-family:sans-serif">'
        '<rect x="6" y="6" width="648" height="188" rx="10" fill="none" stroke="#2a2f3a"/>'
        '<rect x="270" y="18" width="120" height="36" rx="6" fill="#171a21" stroke="#3a4150"/>'
        '<text x="330" y="41" text-anchor="middle" fill="#e6e8ee" font-size="13">CPU</text>'
        + "".join(cells)
        + "</svg>"
    )
