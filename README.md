# SOCIP Perfetto Analyzer

Perfetto trace analyzer for SoC multimedia scenarios. The current implementation focuses on the Phase 9 v2 report contract from `soc_perfetto_analyzer_integrated_plan_v2.md`: it reads a Perfetto trace and an event configuration, computes scheduler/runtime-oriented metrics, and renders a self-contained dark HTML report using the provided `template.html.j2` and `charts.py`.

## What This Does

- Loads `event_config.yaml` or `event_config.json`.
- Opens a `.pftrace` file through the Perfetto Python TraceProcessor API.
- Resolves configured target threads by exact thread name.
- Computes:
  - thread running-burst distribution from `sched`
  - runnable wait / scheduling jitter from `thread_state` `R` and `R+`
  - CPU frequency timeline from `cpu_frequency` counters
  - configurable CPU cluster topology from `event_config.yaml`
  - clock ramp attribution from scheduler/frequency overlap
  - PMU availability, PMU-missing caveats, and function bottleneck rows when linux.perf callstacks exist
  - HW block status from resolved target categories
- Renders a single-file `report.html`.
- Emits machine-readable `report.json` plus `metrics/` and `appendix/` outputs.
- Runs an automatic Phase 9 Quality Gate.

The report chart format is intentionally centralized in `charts.py`. The analyzer passes data into the named chart builders; it does not create ad-hoc chart styles elsewhere.

## Repository Layout

```text
.
├── soc_perfetto_analyzer/
│   ├── analysis.py              # TraceProcessor SQL + fallback trace inventory
│   ├── cli.py                   # analyze, render-report, check-trace commands
│   ├── config.py                # event_config JSON/YAML loader
│   ├── quality_gate.py          # Phase 9 report quality checks
│   └── report/
│       ├── html.py              # Jinja2 template renderer
│       ├── json_report.py       # report.json and output bundle writer
│       └── model.py             # metrics -> template model
├── charts.py                    # Required chart builder source of truth
├── template.html.j2             # Required report template
├── sample_model.py              # Standalone sample model for template validation
├── render.py                    # Sample renderer helper
├── event_config.yaml            # Current event configuration
├── event_config.json            # Legacy event configuration
├── android-perfetto-FHD30-S24U.pftrace
├── tests/
├── pyproject.toml
├── uv.lock
└── soc_perfetto_analyzer_integrated_plan_v2.md
```

## Requirements

- Windows PowerShell or a compatible shell
- Python 3.11 or newer
- `uv`
- Network access for initial dependency sync

The project is tested with Python 3.14 in the local `uv` environment.

## Setup

From the repository root:

```powershell
uv sync --extra test
```

If your installed `uv` does not support `sync --extra`, use:

```powershell
uv venv --python 3.14 .venv
uv pip install -e ".[test]"
```

## Quick Start

Generate a report for the included sample trace:

```powershell
uv run python -m soc_perfetto_analyzer.cli analyze `
  --trace android-perfetto-FHD30-S24U.pftrace `
  --event-config event_config.yaml `
  --out out\android-perfetto-FHD30-S24U-yaml-uv
```

Open the generated report:

```text
out/android-perfetto-FHD30-S24U-yaml-uv/report.html
```

Run only the trace/report quality check:

```powershell
uv run python -m soc_perfetto_analyzer.cli check-trace `
  --trace android-perfetto-FHD30-S24U.pftrace `
  --event-config event_config.yaml
```

Render an HTML report directly:

```powershell
uv run python -m soc_perfetto_analyzer.cli render-report `
  --trace android-perfetto-FHD30-S24U.pftrace `
  --event-config event_config.yaml `
  --out out\report.html
```

## Output Bundle

`analyze` writes this structure:

```text
out/<job>/
├── report.html
├── report.json
├── trace_inventory.json
├── capability.json
├── quality_gate.json
├── metrics/
│   ├── hardware_usage.json
│   ├── sw_portion.json
│   ├── thread_runtime.json
│   ├── thread_function_bottlenecks.json
│   ├── wakeup_jitter.json
│   ├── periods.json
│   ├── cpu_clock.json
│   ├── cluster_clock_attribution.json
│   └── contention.json
└── appendix/
    ├── matched_threads.csv
    ├── unmatched_patterns.csv
    ├── ambiguous_matches.csv
    └── sql_queries.txt
```

`out/` is generated output and is ignored by git.

## Current Sample Trace Behavior

For `android-perfetto-FHD30-S24U.pftrace` with `event_config.yaml`, exact configured thread resolution currently finds:

```text
Uni:PERSONAL_IM
```

These exact configured names are not present in the trace:

```text
WNC-DnsResult
WNC-SensorHWPro
WNC-IspRequest
WNC-EisPlugin
```

