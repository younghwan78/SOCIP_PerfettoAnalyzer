from pathlib import Path

from soc_perfetto_analyzer.analysis import AnalysisResult, CapabilitySummary, ClockRampWindow, FunctionHotspot, PmuCapability, ThreadRuntime
from soc_perfetto_analyzer.config import ClockChangeFilterConfig, EventConfig, ReportFilters, ThreadTarget
from soc_perfetto_analyzer.report.model import _downsample_frequency_series, build_report_model


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
    assert next(kpi for kpi in model["kpis"] if kpi["label"] == "Clock change windows")["value"] == "0"
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


def test_report_model_detects_clock_drop_rows_from_measured_overlap(monkeypatch):
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
        monkeypatch.setattr(charts, name, lambda *args, **kwargs: ("<div>chart</div>", "caption with comparison"))
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
        trace_duration_s=2.0,
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
                samples_us=[100_000.0, 220_000.0, 110_000.0],
                cpus=[7, 7, 7],
                starts_s=[0.25, 0.75, 1.25],
            )
        ],
        freq_series={"big": ([0.0, 0.5, 1.0], [2.4, 1.8, 2.4])},
    )

    model = build_report_model(analysis, config)

    assert model["clock"]["overlap"]["drop_count"] == 1
    assert model["clock"]["throttle_rows"] == [
        {
            "t": "0.750",
            "cluster": "big",
            "drop": "2.4->1.8G",
            "runtime_delta": "target +109.5% vs high-clock median",
            "thermal": "N/A: thermal counter absent",
        }
    ]
    assert next(kpi for kpi in model["kpis"] if kpi["label"] == "Clock change windows")["value"] == "0"


def test_report_model_does_not_flag_low_clock_without_runtime_regression(monkeypatch):
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
        monkeypatch.setattr(charts, name, lambda *args, **kwargs: ("<div>chart</div>", "caption with comparison"))
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
        trace_duration_s=2.0,
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
                samples_us=[100_000.0, 80_000.0, 110_000.0],
                cpus=[7, 7, 7],
                starts_s=[0.25, 0.75, 1.25],
            )
        ],
        freq_series={"big": ([0.0, 0.5, 1.0], [2.4, 1.8, 2.4])},
    )

    model = build_report_model(analysis, config)

    assert model["clock"]["overlap"]["drop_count"] == 0
    assert model["clock"]["throttle_rows"] == []


def test_frequency_downsample_preserves_trace_range_for_chart():
    ts = [idx / 100.0 for idx in range(1_000)]
    values = [1.0 + idx / 1000.0 for idx in range(1_000)]

    result = _downsample_frequency_series({"mid": (ts, values)}, max_points=100)

    out_ts, out_values = result["mid"]
    assert len(out_ts) <= 100
    assert out_ts[0] == ts[0]
    assert out_ts[-1] == ts[-1]
    assert out_values[0] == values[0]
    assert out_values[-1] == values[-1]


def test_report_model_exposes_clock_ramp_attribution_and_corunners(monkeypatch):
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
        monkeypatch.setattr(charts, name, lambda *args, **kwargs: ("<div>chart</div>", "caption with comparison"))
    monkeypatch.setattr(charts, "clock_ramp_attribution", lambda *args, **kwargs: ("<div>ramp</div>", "ramp caption with comparison"))
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
        trace_duration_s=2.0,
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
                samples_us=[100_000.0],
                cpus=[4],
                starts_s=[0.25],
            )
        ],
        clock_ramp_windows=[
            ClockRampWindow(
                cluster="mid",
                start_s=0.10,
                peak_s=0.12,
                end_s=0.20,
                baseline_ghz=1.0,
                peak_ghz=1.4,
                delta_pct=40.0,
                target_runtime_us=200.0,
                non_target_runtime_us=1600.0,
                new_non_target_threads=2,
                target_migrations_into_cluster=0,
                periodicity_score=0.0,
                attribution="added_task_pressure",
                confidence="medium",
                evidence=["non-target runtime 1600.0us vs target 200.0us"],
                top_corunners=[{"thread": "RenderThread", "runtime_us": 900.0}],
            )
        ],
    )

    model = build_report_model(analysis, config)

    assert model["figures"]["clock_ramps"]["html"] == "<div>ramp</div>"
    assert model["clock"]["ramp_rows"][0]["attribution"] == "added_task_pressure"
    assert model["clock"]["ramp_rows"][0]["top_corunner"] == "RenderThread 900.0us"
    assert model["contention"]["corunners"][0]["name"] == "RenderThread"
    assert "clock ramp" in model["verdicts"]["clock"].lower()


