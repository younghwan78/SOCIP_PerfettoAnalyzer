# Clock Change Filter Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the CPU clock section readable by showing significant average-relative clock increase/decrease windows by default while preserving full raw clock metrics.

**Architecture:** Parse `report_filters.clock_change` from `event_config.yaml` into a typed config object. Build full raw ramp/drop rows as before, then derive display rows from cluster-baseline-filtered windows for HTML and chart inputs. Keep JSON raw metric compatibility and add display/filter metadata under `cpu_clock`.

**Tech Stack:** Python dataclasses, pytest, Jinja2 template rendering, existing Plotly chart helpers.

---

### Task 1: Add Typed Clock Change Filter Config

**Files:**
- Modify: `soc_perfetto_analyzer/config.py`
- Test: `tests/unit/test_event_config.py`

- [ ] **Step 1: Write failing tests**

Add a test that loads `report_filters.clock_change` and verifies defaults plus explicit values:

```python
config = load_event_config(config_path)
assert config.report_filters.clock_change.ramp_delta_pct == 15.0
assert config.report_filters.clock_change.drop_delta_pct == 20.0
assert config.report_filters.clock_change.max_rows == 12
```

- [ ] **Step 2: Run the new test**

Run: `uv run python -m pytest tests\unit\test_event_config.py -q`

- [ ] **Step 3: Implement dataclasses and extraction**

Add `ClockChangeFilterConfig`, `ReportFilters`, and `_extract_report_filters()` using safe numeric/bool conversion.

- [ ] **Step 4: Verify**

Run: `uv run python -m pytest tests\unit\test_event_config.py -q`

### Task 2: Derive Significant Clock Display Rows

**Files:**
- Modify: `soc_perfetto_analyzer/report/model.py`
- Test: `tests/unit/test_report_model.py`

- [ ] **Step 1: Write failing tests**

Add tests proving:
- raw `clock.ramp_rows` remains full length
- `clock.significant_rows` only includes average-relative up/down rows
- `clock.filter` exposes thresholds and raw/display counts

- [ ] **Step 2: Run the targeted tests**

Run: `uv run python -m pytest tests\unit\test_report_model.py::<new_test> -q`

- [ ] **Step 3: Implement filtered row builder**

Compute cluster average from `analysis.freq_series`, filter ramp rows by `delta_pct_float >= ramp_delta_pct`, filter drop rows by parsed drop percentage, sort by severity, and cap to `max_rows`.

- [ ] **Step 4: Verify**

Run: `uv run python -m pytest tests\unit\test_report_model.py -q`

### Task 3: Update Report HTML Defaults

**Files:**
- Modify: `template.html.j2`
- Test: `tests/integration/test_cli_bundle.py`

- [ ] **Step 1: Write failing integration assertion**

Assert `report.json["cpu_clock"]["significant_rows"]` exists and has fewer rows than raw attribution rows for the sample trace.

- [ ] **Step 2: Update template**

Render `Significant clock change windows` open by default. Move raw `Clock ramp attribution` and raw `Clock-drop events` into closed details.

- [ ] **Step 3: Verify CLI bundle**

Run: `uv run python -m pytest tests\integration\test_cli_bundle.py -q`

### Task 4: Regenerate Report and Full Verification

**Files:**
- Output: `out/latest-cluster-pmu-report/report.html`
- Output: `out/latest-cluster-pmu-report/report.json`

- [ ] **Step 1: Run full test suite**

Run: `uv run python -m pytest -q`

- [ ] **Step 2: Generate report**

Run: `uv run python -m soc_perfetto_analyzer.cli analyze --trace android-perfetto-FHD30-S24U.pftrace --event-config event_config.yaml --out out\latest-cluster-pmu-report`

- [ ] **Step 3: Inspect report JSON**

Check `quality_gate.passed`, `cpu_clock.significant_rows`, raw row counts, and full clock range preservation.
