from pathlib import Path

from soc_perfetto_analyzer.analysis import analyze_trace
from soc_perfetto_analyzer.config import load_event_config


def test_analyze_trace_uses_trace_processor_sql_for_sample_trace():
    config = load_event_config(Path("event_config.yaml"))

    analysis = analyze_trace(Path("android-perfetto-FHD30-S24U.pftrace"), config)

    assert analysis.capability.trace_processor is True
    assert analysis.capability.sched_switch is True
    assert analysis.capability.cpu_frequency is True
    assert analysis.capability.pmu is False
    assert analysis.trace_duration_s is not None
    assert 9.9 < analysis.trace_duration_s < 10.1
    assert analysis.matched_threads == ["Uni:PERSONAL_IM"]
    assert len(analysis.runtime_rows) == 1
    assert analysis.runtime_rows[0].thread == "Uni:PERSONAL_IM"
    assert len(analysis.runtime_rows[0].samples_us) == 229
    assert len(analysis.runtime_rows[0].starts_s) == 229
    assert max(analysis.runtime_rows[0].samples_us) > 1000.0
    assert analysis.wakeup_samples_by_cluster
    assert analysis.freq_series