def test_large_clock_ramp_set_is_aggregated_for_chart_only(monkeypatch):
    import charts

    calls = {}

    def record(name):
        def _builder(*args, **kwargs):
            calls[name] = {"args": args, "kwargs": kwargs}
            return f"<div>{name}</div>", f"{name} caption with comparison"

        return _builder

    for name in [
        "portion_bar",
        "runtime_box",
        "wakeup_cdf",
        "jitter_rank",
        "interval_strip",
        "freq_timeline",
        "freq_residency",
        "runtime_vs_freq",
        "clock_ramp_attribution",
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
    windows = [
        ClockRampWindow(
            cluster="mid" if idx < 70 else "little",
            start_s=idx * 0.01,
            peak_s=idx * 0.01 + 0.001,
            end_s=idx * 0.01 + 0.002,
            baseline_ghz=1.0,
            peak_ghz=1.2,
            delta_pct=20.0 + (idx % 5),
            target_runtime_us=100.0,
            non_target_runtime_us=500.0,
            new_non_target_threads=1,
            target_migrations_into_cluster=idx % 2,
            periodicity_score=0.5,
            attribution="added_task_pressure" if idx % 2 else "periodic_target_migration",
            confidence="medium",
            evidence=[],
            top_corunners=[],
        )
        for idx in range(100)
    ]
    analysis = AnalysisResult(
        trace_path=Path("sample.pftrace"),
        trace_size_bytes=1024,
        trace_duration_s=2.0,
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
        clock_ramp_windows=windows,
    )

    model = build_report_model(analysis, config)

    chart_rows = calls["clock_ramp_attribution"]["args"][0]
    assert len(model["clock"]["ramp_rows"]) == 100
    assert len(chart_rows) < len(model["clock"]["ramp_rows"])
    assert sum(row["count"] for row in chart_rows) == 100
    assert any("n=" in row["label"] for row in chart_rows)


def test_active_span_polygons_preserve_all_active_ranges():
    import charts

    xs, ys = charts._active_span_polygons([(0.1, 0.2), (0.3, 0.4)], y_min=1.0, y_max=2.0)

    assert xs == [0.1, 0.1, 0.2, 0.2, None, 0.3, 0.3, 0.4, 0.4, None]
    assert ys == [1.0, 2.0, 2.0, 1.0, None, 1.0, 2.0, 2.0, 1.0, None]


def test_report_model_shows_significant_average_relative_clock_windows(monkeypatch):
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
        "clock_ramp_attribution",
    ]:
        monkeypatch.setattr(charts, name, lambda *args, **kwargs: ("<div>chart</div>", "caption with comparison"))
    monkeypatch.setattr(charts, "reset_plotlyjs", lambda: None)

    config = EventConfig(
        path=Path("event_config.json"),
        description="fixture",
        config_version="1.0",
        event_count=1,
        thread_targets=[ThreadTarget(event_name="Camera", thread="CamX_ReqProc", category="camera_hal")],
        raw={"events": []},
        report_filters=ReportFilters(
            clock_change=ClockChangeFilterConfig(
                ramp_delta_pct=20.0,
                drop_delta_pct=20.0,
                min_duration_ms=5.0,
                merge_gap_ms=1.0,
                max_rows=3,
                include_unknown=False,
            )
        ),
    )
    windows = [
        ClockRampWindow(
            cluster="mid",
            start_s=0.020,
            peak_s=0.030,
            end_s=0.040,
            baseline_ghz=1.0,
            peak_ghz=1.6,
            delta_pct=60.0,
            target_runtime_us=400.0,
            non_target_runtime_us=1200.0,
            new_non_target_threads=2,
            target_migrations_into_cluster=0,
            periodicity_score=0.2,
            attribution="added_task_pressure",
            confidence="high",
            evidence=["non-target runtime dominates"],
            top_corunners=[
                {"thread": "swapper", "runtime_us": 2000.0},
                {"thread": "RenderThread", "runtime_us": 900.0},
            ],
        ),
        ClockRampWindow(
            cluster="mid",
            start_s=0.075,
            peak_s=0.080,
            end_s=0.085,
            baseline_ghz=1.0,
            peak_ghz=1.5,
            delta_pct=50.0,
            target_runtime_us=100.0,
            non_target_runtime_us=100.0,
            new_non_target_threads=0,
            target_migrations_into_cluster=0,
            periodicity_score=0.0,
            attribution="unknown",
            confidence="low",
            evidence=[],
            top_corunners=[],
        ),
    ]
    analysis = AnalysisResult(
        trace_path=Path("sample.pftrace"),
        trace_size_bytes=1024,
        trace_duration_s=0.10,
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
        freq_series={
            "mid": (
                [0.00, 0.02, 0.04, 0.06, 0.08, 0.10],
                [1.0, 1.6, 1.0, 0.6, 1.5, 1.0],
            )
        },
        clock_ramp_windows=windows,
    )

    model = build_report_model(analysis, config)

    significant = model["clock"]["significant_rows"]
    assert len(model["clock"]["ramp_rows"]) == 2
    assert len(significant) == 2
    assert {row["direction"] for row in significant} == {"up", "down"}
    up = next(row for row in significant if row["direction"] == "up")
    down = next(row for row in significant if row["direction"] == "down")
    assert up["attribution"] == "added_task_pressure"
    assert up["top_corunner"] == "RenderThread 900.0us"
    assert float(up["delta_vs_avg_pct"]) > 20.0
    assert down["attribution"] == "clock_below_average"
    assert model["clock"]["filter"]["display_count"] == 2
    assert model["clock"]["filter"]["raw_ramp_count"] == 2
    assert model["clock"]["filter"]["max_rows"] == 3
    assert "significant average-relative" in model["verdicts"]["clock"]


