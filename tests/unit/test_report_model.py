from pathlib import Path

from soc_perfetto_analyzer.analysis import AnalysisResult, CapabilitySummary, ThreadRuntime
from soc_perfetto_analyzer.config import EventConfig, ThreadTarget
from soc_perfetto_analyzer.report.model import build_report_model


def test_report_model_builds_verdicts_issues_and_uses_chart_builders(monkeypatch):
    calls = []

    def record(name, value):
        def _builder(*args, **kwargs):
            calls.append((name, args, kwargs))
            return f"<div>{name}</div>", f"{name} caption with comparison"
        return _builder

    import charts

    for name in [
        "portion_bar",
        "runtime_box",
        "wakeup_cdf",
        "jitter_rank",
        "interval_strip",
        "freq_timeline",
        "freq_residency",
        "runtime_vs_freq",
    ]:
        monkeypatch.setattr(charts, name, record(name, None))
    monkeypatch.setattr(charts, "reset_plotlyjs", lambda: calls.append(("reset_plotlyjs", (), {})))

    config = EventConfig(
        path=Path("event_config.json"),
        description="fixture",
        config_version="1.0",
        event_count=1,
        thread_targets=[ThreadTarget(event_name="Camera", thread="CamX_ReqProc", category="camera_hal")],
        raw={"events": []},
    )
    analysis = AnalysisResult(
        trace_path=Path("android-perfetto-FHD30-S24U.pftrace"),
        trace_size_bytes=1024,
        trace_duration_s=12.0,
        capability=CapabilitySummary(
            sched_switch=True,
            sched_waking=True,
            cpu_frequency=True,
            irq_events=False,
            hw_counters=False,
            pmu=False,
            trace_processor=True,
            caveats=[],
        ),
        matched_threads=["CamX_ReqProc"],
        runtime_rows=[
            ThreadRuntime(
                category="camera_hal",
                thread="CamX_ReqProc",
                samples_us=[1000.0, 1500.0, 2500.0],
                cpus=[0, 4, 7],
            )
        ],
        wakeup_samples_by_cluster={"big": [50.0, 100.0, 600.0]},
        freq_series={"big": ([0.0, 1.0], [2.4, 1.8])},
    )

    model = build_report_model(analysis, config)

    assert set(model["verdicts"]) >= {"hw", "portion", "runtime", "jitter", "clock", "contention"}
    assert model["top_issues"]
    assert all(issue["headline"] for issue in model["top_issues"])
    assert any(issue.get("comparison") or issue.get("next_step") for issue in model["top_issues"])
    assert model["portion"]["tier"] == "time_only"
    assert all("N/A:" in row["cycle_pct"] for row in model["portion"]["rows"])
    assert any(row["cross_ref_anchor"] == "s6" for row in model["jitter"]["rows"])
    assert ("reset_plotlyjs", (), {}) in calls
    assert {name for name, _, _ in calls} >= {
        "portion_bar",
        "runtime_box",
        "wakeup_cdf",
        "jitter_rank",
        "interval_strip",
        "freq_timeline",
        "freq_residency",
        "runtime_vs_freq",
    }


def test_report_model_does_not_fabricate_clock_or_frequency_metrics(monkeypatch):
    calls = {}

    def record(name):
        def _builder(*args, **kwargs):
            calls[name] = {"args": args, "kwargs": kwargs}
            return f"<div>{name}</div>", f"{name} caption with comparison"
        return _builder

    import charts

    for name in [
        "portion_bar",
        "runtime_box",
        "wakeup_cdf",
        "jitter_rank",
        "interval_strip",
        "freq_timeline",
        "freq_residency",
        "runtime_vs_freq",
    ]:
        monkeypatch.setattr(charts, name, record(name))
    monkeypatch.setattr(charts, "reset_plotlyjs", lambda: None)

    config = EventConfig(
        path=Path("event_config.json"),
        description="fixture",
        config_version="1.0",
        event_count=1,
        thread_targets=[ThreadTarget(event_name="Camera", thread="CamX_ReqProc", category="camera_hal")],
        raw={"events": []},
    )
    analysis = AnalysisResult(
        trace_path=Path("sample.pftrace"),
        trace_size_bytes=1024,
        trace_duration_s=1.0,
        capability=CapabilitySummary(
            sched_switch=True,
            sched_waking=False,
            cpu_frequency=True,
            irq_events=False,
            hw_counters=False,
            pmu=False,
            trace_processor=True,
            caveats=[],
        ),
        matched_threads=["CamX_ReqProc"],
        runtime_rows=[
            ThreadRuntime(
                category="camera_hal",
                thread="CamX_ReqProc",
                samples_us=[1000.0, 1500.0, 2500.0],
                cpus=[0, 4, 7],
            )
        ],
        freq_series={"big": ([0.0, 1.0], [2.4, 1.8])},
    )

    model = build_report_model(analysis, config)

    assert calls["freq_timeline"]["kwargs"]["active_spans"] == []
    assert calls["freq_residency"]["args"] == ([], [], {})
    assert calls["runtime_vs_freq"]["args"] == ([], [], 0.0)
    assert model["clock"]["throttle_rows"] == []
    assert next(kpi for kpi in model["kpis"] if kpi["label"] == "Clock-drop events")["value"] == "0"
    assert model["capability"]["has_waking"] is False


def test_report_model_uses_measured_runtime_frequency_overlap(monkeypatch):
    calls = {}

    def record(name):
        def _builder(*args, **kwargs):
            calls[name] = {"args": args, "kwargs": kwargs}
            return f"<div>{name}</div>", f"{name} caption with comparison"
        return _builder

    import charts

    for name in [
        "portion_bar",
        "runtime_box",
        "wakeup_cdf",
        "jitter_rank",
        "interval_strip",
        "freq_timeline",
        "freq_residency",
        "runtime_vs_freq",
    ]:
        monkeypatch.setattr(charts, name, record(name))
    monkeypatch.setattr(charts, "reset_plotlyjs", lambda: None)

    config = EventConfig(
        path=Path("event_config.json"),
        description="fixture",
        config_version="1.0",
        event_count=1,
        thread_targets=[ThreadTarget(event_name="Camera", thread="CamX_ReqProc", category="camera_hal")],
        raw={"events": []},
    )
    analysis = AnalysisResult(
        trace_path=Path("sample.pftrace"),
        trace_size_bytes=1024,
        trace_duration_s=1.0,
        capability=CapabilitySummary(
            sched_switch=True,
            sched_waking=False,
            cpu_frequency=True,
            irq_events=False,
            hw_counters=False,
            pmu=False,
            trace_processor=True,
            caveats=[],
        ),
        matched_threads=["CamX_ReqProc"],
        runtime_rows=[
            ThreadRuntime(
                category="camera_hal",
                thread="CamX_ReqProc",
                samples_us=[100_000.0, 200_000.0],
                cpus=[7, 7],
                starts_s=[0.25, 0.75],
            )
        ],
        freq_series={"big": ([0.0, 0.5, 1.0], [2.4, 1.8, 2.0])},
    )

    model = build_report_model(analysis, config)

    assert calls["freq_timeline"]["kwargs"]["active_spans"] == [(0.25, 0.35), (0.75, 0.95)]
    assert calls["freq_residency"]["args"] == (["big"], ["low", "mid", "high"], {"big": [0.0, 66.7, 33.3]})
    assert calls["runtime_vs_freq"]["args"] == ([2.4, 1.8], [100_000.0, 200_000.0], -1.0)
    assert model["clock"]["overlap"]["sample_count"] == 2
    assert model["clock"]["overlap"]["measurement_state"] == "measured"
