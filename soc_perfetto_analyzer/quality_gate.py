from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GateResult:
    passed: bool
    checks: list[str]
    failures: list[str]


def evaluate_phase9_quality_gate(model: dict[str, Any], html_path: Path | str | None = None) -> GateResult:
    checks: list[str] = []
    failures: list[str] = []

    _check_verdicts(model, checks, failures)
    _check_issue_objects(model, checks, failures)
    _check_comparisons(model, checks, failures)
    _check_captions(model, checks, failures)
    _check_na_reasons(model, checks, failures)
    _check_cross_ref(model, checks, failures)
    _check_pmu_absent(model, checks, failures)
    _check_severity_values(model, checks, failures)
    _check_nav_contract(model, checks, failures)
    _check_hw_badges(model, checks, failures)
    _check_chart_contract(model, checks, failures)
    _check_no_fabricated_metrics(model, checks, failures)
    if html_path is not None:
        _check_rendered_html(Path(html_path), checks, failures)

    return GateResult(passed=not failures, checks=checks, failures=failures)


def _check_verdicts(model: dict[str, Any], checks: list[str], failures: list[str]) -> None:
    required = ["hw", "portion", "runtime", "jitter", "clock", "contention"]
    verdicts = model.get("verdicts", {})
    missing = [name for name in required if not str(verdicts.get(name, "")).strip()]
    _record("sections §2-§7 have verdicts", not missing, checks, failures, f"missing verdicts: {missing}")


def _check_issue_objects(model: dict[str, Any], checks: list[str], failures: list[str]) -> None:
    issues = model.get("top_issues") or []
    ok = bool(issues) and all(issue.get("headline") and (issue.get("comparison") or issue.get("next_step")) for issue in issues)
    _record("dashboard top issues use issue object schema", ok, checks, failures, "issue missing headline or comparison/next_step")


def _check_comparisons(model: dict[str, Any], checks: list[str], failures: list[str]) -> None:
    texts = list(_walk_strings(model.get("top_issues", [])))
    texts.extend(str(value) for value in model.get("verdicts", {}).values())
    pattern = re.compile(r"(×|vs|exceeds|of )")
    ok = any(pattern.search(text) for text in texts)
    _record("highlight numbers include comparison text", ok, checks, failures, "no comparison marker found")


def _check_captions(model: dict[str, Any], checks: list[str], failures: list[str]) -> None:
    figures = [fig for fig in (model.get("figures") or {}).values() if fig]
    ok = bool(figures) and all(str(fig.get("caption", "")).strip() for fig in figures)
    _record("all charts have captions", ok, checks, failures, "empty chart caption")


def _check_na_reasons(model: dict[str, Any], checks: list[str], failures: list[str]) -> None:
    bad = [text for text in _walk_strings(model) if text.strip() == "N/A"]
    _record("all N/A strings include reasons", not bad, checks, failures, f"bare N/A values: {bad[:5]}")


def _check_cross_ref(model: dict[str, Any], checks: list[str], failures: list[str]) -> None:
    strings = list(_walk_strings(model.get("jitter", {}))) + list(_walk_strings(model.get("top_issues", [])))
    ok = "s6" in strings or any("#s6" in text or "§6" in text for text in strings)
    _record("§5 to §6 cross-reference exists", ok, checks, failures, "missing s6 cross-reference")


def _check_pmu_absent(model: dict[str, Any], checks: list[str], failures: list[str]) -> None:
    portion = model.get("portion", {})
    if portion.get("tier") != "time_only":
        _record("PMU present path does not require N/A cycle/inst", True, checks, failures, "")
        return
    rows = portion.get("rows") or []
    ok = all(not _is_number(str(row.get("cycle_pct"))) and not _is_number(str(row.get("inst_pct"))) for row in rows)
    ok = ok and portion.get("tier_label")
    _record("PMU absent keeps cycle/inst non-numeric and tiered", ok, checks, failures, "numeric cycle/inst in time-only tier")


def _check_severity_values(model: dict[str, Any], checks: list[str], failures: list[str]) -> None:
    allowed = {"ok", "warn", "bad", "na", "info"}
    severity_like = []
    for key, value in _walk_items(model):
        if key in {"status", "severity", "p99_status", "cov_status"}:
            severity_like.append(str(value))
    bad = [value for value in severity_like if value and value not in allowed]
    _record("severity values use the template color vocabulary", not bad, checks, failures, f"unknown severities: {bad[:5]}")


def _check_nav_contract(model: dict[str, Any], checks: list[str], failures: list[str]) -> None:
    ok = bool(model.get("contention")) and bool(model.get("appendix"))
    _record("report has data for nav §0-§8", ok, checks, failures, "contention or appendix missing")


def _check_hw_badges(model: dict[str, Any], checks: list[str], failures: list[str]) -> None:
    names = [row.get("name") for row in model.get("hw_usage", [])]
    ok = names == ["GPU", "DPU", "CODEC", "ISP", "NPU"]
    _record("HW badge list has five fixed blocks", ok, checks, failures, f"HW badges were {names}")


def _check_chart_contract(model: dict[str, Any], checks: list[str], failures: list[str]) -> None:
    required = {"portion", "runtime_box", "wakeup_cdf", "jitter_rank", "interval_strip", "freq_ts", "freq_residency", "freq_corr", "hw_map"}
    present = {key for key, value in (model.get("figures") or {}).items() if value}
    missing = sorted(required - present)
    _record("all required chart slots are populated by report model", not missing, checks, failures, f"missing figures: {missing}")


def _check_no_fabricated_metrics(model: dict[str, Any], checks: list[str], failures: list[str]) -> None:
    clock_rows = model.get("clock", {}).get("throttle_rows") or []
    bad_clock_rows = []
    for row in clock_rows:
        values = [str(value) for value in row.values()]
        has_known_placeholder = any("target +15%" in value for value in values)
        has_demo_drop = any(("2.4" in value and "1.8" in value) for value in values)
        if str(row.get("thermal", "")).lower() == "inferred" and (has_known_placeholder or has_demo_drop):
            bad_clock_rows.append(row)
    bad_markers = [
        marker
        for marker in ("target +15% vs baseline", "r = -0.42")
        if any(marker in text for text in _walk_strings(model))
    ]
    ok = not bad_clock_rows and not bad_markers
    detail = f"clock_rows={len(bad_clock_rows)}, markers={bad_markers[:3]}"
    _record("fabricated clock/frequency metrics are absent", ok, checks, failures, detail)


def _check_rendered_html(path: Path, checks: list[str], failures: list[str]) -> None:
    if not path.exists():
        _record("rendered report.html has nav §0-§8 and five HW badges", False, checks, failures, f"{path} does not exist")
        return
    html = path.read_text(encoding="utf-8")
    nav_ok = all(f'href="#s{idx}"' in html for idx in range(9))
    badges_ok = html.count('class="card badge"') == 5
    external_assets = re.findall(r'<(?:script|link)[^>]+(?:src|href)="https?://', html)
    single_file_ok = path.name == "report.html" and not external_assets
    ok = nav_ok and badges_ok and single_file_ok
    failure = f"nav_ok={nav_ok}, badges_ok={badges_ok}, single_file_ok={single_file_ok}"
    _record("rendered report.html has nav §0-§8 and five HW badges", ok, checks, failures, failure)


def _record(name: str, ok: bool, checks: list[str], failures: list[str], failure: str) -> None:
    checks.append(name)
    if not ok:
        failures.append(f"{name}: {failure}")


def _walk_strings(value: Any):
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)
    elif isinstance(value, str):
        yield value


def _walk_items(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk_items(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_items(child)


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False
