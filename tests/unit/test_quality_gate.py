from pathlib import Path

from soc_perfetto_analyzer.analysis import analyze_trace
from soc_perfetto_analyzer.config import load_event_config
from soc_perfetto_analyzer.quality_gate import evaluate_phase9_quality_gate
from soc_perfetto_analyzer.report.model import build_report_model


def test_phase9_quality_gate_passes_for_sample_trace_and_event_config():
    config = load_event_config(Path("event_config.json"))
    analysis = analyze_trace(Path("android-perfetto-FHD30-S24U.pftrace"), config)
    model = build_report_model(analysis, config)

    result = evaluate_phase9_quality_gate(model)

    assert result.passed, result.failures
    assert len(result.checks) >= 9