Partial terms such as `Dns` and `Sensor` do appear elsewhere in the trace, but they are not automatically treated as target thread matches. This is intentional. Candidate matches can create false positives, so only exact `event_config` targets are used for metric generation.

## Metric Notes

### Thread Runtime

The §4 runtime chart uses actual CPU running bursts from the `sched` table:

```sql
select dur, cpu
from sched
where utid in (...)
  and dur > 0
order by ts
```

This means the runtime boxplot shows short scheduler run bursts, not higher-level task/frame intervals. For the included sample trace, `Uni:PERSONAL_IM` has 229 running bursts.

### Wakeup / Scheduling Jitter

The §5 jitter table uses runnable wait samples from `thread_state`:

```sql
select dur
from thread_state
where utid in (...)
  and state in ('R', 'R+')
  and dur > 0
order by ts
```

This is different from running duration. In the included sample trace, the runnable wait tail is much larger than the running-burst tail.

### PMU

If `linux.perf` cycle/instruction samples are absent, the report marks cycle, instruction, and IPC fields as:

```text
N/A: linux.perf absent
```

The analyzer does not fabricate cycle or instruction percentages from CPU frequency.

If `linux.perf` callstack samples and stack profile tables are present, the analyzer reports the top three sampled functions per configured `event_config` target thread. If PMU counters or callstacks are absent, the function bottleneck table is replaced with a reasoned `N/A:` caveat.

### CPU Cluster Topology

Cluster naming can vary by SoC and by project. Add an optional top-level `cpu_topology` block to `event_config.yaml` when the project knows the correct CPU grouping:

```yaml
cpu_topology:
  source: project_config
  clusters:
    - name: little
      cpus: [0, 1]
      role: efficiency
      freq_hint_ghz:
        min: 0.3
        max: 1.4
    - name: mid
      cpus: [2, 3, 4, 5, 6]
      role: performance
      freq_hint_ghz:
        min: 0.5
        max: 2.6
    - name: big
      cpus: [7]
      role: prime
      freq_hint_ghz:
        min: 0.6
        max: 3.4
```

If `cpu_topology` is absent, the analyzer groups CPUs by observed max `cpu_frequency`; only the legacy CPU-number rule is used as a final fallback.

The same topology shape can also be supplied as a separate override file:

```powershell
uv run python -m soc_perfetto_analyzer.cli analyze `
  --trace android-perfetto-FHD30-S24U.pftrace `
  --event-config event_config.yaml `
  --topology-config configs\s24u_cpu_topology.yaml `
  --out out\job
```

### Clock Ramp Attribution

The §6 clock section reports frequency ramp windows, not only clock drops. For each ramp window it compares target runtime, non-target runtime, newly observed co-runners, target migration into the cluster, and periodicity evidence. Attribution values are:

```text
added_task_pressure
periodic_target_migration
mixed_pressure
unknown
```

The output is written to `metrics/cluster_clock_attribution.json` and mirrored in `report.json` as `cluster_clock_attribution`.

## Phase 9 Quality Gate

The analyzer enforces the v2 report gate:

- verdicts exist for §2 through §7
- dashboard issues follow the issue object schema
- emphasized numbers include comparison context
- every chart has a non-empty caption
- bare `N/A` strings are rejected
- §5 links to §6 where needed
- PMU-missing cycle/inst fields are non-numeric
- severity labels use the known color vocabulary
- the report contains nav §0 through §8 and five HW badges
- all required chart slots are populated through `charts.py`

The result is written to:

```text
out/<job>/quality_gate.json
```

## Testing

Run the full test suite:

```powershell
uv run python -m pytest -q
```

The tests cover:

- event config JSON/YAML loading
- exact target extraction
- configurable CPU topology parsing
- TraceProcessor SQL analysis against the included sample trace
- cluster clock ramp attribution
- PMU capability and function hotspot extraction
- report model construction through `charts.py`
- output bundle contract
- Phase 9 quality gate behavior

## Development Notes

- Do not generate charts directly in report/model code. Use `charts.py`.
- Do not commit `out/`, `.venv/`, cache directories, or editable-install metadata.
- Keep candidate thread matches separate from confirmed event_config targets.
- If a target is not exact-matched, update `event_config.yaml` after manual trace inspection rather than silently accepting a partial match.

## Known Limitations

- Task interval reconstruction from event_config start/end conditions is not implemented yet. Current §4 runtime is scheduler running-burst distribution.
- Candidate/fuzzy matching is not used for metric generation.
- PMU-derived cycle/instruction percentages and function bottlenecks require a trace captured with `linux.perf` callstack samples.
- HW utilization counters are only reported when discoverable in the trace.
