import json
from pathlib import Path

from soc_perfetto_analyzer.cli import main


def test_analyze_writes_output_contract_bundle_without_chart_html(tmp_path):
    out_dir = tmp_path / "job"

    rc = main(
        [
            "analyze",
            "--trace",
            "android-perfetto-FHD30-S24U.pftrace",
            "--event-config",
            "event_config.yaml",
            "--out",
            str(out_dir),
        ]
    )

    assert rc == 0
    expected = [
        "report.html",
        "report.json",
        "trace_inventory.json",
        "capability.json",
        "metrics/hardware_usage.json",
        "metrics/sw_portion.json",
        "metrics/thread_runtime.json",
        "metrics/thread_function_bottlenecks.json",
        "metrics/wakeup_jitter.json",
        "metrics/periods.json",
        "metrics/cpu_clock.json",
        "metrics/cluster_clock_attribution.json",
        "metrics/contention.json",
        "appendix/matched_threads.csv",
        "appendix/unmatched_patterns.csv",
        "appendix/ambiguous_matches.csv",
        "appendix/sql_queries.txt",
        "quality_gate.json",
    ]
    for rel in expected:
        assert (out_dir / rel).exists(), rel

    report_json = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert set(report_json) >= {
        "metadata",
        "capability",
        "integrity",
        "hardware_usage",
        "sw_portion",
        "thread_runtime",
        "thread_function_bottlenecks",
        "wakeup_jitter",
        "periods",
        "cpu_clock",
        "cluster_clock_attribution",
        "contention",
        "issues",
        "caveats",
        "appendix",
    }
    assert report_json["metadata"]["trace_duration_ms"] is not None
    assert report_json["metadata"]["trace_processor_version"] != "unavailable"
    assert report_json["cluster_clock_attribution"] == report_json["cpu_clock"]["ramp_rows"]
    assert report_json["cpu_clock"]["significant_rows"]
    assert len(report_json["cpu_clock"]["significant_rows"]) <= report_json["cpu_clock"]["filter"]["max_rows"]
    assert len(report_json["cpu_clock"]["significant_rows"]) < len(report_json["cluster_clock_attribution"])
    assert report_json["cpu_clock"]["filter"]["raw_ramp_count"] == len(report_json["cluster_clock_attribution"])
    assert report_json["thread_function_bottlenecks"] == []
    assert any("perf callstack samples absent" in caveat for caveat in report_json["caveats"])
    assert max(float(row["t"]) for row in report_json["cluster_clock_attribution"]) > 10.0
    isp = next(row for row in report_json["hardware_usage"] if row["name"] == "ISP")
    assert isp["state_label"] == "USED"
    jitter = report_json["wakeup_jitter"][0]
    assert jitter["thread"] == "Uni:PERSONAL_IM"
    assert float(jitter["p99"]) > 6000.0
    assert jitter["status"] == "bad"
    raw_report = (out_dir / "report.json").read_text(encoding="utf-8")
    assert "Plotly.newPlot" not in raw_report
    assert "<div" not in raw_report
    report_html = (out_dir / "report.html").read_text(encoding="utf-8")
    assert "Significant clock change windows" in report_html
    assert "Raw clock ramp attribution" in report_html

    gate = json.loads((out_dir / "quality_gate.json").read_text(encoding="utf-8"))
    assert gate["passed"] is True
    assert "rendered report.html has nav §0-§8 and five HW badges" in gate["checks"]