def test_report_model_exposes_pmu_function_bottlenecks(monkeypatch):
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
        "clock_ramp_attribution",
    ]:
        monkeypatch.setattr(charts, name, lambda *args, **kwargs: ("<div>chart</div>", "caption with comparison"))
    monkeypatch.setattr(charts, "reset_plotlyjs", lambda: None)

    config = EventConfig(
        path=Path("event_config.json"),
        description="fixture",
        config_version="1.0",
        event_count=1,
        thread_targets=[ThreadTarget(event_name="Camera", thread="CamX_ReqProc", category="camera_hal")],
        raw={"events": []},
    )
    hotspot = FunctionHotspot(
        thread="CamX_ReqProc",
        function="ProcessFrame",
        mapping="libcamera.so",
        source_file=None,
        line_number=None,
        self_cycles=None,
        cumulative_cycles=None,
        self_samples=50,
        cumulative_samples=50,
        sample_pct=47.6,
        ipc=None,
        cache_miss_pct=None,
        frontend_stall_pct=None,
        backend_stall_pct=None,
        wait_pct=None,
        classification="unknown",
        confidence="low",
        reason="N/A: classifier PMU events unavailable; top function is sample-based only.",
    )
    analysis = AnalysisResult(
        trace_path=Path("sample.pftrace"),
        trace_size_bytes=1024,
        trace_duration_s=2.0,
        capability=CapabilitySummary(
            sched_switch=True,
            sched_waking=False,
            cpu_frequency=True,
            irq_events=False,
            hw_counters=False,
            pmu=True,
            trace_processor=True,
            caveats=[],
        ),
        matched_threads=["CamX_ReqProc"],
        runtime_rows=[ThreadRuntime(category="camera_hal", thread="CamX_ReqProc", samples_us=[1000.0], cpus=[4])],
        pmu_capability=PmuCapability(
            has_perf_samples=True,
            has_callstacks=True,
            has_cycles=True,
            has_instructions=False,
            caveats=[],
        ),
        function_hotspots_by_thread={"CamX_ReqProc": [hotspot]},
    )

    model = build_report_model(analysis, config)

    assert model["pmu"]["tier"] == "measured"
    assert model["pmu"]["function_bottlenecks"][0]["function"] == "ProcessFrame"
    assert model["pmu"]["function_bottlenecks"][0]["rank"] == 1
    assert model["pmu"]["function_bottlenecks"][0]["classification"] == "unknown"
