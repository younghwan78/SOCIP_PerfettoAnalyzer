import sys
from pathlib import Path
from types import ModuleType

from soc_perfetto_analyzer import analysis as analysis_module
from soc_perfetto_analyzer.analysis import analyze_trace
from soc_perfetto_analyzer.config import EventConfig, ThreadTarget


def _config() -> EventConfig:
    return EventConfig(
        path=Path("event_config.json"),
        description="fixture",
        config_version="1.0",
        event_count=1,
        thread_targets=[ThreadTarget(event_name="Task", thread="TargetThread", category="camera_hal")],
        raw={"events": []},
    )


def test_string_inventory_fallback_does_not_synthesize_performance_metrics(tmp_path, monkeypatch):
    trace = tmp_path / "fallback.pftrace"
    trace.write_bytes(b"TargetThread sched_switch sched_waking cpu_frequency linux.perf")
    config = _config()
    monkeypatch.setattr(analysis_module, "_try_trace_processor_analysis", lambda trace_path, event_config: None)

    result = analyze_trace(trace, config)

    assert result.matched_threads == ["TargetThread"]
    assert result.raw_string_hits == {"TargetThread": 1}
    assert result.runtime_rows == []
    assert result.wakeup_samples_by_cluster == {}
    assert result.runnable_wait_by_thread == {}
    assert result.freq_series == {}
    assert result.capability.trace_processor is False


def test_trace_processor_runtime_failure_is_reported_in_fallback_caveats(tmp_path, monkeypatch):
    trace = tmp_path / "broken-tp.pftrace"
    trace.write_bytes(b"TargetThread sched_switch")
    config = _config()

    perfetto_module = ModuleType("perfetto")
    perfetto_module.__path__ = []
    trace_processor_module = ModuleType("perfetto.trace_processor")

    class BrokenTraceProcessor:
        def __init__(self, trace):
            self.trace = trace

        def query(self, sql):
            raise RuntimeError("trace processor SQL failed")

        def close(self):
            pass

    trace_processor_module.TraceProcessor = BrokenTraceProcessor
    perfetto_module.trace_processor = trace_processor_module
    monkeypatch.setitem(sys.modules, "perfetto", perfetto_module)
    monkeypatch.setitem(sys.modules, "perfetto.trace_processor", trace_processor_module)

    result = analyze_trace(trace, config)

    assert result.capability.trace_processor is False
    assert any("TraceProcessor analysis failed: trace processor SQL failed" in caveat for caveat in result.capability.caveats)
    assert not any("TraceProcessor unavailable" in caveat for caveat in result.capability.caveats)
