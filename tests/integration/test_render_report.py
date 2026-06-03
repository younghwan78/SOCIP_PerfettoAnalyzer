from pathlib import Path

from soc_perfetto_analyzer.analysis import analyze_trace
from soc_perfetto_analyzer.config import load_event_config
from soc_perfetto_analyzer.report.html import render_report
from soc_perfetto_analyzer.report.model import build_report_model


def test_render_report_preserves_sample_structure_and_single_file(tmp_path):
    config = load_event_config(Path("event_config.json"))
    analysis = analyze_trace(Path("android-perfetto-FHD30-S24U.pftrace"), config)
    model = build_report_model(analysis, config)
    out = tmp_path / "report.html"

    render_report(model, out)
    html = out.read_text(encoding="utf-8")

    assert out.exists()
    for section in ["s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"]:
        assert f'id="{section}"' in html or f"#{section}" in html
    assert html.count('class="card badge"') == 5
    assert "tier-time_only" in html
    assert "N/A: linux.perf absent" in html
    assert "plotly" in html.lower() or "no data:" in html
    assert html.count('class="cap"') >= 7
    assert "Clock-drop events" in html
    assert "runtime/frequency overlap samples measured" in html
    assert "N/A: runtime/frequency overlap not implemented" not in html
    assert "target +15% vs baseline" not in html
    assert "r = -0.42" not in html
