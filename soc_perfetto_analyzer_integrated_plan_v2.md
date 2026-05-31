# SoC Perfetto Analyzer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Multimedia scenario, especially camera scenario, Perfetto trace를 분석해 HW usage, HW driving SW cost, vendor-thread runtime/jitter, CPU clock influence를 하나의 self-contained HTML report로 생성하는 analyzer를 구현한다.

**Architecture:** Analyzer는 `Capability/Integrity Audit`을 먼저 수행한 뒤, trace가 실제로 지원하는 metric만 계산한다. Perfetto TraceProcessor SQL 결과를 normalized model로 변환하고, YAML rule을 적용해 HW usage, SW attribution, thread runtime, wakeup jitter, CPU clock correlation, contention 후보를 계산한 뒤 `report_sample.html` 형식의 dashboard로 렌더링한다.

**Tech Stack:** Python 3.11+, Perfetto TraceProcessor Python API 또는 `trace_processor_shell`, PyYAML, pandas/numpy optional, Jinja2, Plotly offline 또는 static SVG fallback, pytest.

---

## 0. Reviewed Inputs

이 문서는 아래 입력을 통합해 작성한 단일 구현 계획이다.

| Source | 역할 | 반영한 핵심 |
|---|---|---|
| `D:/YHJOO/SOC_Perfetto_Analyzer/soc_perfetto_analyzer_plan.md` | 다른 경로에서 작성된 상세 계획 | PMU 한계, capability-first 설계, data source 측정 가능성, trace audit 선행, graceful degrade |
| `C:/Users/user/Documents/03_Soc_Perfetto_Analyzer/SOC_PERFETTO_ANALYZER_GOALS.md` | 현재 작성된 goal 중심 계획 | YAML 계약, data model, G0-G10 agent goal, acceptance criteria, output JSON 구조 |
| `D:/YHJOO/SOC_Perfetto_Analyzer/report_sample.html` | 목표 report format | sticky nav, dark dashboard, HW badge, capability audit, HW map, HW SW portion, runtime table, jitter table, CPU clock, contention, appendix |

## 1. Product Objective

Analyzer가 답해야 하는 질문은 4개다.

1. **R1: 어떤 HW가 사용되었는가?**
   - GPU
   - DPU/display pipeline
   - Codec HW, 예: MFC/APV/VPU
   - ISP/camera pipeline
   - NPU/DSP/NN accelerator
2. **R2: 해당 HW를 구동하기 위한 CPU-side SW cost는 전체 SW 중 어느 portion인가?**
   - 실제 dump에 PMU가 포함되는 것을 전제로 sampled cycle/instruction percentage를 1차 지표로 계산
   - scheduler running time percentage는 항상 함께 제공하는 baseline 지표
   - PMU event가 누락되거나 parsing되지 않으면 N/A와 함께 재덤프용 `pbtx` 보완안을 제안
3. **R3: YAML config에 정의된 vendor category/thread의 수행 시간 분포와 scheduler jitter는 어떤가?**
   - camera HAL
   - codec HAL
   - HWC/display
   - audio HAL
   - custom vendor category
4. **R4: SW 수행 동안 CPU core별 clock 변화가 성능에 영향을 주었을 가능성이 있는가?**
   - core/cluster frequency timeline
   - target thread running interval과 frequency overlap
   - jitter/runtime outlier 주변 DVFS/thermal 후보


## 1.5 Report Authoring Doctrine (v2 — top priority)

이전 버전 report가 "숫자 표만 가득하고 해석이 없다"는 피드백을 받은 근본 원인은,
이 계획이 *무엇을 계산/표시할지*는 정의했지만 *어떻게 해석/표현/서술할지*를 정의하지
않았기 때문이다. 아래 7원칙은 **모든 섹션 렌더가 반드시 통과해야 하는 최상위 규칙**이며,
§6 Output Contract와 §10 Phase 9/8.5의 상위 규범이다.

| 원칙 | 규칙 | 위반 → 교정 |
|---|---|---|
| **P1 Answer-first** | 각 섹션은 숫자 표 이전에 **한 문장 verdict**로 시작 | (표만) → "Camera HAL은 30fps 안정, codec worker가 p99 920µs로 튄다" |
| **P2 So-what** | 모든 핵심 수치 옆에 **판정(좋다/주의/나쁘다)+이유** | "p99 920µs" → "p99 920µs 🔴 (주기의 2.8%, big core contention 정황)" |
| **P3 Comparison** | 단독 수치 금지. **baseline·임계·분포 위치**와 함께 | "CoV 0.61" → "CoV 0.61 (정상 thread 0.25 대비 2.4×)" |
| **P4 Visual-first** | 분포/시계열/상관은 **표가 아니라 그래프**가 1차 표현 | jitter 분포를 표로만? → CDF가 main, 표는 details |
| **P5 Hierarchy** | 섹션당 **핵심 1~3개만 강조**, 나머지는 collapse | 20개 필드 평면 나열 금지 |
| **P6 Connect** | 섹션은 관련 섹션을 **명시적으로 cross-ref 링크** | §5 jitter 행 → "§6 clock 7.2s throttle 참조" |
| **P7 Honest** | 불확실은 confidence/"inferred", N/A는 사유+next-step | "throttling caused" 금지 → "overlap, inferred" |

각 섹션의 표준 구조는 다음 3단으로 고정한다(§6.4가 이를 강제):

```
섹션 = [Verdict 한 문장 (P1)] + [핵심 시각화 1~3개 (P4, 부록 C 규격)] + [상세 표 (collapse, P5)]
       + 각 강조 수치는 [값 + 판정 + 비교] (P2/P3)
       + 관련 섹션 [cross_ref] (P6)
```


## 2. Key Product Decisions

기존 계획의 `[DECISION]` 항목은 다음 초기 정책으로 확정한다.

| Decision | Final Initial Policy | 이유 |
|---|---|---|
| R2 primary metric | PMU가 포함된 dump에서는 `cycle%`, `instruction%`, `IPC`를 1차 지표로 사용하고 `time%`를 baseline으로 병기 | 사용자가 실제 dump에 PMU를 포함하고 있으므로 cycle/instruction 기반 SW portion을 핵심 가치로 올림 |
| PMU absent handling | cycle/instruction은 `N/A: linux.perf absent or incomplete`로 표시하고 `pbtx` 보완안을 report와 `check-trace`에 출력 | CPU freq 기반 값을 실제 instruction/cycle처럼 보이지 않게 하면서 다음 dump를 개선할 수 있어야 함 |
| CPU freq weighted proxy | `freq_weighted_runtime`이라는 별도 proxy metric으로만 제공 | 실제 cycle count가 아니라 clock exposure 지표임을 분리 |
| HW keyword 기본값 | generic rule을 제공하되, sample trace audit으로 SoC별 YAML을 보정 | vendor naming이 build마다 다름 |
| category 중복 matching | 선언 순서 우선 귀속, 중복은 Appendix에 warning | report를 생성하되 audit 가능하게 함 |
| portion denominator | `selected multimedia CPU running time`을 primary, `total CPU busy time`을 secondary | 전체 system 기준은 background noise가 커서 multimedia 분석에 불리함 |
| jitter 대표값 | `wakeup p95/p99`, `CoV`, `MAD`, `runnable_ratio` 병기 | 평균만으로 tail latency를 설명하기 어려움 |
| window 기본 정책 | marker-first, 없으면 warmup trim + activity heuristic | marker가 없는 trace에서도 동작해야 함 |
| missing data policy | fail 대신 graceful degrade + capability badge + caveat | report가 "무엇을 못 봤는지"를 명확히 알려야 함 |
| report style | `report_sample.html`의 compact dark dashboard를 목표 contract로 사용 | 사용자가 선호한 format을 구현 기준으로 고정 |

## 3. Scope

### 3.1 MVP In Scope

- Local Perfetto trace 입력
- Scenario YAML 입력
- Analyzer rule YAML 입력
- TraceProcessor 기반 query
- Capability/integrity audit
- HW usage detection
- HW driving SW cost attribution
- category/thread runtime distribution
- runnable -> running latency
- frame/operation period inference
- CPU clock correlation
- PMU 기반 cycle/instruction/IPC 분석
- trace quality check의 `pbtx` 보완 제안
- contention/waker 후보 요약
- self-contained HTML report
- machine-readable `report.json`
- sample trace 기반 golden regression

### 3.2 MVP Out of Scope

- 모든 SoC/vendor tracepoint의 built-in 완전 지원
- HW 내부 utilization을 counter 없이 추정
- PMU 없이 instruction count 산출
- CPU clock correlation을 root cause로 단정
- proprietary register/log decoder를 common repo에 포함
- Android app UI 또는 web server 운영
- multi-trace A/B diff의 완성판

### 3.3 Extension Scope

아래는 extension hook으로 열어두되 MVP 구현에서는 최소 skeleton만 둔다.

- vendor private tracepoint decoder
- proprietary log/register summary ingestion
- direct device capture automation
- multi-trace comparison
- report export bundle zip
- CI artifact publishing

## 4. Measurement Contract

### 4.1 Capability-first Rule

Analyzer는 모든 계산 전에 trace capability를 먼저 산출한다.

```text
Trace input
  -> capability/integrity audit
  -> allowed metrics 결정
  -> metric engines 실행
  -> report caveat 자동 삽입
```

Metric engine은 capability map을 보고 다음 중 하나로 결과를 낸다.

| State | 의미 | Report 표시 |
|---|---|---|
| `available` | 필요한 source가 충분함 | 정상 숫자 |
| `partial` | 일부 source만 있어 estimation 가능 | 숫자 + confidence/caveat |
| `unavailable` | 필수 source가 없음 | `N/A` + 누락 source |
| `unknown` | source는 있으나 matching evidence 부족 | `UNKNOWN` + next capture recommendation |

### 4.2 Data Source Matrix

| Metric | Required data | Optional data | 없으면 |
|---|---|---|---|
| HW used/unknown/unused | IRQ/ftrace/counter/thread activity 중 하나 | HW util/freq counter | confidence 하락 또는 unknown |
| HW utilization busy% | HW util counter | HW freq counter | N/A |
| CPU-side SW time% | `sched_switch` 또는 `thread_state` | slices, binder/waker chain | R2 time% 불가 |
| cycle/instruction% | `linux.perf` PMU samples for `HW_CPU_CYCLES` and `HW_INSTRUCTIONS` | scaling metadata, sampling period/frequency | N/A + PMU `pbtx` 보완 제안 |
| thread runtime distribution | `sched_switch` 또는 `thread_state` | atrace slices | R3 runtime 불가 |
| wakeup latency | `sched_wakeup` 또는 `sched_waking` + running transition | wakeup source | R3 jitter N/A |
| period inference | marker/vsync/IRQ/slice/activity loop | target FPS hint | confidence 낮음 |
| CPU clock correlation | `cpu_frequency` counter | `cpu_idle`, thermal counter | R4 N/A 또는 partial |
| contention attribution | scheduler spans | waker chain, binder chain | top co-runner만 partial |

### 4.3 Confidence Vocabulary

| Confidence | 사용 조건 |
|---|---|
| `confirmed` | IRQ/ftrace/counter 등 HW 또는 scheduler-level evidence가 있음 |
| `estimated` | framework slice, HAL thread, activity heuristic 기반 |
| `weak` | name matching만 있고 runtime/evidence가 약함 |
| `unknown` | trace signal 부족 또는 matching 불충분 |
| `not_observed` | relevant source는 있었지만 evidence가 0 |
| `unavailable` | 필요한 source 자체가 trace에 없음 |

## 5. Input Contract

### 5.1 CLI Inputs

```powershell
python -m soc_perfetto_analyzer.cli analyze `
  --trace samples/camera_preview_30fps.perfetto-trace `
  --scenario configs/scenarios/camera_preview_30fps.yaml `
  --rules configs/rules/vendor_generic.yaml `
  --out out/camera_preview_30fps
```

### 5.2 Scenario YAML

```yaml
meta:
  scenario: camera_preview_30fps
  device: S25_EVT
  soc: vendor_soc_a
  target_fps: 30
  notes: Camera preview, display on, no explicit frame marker.

window:
  mode: auto               # auto | manual | full
  warmup_ms: 2000
  cooldown_ms: 0
  manual_start_ms: null
  manual_end_ms: null
  marker_start_regex:
    - "Camera.*start"
    - "process_capture_request"
  marker_end_regex:
    - "Camera.*stop"
    - "close_session"

period_detection:
  primary:
    source: slice
    regex:
      - "process_capture_request"
      - "notifyShutter"
      - "capture_result"
      - "frame_done"
      - "SOF"
      - "EOF"
  fallback:
    source: thread_activity
    category: camera_hal
    min_period_ms: 20
    max_period_ms: 50
  display:
    source: vsync
    expected_hz: 60

categories:
  camera_hal:
    priority: 10
    processes:
      - "cameraserver"
      - "android.hardware.camera.provider.*"
      - "vendor.*camera.*"
    threads:
      - "Camera.*"
      - "CamX.*"
      - "CHI.*"
      - "ISP.*"
    expect_periodic: true
    period_source: shot
    period_hint_hz: 30

  codec_hal:
    priority: 20
    processes:
      - "mediaserver"
      - "media.codec"
      - "vendor.*codec.*"
    threads:
      - "C2.*"
      - "OMX.*"
      - "MFC.*"
      - "APV.*"
    expect_periodic: true
    period_source: irq
    period_hint_hz: 30

  hwc:
    priority: 30
    processes:
      - "surfaceflinger"
      - "android.hardware.graphics.composer.*"
      - "vendor.*hwc.*"
    threads:
      - "HWC.*"
      - "Composer.*"
      - "DispSync.*"
      - "hwc.*"
    expect_periodic: true
    period_source: vsync
    period_hint_hz: 60

  audio_hal:
    priority: 40
    processes:
      - "audioserver"
      - "android.hardware.audio.*"
      - "vendor.*audio.*"
    threads:
      - "Audio.*"
      - "FastMixer"
      - "MixerThread"
    expect_periodic: true
    period_source: none
    period_hint_hz: null

thresholds:
  wakeup_p95_us: 300
  wakeup_p99_us: 500
  runnable_ratio_pct: 5
  runtime_cov: 0.5
  cpu_low_freq_ratio_pct: 20

output:
  report_title: SoC Multimedia Trace Report
  include_plots: true
  include_raw_appendix: true
  emit_metrics_json: true
```

### 5.3 Analyzer Rule YAML

```yaml
matching:
  mode: regex              # regex | glob | substring
  case_sensitive: false
  duplicate_category_policy: priority_first_warn
  min_thread_lifetime_ms: 1

hardware_blocks:
  gpu:
    display_name: GPU
    evidence:
      - type: counter_name_regex
        regex: "gpu.*util|kgsl.*busy|mali.*util"
        meaning: utilization
        confidence: confirmed
      - type: counter_name_regex
        regex: "gpu.*freq|kgsl.*freq|mali.*freq"
        meaning: frequency
        confidence: confirmed
      - type: ftrace_name_regex
        regex: "kgsl|mali|gpu"
        meaning: driver_or_irq
        confidence: confirmed
      - type: thread_name_regex
        regex: "RenderThread|Gpu.*|kgsl.*|mali.*"
        meaning: cpu_side_driver_thread
        confidence: weak

  dpu:
    display_name: DPU
    evidence:
      - type: ftrace_name_regex
        regex: "dpu|decon|drm|vblank|vsync"
        meaning: driver_or_irq
        confidence: confirmed
      - type: slice_name_regex
        regex: "HWC|HWComposer|presentDisplay|validateDisplay|setPowerMode"
        meaning: framework_or_hal_control
        confidence: estimated
      - type: thread_name_regex
        regex: "HWC.*|Composer.*|DispSync.*|hwc.*"
        meaning: cpu_side_driver_thread
        confidence: weak

  codec:
    display_name: CODEC
    aliases:
      - MFC
      - APV
      - VPU
    evidence:
      - type: ftrace_name_regex
        regex: "mfc|apv|vcodec|venus|vpu|vdec|venc"
        meaning: driver_or_irq
        confidence: confirmed
      - type: slice_name_regex
        regex: "MFC|APV|C2|Codec2|OMX|VPU|video_decode|video_encode"
        meaning: framework_or_hal_control
        confidence: estimated
      - type: thread_name_regex
        regex: "C2.*|OMX.*|MFC.*|APV.*"
        meaning: cpu_side_driver_thread
        confidence: weak

  isp:
    display_name: ISP
    evidence:
      - type: ftrace_name_regex
        regex: "isp|cam|camera|v4l2|csis|csi|camss|fimc"
        meaning: driver_or_irq
        confidence: confirmed
      - type: slice_name_regex
        regex: "ISP|Camera.*request|process_capture_request|frame_done|SOF|EOF"
        meaning: framework_or_hal_control
        confidence: estimated
      - type: thread_name_regex
        regex: "CamX.*|CHI.*|ISP.*|Camera.*"
        meaning: cpu_side_driver_thread
        confidence: weak

  npu:
    display_name: NPU
    evidence:
      - type: ftrace_name_regex
        regex: "npu|nnapi|htp|edgetpu|tpu|dsp"
        meaning: driver_or_irq
        confidence: confirmed
      - type: slice_name_regex
        regex: "NPU|NNAPI|NeuralNetworks|HTP|DSP|TPU"
        meaning: framework_or_hal_control
        confidence: estimated
      - type: thread_name_regex
        regex: "NNAPI.*|NPU.*|HTP.*|DSP.*"
        meaning: cpu_side_driver_thread
        confidence: weak

attribution:
  default_window:
    before_hw_start_ms: 5
    after_hw_end_ms: 5
  denominator:
    primary: selected_multimedia_cpu_running_time
    secondary:
      - total_cpu_busy_time
      - scenario_wall_time_x_core_count
  score_weights:
    direct_slice_overlap: 1.0
    direct_sched_overlap: 1.0
    binder_chain_overlap: 0.8
    wakeup_chain_overlap: 0.7
    nearby_control_thread: 0.5
  minimum_score: 0.5

pmu:
  mode: expected            # expected | auto | force | off
  events:
    - instructions
    - cpu-cycles
  absent_policy: mark_na_and_recommend_pbtx
  minimum_sample_rows: 100
  require_thread_mapping: true
  report_sampling_note: true

cpu_clock:
  cluster_map:
    little: [0, 1, 2, 3]
    mid: [4, 5]
    big: [6, 7]
  low_freq_threshold_ratio: 0.65
```

## 6. Output Contract

### 6.1 Output Directory

```text
out/<job_id>/
  report.html
  report.json
  trace_inventory.json
  capability.json
  metrics/
    hardware_usage.json
    sw_portion.json
    thread_runtime.json
    wakeup_jitter.json
    periods.json
    cpu_clock.json
    contention.json
  appendix/
    matched_threads.csv
    unmatched_patterns.csv
    ambiguous_matches.csv
    sql_queries.txt
```

### 6.2 `report.json` Top-level Schema

```json
{
  "metadata": {
    "scenario": "camera_preview_30fps",
    "device": "S25_EVT",
    "soc": "vendor_soc_a",
    "trace_duration_ms": 12400,
    "analysis_window_ms": [2000, 12400],
    "generated_at": "2026-05-30T14:22:00+09:00",
    "analyzer_version": "0.1.0",
    "trace_processor_version": "47.0"
  },
  "capability": {},
  "integrity": {},
  "hardware_usage": [],
  "sw_portion": [],
  "thread_runtime": [],
  "wakeup_jitter": [],
  "periods": [],
  "cpu_clock": {},
  "contention": [],
  "issues": [],
  "caveats": [],
  "appendix": {}
}
```

### 6.3 HTML Report Visual Contract

`report_sample.html`의 형식을 목표 report contract로 사용한다.

Required layout:

- dark compact engineering dashboard
- left sticky nav
- top header with scenario, device, duration, window, generated time
- section IDs `§0` to `§8`
- HW status cards for GPU/DPU/CODEC/ISP/NPU
- KPI cards for FPS, worst jitter CoV, max wakeup p99, throttle events
- auto issue list
- tables with compact numeric alignment
- caveat/warning note boxes
- Appendix with config dump and version info
- self-contained single HTML file

Required sections:

| Section | Title | Purpose |
|---|---|---|
| `§0` | Executive dashboard | 한 화면 summary, HW badges, key issues |
| `§1` | Capability & integrity audit | trace가 어떤 metric을 지원하는지 표시 |
| `§2` | HW usage map | R1, HW verdict와 evidence |
| `§3` | HW SW portion | R2, PMU cycle/instruction primary metric and time baseline |
| `§4` | Thread runtime profile | R3a, category/thread duration distribution |
| `§5` | Wakeup / scheduling jitter | R3b, wakeup p50/p95/p99/CoV/MAD/interval jitter |
| `§6` | CPU clock & influence | R4, clock drop/throttle 후보와 SW overlap |
| `§7` | Contention attribution | co-runner, preemptor, waker chain 후보 |
| `§8` | Appendix | unmatched/ambiguous config, caveats, versions, config dump |

### 6.4 HTML Report Content Contract (v2 — verdict-first, not field-lists)

이전 버전의 "Must show: [필드 나열]"은 폐기한다. 모든 섹션은 §1.5의 3단 구조를 따른다.
각 섹션에 대해 (a) verdict 생성 규칙, (b) 필수 시각화(부록 C 규격 참조), (c) collapse 상세,
(d) emit하는 issue를 정의한다.

#### Verdict 생성 규칙 (P1 — deterministic template, 자유작문 금지)

`metrics/narrative.py`가 metric JSON으로부터 아래 template을 채운다.

| 섹션 | verdict template |
|---|---|
| §1 Capability | `"{sched/clock 상태}; {PMU 상태}, so R2 {tier}."` |
| §2 HW | `"{used_count}/{total} HW blocks active: {used_list}. {unknown_note}"` |
| §3 Portion | `"Multimedia SW = {sum_pct}% of {denominator}. Largest driver: {top_hw} ({top_pct}%)."` |
| §4 Runtime | `"{n} threads profiled. Most variable: {worst} (CoV {cov}, {ratio}× baseline)."` |
| §5 Jitter | `"Wakeup tail worst on {worst} (p99 {v}µs, {ratio}× cluster baseline). {hint}"` |
| §6 Clock | `"{n_drop} clock-drop events; {n_overlap} overlap multimedia runtime outliers."` |
| §7 Contention | `"During {target} outliers, {top_corunner} is dominant co-runner (candidate)."` |

#### 섹션별 contract

| § | Verdict | 필수 시각화 (부록 C) | Collapse 상세 | Emit issues |
|---|---|---|---|---|
| §0 | (dashboard 자체가 요약) | HW badge row, KPI cards(부제 포함), top-issue list (issue object) | — | critical+warning 최대 5 |
| §2 | HW verdict | SoC block diagram (used=채색+실선, unknown=회색+점선) | evidence 표(IRQ/driver/util/freq/confidence) | `HW_UNKNOWN` |
| §3 | portion verdict | 100% stacked portion bar (**분모를 차트 제목에**) | time/cycle/inst/IPC 표 + **tier 배지** | `PMU_MISSING` |
| §4 | runtime verdict | box/violin per thread + **peer baseline 선** | 전체 통계 표 (min..max/CoV/MAD/cluster) | `RUNTIME_COV_HIGH` |
| §5 | jitter verdict | **cluster별 CDF** + jitter ranking bar + interval strip(periodic) | p50..p99/CoV/MAD/runnable/verdict 표 + §6 cross_ref | `JITTER_P99_HIGH`,`RUNNABLE_RATIO_HIGH` |
| §6 | clock verdict | freq timeline(**target active 음영**) + residency bar + runtime↔freq scatter(**r 표기**) | throttle 표(inferred 표기) | `CLOCK_DROP_OVERLAP` |
| §7 | contention verdict | co-runner 표 + waker chain mini-diagram | — | — |
| §8 | — | — | unmatched/ambiguous/caveats/versions/config dump | `PERIOD_LOW_CONF` 등 info |

#### 시각화 공통 규칙 (모든 차트 — 부록 C가 코드로 강제)

1. 모든 차트는 단일 dark theme(부록 C `charts.py`의 THEME), 투명 배경.
2. 색은 **의미 인코딩**만: severity(ok/warn/bad/na), cluster 고정색, HW 고정색. 무지개 금지.
3. **각 차트에 캡션 1줄 필수** — "무엇을 보는 그래프이고 무엇을 읽어야 하는가".
4. 빈 데이터는 빈 캔버스 금지 → `"no data: <사유>"` placeholder.
5. Plotly: 첫 figure만 `include_plotlyjs='inline'`, 이후 `False`.
6. 엔진은 **데이터만** 전달하고 Plotly layout을 직접 만지지 않는다. 모든 차트는
   `charts.py`의 named builder를 통해서만 생성한다(형식 일관성의 단일 소스).

## 7. Core Data Model

### 7.1 TraceSession

```python
TraceSession:
  trace_path: str
  trace_size_bytes: int
  trace_duration_ns: int
  analysis_window_ns: tuple[int, int]
  scenario: str
  device: str
  soc: str
  generated_at: str
```

### 7.2 Capability

```python
Capability:
  sched_switch: DataSourceState
  sched_wakeup: DataSourceState
  cpu_frequency: DataSourceState
  cpu_idle: DataSourceState
  irq_events: DataSourceState
  hw_counters: dict[str, DataSourceState]
  linux_perf: DataSourceState
  vsync: DataSourceState
  binder: DataSourceState
  atrace_slices: DataSourceState
```

### 7.3 DataSourceState

```python
DataSourceState:
  present: bool
  state: str              # available | partial | unavailable | unknown
  rows: int
  examples: list[str]
  affects: list[str]
  caveat: str | None
```

### 7.4 ThreadIdentity

```python
ThreadIdentity:
  utid: int
  tid: int
  thread_name: str
  upid: int
  pid: int
  process_name: str
  category: str | None
  category_priority: int | None
  lifetime_ns: int
```

### 7.5 HardwareEvidence

```python
HardwareEvidence:
  block: str
  source_type: str        # counter | ftrace | slice | thread | irq
  ts_ns: int | None
  dur_ns: int | None
  value: float | None
  matched_rule: str
  meaning: str
  confidence: str
  evidence_text: str
```

### 7.6 HardwareUsage

```python
HardwareUsage:
  block: str
  verdict: str            # used | not_observed | unknown
  confidence: str
  evidence_count: int
  irq_count: int | None
  driver_runtime_ns: int | None
  util_avg_pct: float | None
  freq_observed: bool
  first_active_ts_ns: int | None
  last_active_ts_ns: int | None
  caveats: list[str]
```

### 7.7 SoftwareInterval

```python
SoftwareInterval:
  category: str
  thread: ThreadIdentity
  ts_ns: int
  dur_ns: int
  running_ns: int
  runnable_wait_ns: int | None
  cpu: int | None
  cluster: str | None
  slice_name: str | None
```

### 7.8 ThreadRuntimeStats

```python
ThreadRuntimeStats:
  category: str
  thread_name: str
  process_name: str
  count: int
  min_us: float
  avg_us: float
  p50_us: float
  p95_us: float
  p99_us: float
  max_us: float
  cov: float
  mad_us: float
  total_running_ms: float
  cluster_ratio: dict[str, float]
```

### 7.9 WakeupJitterStats

```python
WakeupJitterStats:
  category: str
  thread_name: str
  samples: int
  p50_us: float
  p95_us: float
  p99_us: float
  max_us: float
  cov: float
  mad_us: float
  runnable_ratio_pct: float
  interval_sigma_ms: float | None
  verdict: str
  outliers: list[dict]
```

### 7.10 SwPortion

```python
SwPortion:
  hardware_block: str
  category: str
  process_name: str
  thread_name: str
  wall_ms: float
  running_ms: float
  time_pct_primary: float
  time_pct_total_cpu: float
  sampled_cycles_pct: float | None
  sampled_instructions_pct: float | None
  ipc: float | None
  freq_weighted_runtime: float | None
  attribution_score: float
  confidence: str
  caveats: list[str]
```

### 7.11 CpuClockEvent

```python
CpuClockEvent:
  ts_ns: int
  dur_ns: int
  cpu: int
  cluster: str
  freq_khz: int
  low_freq: bool
  overlaps_target_thread: bool
```

## 8. System Architecture

```text
src/soc_perfetto_analyzer/
  cli.py
  config/
    schema.py
    loader.py
    defaults.py
  trace/
    processor.py
    inventory.py
    capability.py
    integrity.py
    quality.py
    sql/
      metadata.sql
      process_threads.sql
      slices.sql
      sched.sql
      counters.sql
      irq.sql
      cpu_frequency.sql
      linux_perf.sql
      binder.sql
  normalize/
    time.py
    threads.py
    categories.py
    windows.py
  hardware/
    rules.py
    detector.py
    evidence.py
  periods/
    detector.py
    camera.py
    display.py
    codec.py
    audio.py
  metrics/
    running_spans.py
    thread_runtime.py
    wakeup_jitter.py
    sw_portion.py
    pmu.py
    cpu_clock.py
    contention.py
    issues.py
  report/
    model.py
    html.py
    template.html.j2
    style.css
    assets.py
  device/
    capture.py
  extensions/
    README.md
configs/
  scenarios/
    camera_preview_30fps.yaml
  rules/
    vendor_generic.yaml
  perfetto/
    camera_trace_config.pbtx
samples/
  README.md
docs/
  TRACE_CAPTURE.md
  CALIBRATION.md
tests/
  unit/
  integration/
  fixtures/
  golden/
```

### 8.1 Module Responsibilities

| Module | Responsibility |
|---|---|
| `cli.py` | `inspect`, `analyze`, `check-trace`, `render-report` command 제공 |
| `config/loader.py` | YAML load, validation, regex compile, duplicate policy 적용 |
| `trace/processor.py` | TraceProcessor backend abstraction |
| `trace/inventory.py` | process/thread/slice/counter/ftrace inventory 추출 |
| `trace/capability.py` | data source availability와 affected metric 계산 |
| `trace/integrity.py` | ftrace data loss, duration, clock sync, row count sanity |
| `normalize/categories.py` | category/thread matching과 ambiguous match report |
| `hardware/detector.py` | HW block별 evidence aggregation과 verdict 산출 |
| `periods/detector.py` | marker-first, heuristic-second period inference |
| `metrics/thread_runtime.py` | running duration distribution |
| `metrics/wakeup_jitter.py` | wakeup-to-running latency pairing |
| `metrics/sw_portion.py` | HW driving SW attribution과 denominator percentage |
| `metrics/pmu.py` | linux.perf sample 기반 cycle/instruction/IPC |
| `metrics/cpu_clock.py` | CPU freq residency와 target overlap |
| `metrics/contention.py` | co-runner, preemptor, waker chain 후보 |
| `metrics/issues.py` | top issue ranking |
| `report/html.py` | `report_sample.html` style의 self-contained HTML 생성 |

## 9. Analysis Algorithms

### 9.1 Analysis Window

Priority:

1. Manual window from YAML
2. start/end marker slice
3. scenario activity window from target categories
4. full trace minus warmup/cooldown

Output must include:

- selected start/end
- selection method
- confidence
- caveat if fallback used

### 9.2 HW Usage Detection

For each HW block:

```text
collect evidence from configured rules
  -> strong evidence: IRQ/ftrace/HW counter
  -> medium evidence: slice/framework/HAL operation
  -> weak evidence: thread name/activity only
aggregate:
  if strong evidence count > 0: verdict=used, confidence=confirmed
  else if medium evidence count > 0: verdict=used, confidence=estimated
  else if weak evidence count > 0: verdict=unknown or used with weak confidence
  else if relevant source was present: verdict=not_observed
  else: verdict=unknown, confidence=unavailable
```

Important distinction:

- HW usage means the block likely participated.
- HW utilization/busy% requires util counter.
- CPU-side driver/HAL cost is separate from HW busy time.

### 9.3 Thread Runtime Distribution

Inputs:

- thread/category matched targets
- `sched_switch` or `thread_state`
- optional slice context

Metrics:

- count
- min/avg/p50/p95/p99/max
- CoV
- MAD
- total running time
- cluster ratio
- per-period sum if period exists

### 9.4 Wakeup Latency Pairing

Definition:

```text
wakeup_latency = first_running_ts_after_wakeup - wakeup_ts
```

Pairing rules:

- Use `utid`/`tid` as identity.
- If multiple wakeups occur before running, use latest wakeup by default and count overwritten wakeups.
- Migration does not break pairing.
- Missing wakeup means sample excluded from latency but counted in missing statistics.
- Cluster is assigned from first running CPU.

### 9.5 Period Inference

Priority:

1. explicit marker
2. camera shot/request/result/SOF/EOF
3. display vsync
4. codec buffer/IRQ activity
5. thread activity periodicity

Output:

- period count
- estimated FPS/Hz
- source
- confidence
- missing marker caveat

### 9.6 HW Driving SW Attribution

Candidates:

- SW interval directly overlaps HW evidence interval
- SW interval immediately precedes HW interval within configured window
- wakeup chain points from IRQ/driver to HAL thread
- binder chain connects framework and HAL if available
- YAML declares category as likely driver for the HW block

Score:

```text
score =
  direct_sched_overlap * 1.0
  + direct_slice_overlap * 1.0
  + binder_chain_overlap * 0.8
  + wakeup_chain_overlap * 0.7
  + nearby_control_thread * 0.5
```

Report must separate:

- selected multimedia SW total cost
- HW-attributed SW cost
- unattributed selected SW cost

### 9.7 PMU Cycle/Instruction Policy

R2는 PMU가 포함된 dump를 우선 경로로 사용한다. `linux.perf` sample이 있고 `HW_CPU_CYCLES`, `HW_INSTRUCTIONS` 또는 equivalent event가 thread identity와 연결되면 cycle/instruction/IPC를 report의 primary SW portion 지표로 표시한다.

```text
sampled_cycle_pct(thread) =
  sampled_cycles(thread) / sampled_cycles(denominator_scope) * 100

sampled_instruction_pct(thread) =
  sampled_instructions(thread) / sampled_instructions(denominator_scope) * 100

ipc = sampled_instructions / sampled_cycles
```

PMU quality gate:

```text
if linux.perf source missing:
  pmu_status = unavailable
if cycle or instruction event missing:
  pmu_status = incomplete
if sample row count < configured minimum:
  pmu_status = weak
if samples cannot be mapped to thread/process:
  pmu_status = incomplete
else:
  pmu_status = available
```

If PMU is absent or incomplete:

- `cycle_pct = null`
- `instruction_pct = null`
- `ipc = null`
- caveat: `PMU data absent/incomplete -> cycle/inst/IPC are N/A. Time % shown as fallback.`
- `check-trace` emits a concrete `pbtx` recommendation for `linux.perf` with cycle/instruction followers.

Optional proxy:

```text
freq_weighted_runtime = running_ns * cpu_frequency_khz
```

This proxy must not be labeled as real cycles.

Sampling caveat:

- PMU values are sample-based estimates, not exact retired instruction totals.
- Report must show sample count and event source.
- If PMU multiplexing/scaling metadata is available, expose it in Appendix.

### 9.8 CPU Clock Correlation

For each target running interval:

1. Join running span with CPU frequency segments by CPU and time overlap.
2. Compute weighted average frequency.
3. Compute low frequency overlap ratio using cluster-specific threshold.
4. Compare outlier runtime/jitter windows with frequency drop events.
5. Emit influence candidate only when overlap is temporal and meaningful.

Wording:

- Allowed: "CPU4-6 clock drop overlapped codec runtime outliers."
- Not allowed: "CPU throttling caused codec issue" unless thermal/power evidence confirms it.

### 9.9 Contention Attribution

MVP computes:

- top co-runners during target outlier windows
- preemptor overlap from scheduler transitions
- waker chain from `sched_wakeup` source if available
- binder chain if binder data exists

Report shows candidates with confidence, not final root cause.


### 9.10 Insight & Issue Generation (v2)

issue를 "기계적 ranking"이 아니라 **사람이 읽는 문장 객체**로 생성한다.

```python
Issue:
  severity: str        # critical | warning | info   (색/배치 결정)
  rule_id: str         # 추적·테스트용 안정 ID
  headline: str        # 주어(thread/hw) + 증상 + 수치  (형용사-only 금지)
  comparison: str|None # baseline/임계/분모 대비 (P3)
  cross_ref: str|None  # 관련 섹션 라벨
  cross_ref_anchor: str|None  # HTML anchor (#s5 등)
  confidence: str      # confirmed | estimated | weak
  next_step: str|None  # 재현/추가캡처/조사 방향
```

규칙: headline은 항상 주어+증상+수치. 모든 issue는 **comparison 또는 next_step 중 최소 1개**를 채운다.

MVP issue rule set (각 rule은 unit test 1개씩):

| rule_id | trigger | severity | headline 형식 |
|---|---|---|---|
| `JITTER_P99_HIGH` | wakeup p99 > thresh | warning/critical | "{thread} wakeup p99 {v}µs exceeds {thr}µs" |
| `RUNTIME_COV_HIGH` | CoV > thresh | warning | "{thread} runtime unstable (CoV {v}, {r}× baseline)" |
| `RUNNABLE_RATIO_HIGH` | runnable% > thresh | warning | "{thread} waits for CPU {v}% of active time" |
| `CLOCK_DROP_OVERLAP` | freq drop ∩ outlier | info/warning | "Clock drop on {cluster} overlaps {thread} outliers" |
| `PMU_MISSING` | linux.perf absent | info | "Cycle/instruction unavailable — PMU not captured" |
| `PERIOD_LOW_CONF` | period weak | info | "FPS estimate is heuristic (no frame marker)" |
| `HW_UNKNOWN` | relevant src absent | info | "{hw} usage indeterminate — no IRQ/counter" |

severity→배치: critical/warning만 dashboard top(최대 5, critical 우선). info는 해당 섹션 내에만.
severity→색: critical=🔴, warning=🟡, info=🔵/⬜ (color class는 단일 정의, 일관).

### 9.11 Baseline Model (v2 — P3 데이터 근거)

단독 수치를 금지하기 위해 모든 강조 수치는 아래 baseline 중 최소 하나와 함께 렌더한다.

| baseline | 정의 | 용도 |
|---|---|---|
| in-trace peer | 같은 category 최저 CoV thread | "동료 대비 N× 불안정" |
| cluster wakeup | 같은 cluster 전체 thread wakeup p50 중앙값 | little 느림 정상화 비교 |
| threshold | YAML thresholds | pass/fail 색 |
| self steady-state | warmup 이후 안정 구간 평균 | "자기 평균 대비 N×" |
| golden (확장) | 이전 trace metrics.json | A/B 회귀 |

comparison 문자열 생성기는 값→`"{r}× baseline" | "exceeds {thr}" | "{v}% of {denom}"` 형태로 표준화.

### 9.12 R2 SW Portion — 3-Tier Policy (v2, §9.7 확장)

PMU가 없을 때 R2가 "time%만"으로 빈약해지는 문제를, Perfetto stdlib의 cpu cycles
모듈(`linux.cpu.utilization.*`, freq×runtime 기반 megacycles)을 활용해 한 단계 보강한다.

| Tier | 조건 | 산출 지표 | report 라벨(배지) |
|---|---|---|---|
| **T1 measured** | linux.perf PMU 존재 | sampled cycle%/inst%/IPC | "measured (PMU sample)" |
| **T2 estimated** | cpu_frequency 존재(PMU 없음) | **megacycles = Σ(runtime×freq)** 기반 cycle% | "estimated (freq×time)" |
| **T3 time-only** | sched만 존재 | running time% only | "time-only" |

규칙:
- T2는 **instruction%·IPC를 산출하지 않는다**(retired instruction은 PMU 필수). cycle 추정만.
- T1/T2/T3 **tier 배지를 §3에 명시**하여 측정/추정/시간기준을 혼동시키지 않는다(P7).
- 기존 `freq_weighted_runtime` proxy는 T2 내부 계산으로 흡수하되 외부 표기는 "estimated cycles".


## 10. Implementation Phases and Agent Goals

### Phase 0. Planning Contract and Repo Bootstrap

**Goal:** Repository를 runnable Python CLI project로 만든다.

**Files:**

- Create `pyproject.toml`
- Create `src/soc_perfetto_analyzer/cli.py`
- Create `src/soc_perfetto_analyzer/__init__.py`
- Create `configs/scenarios/camera_preview_30fps.yaml`
- Create `configs/rules/vendor_generic.yaml`
- Create `samples/README.md`
- Create `README.md`

**Steps:**

- [ ] Create minimal package structure.
- [ ] Add `inspect`, `analyze`, `check-trace`, `render-report` CLI skeleton.
- [ ] Add sample scenario/rule YAML matching this document.
- [ ] Add README quickstart with PowerShell commands.
- [ ] Run CLI help checks.

**Verification:**

```powershell
python -m soc_perfetto_analyzer.cli --help
python -m soc_perfetto_analyzer.cli inspect --help
python -m soc_perfetto_analyzer.cli analyze --help
python -m soc_perfetto_analyzer.cli check-trace --help
python -m soc_perfetto_analyzer.cli render-report --help
```

**Exit Criteria:**

- All help commands run without traceback.
- No actual trace analysis logic is required yet.

### Phase 1. TraceProcessor, Inventory, Capability Audit

**Goal:** Perfetto trace를 열어 inventory와 capability/integrity report를 생성한다.

**Files:**

- Create `trace/processor.py`
- Create `trace/inventory.py`
- Create `trace/capability.py`
- Create `trace/integrity.py`
- Create `trace/sql/*.sql`
- Create `tests/unit/test_capability.py`

**Steps:**

- [ ] Implement TraceProcessor backend abstraction.
- [ ] Support Python API first, `trace_processor_shell` fallback.
- [ ] Query process/thread/slice/counter/ftrace/irq/cpu frequency/PMU/binder availability.
- [ ] Emit `trace_inventory.json`.
- [ ] Emit `capability.json`.
- [ ] Emit `integrity` section including data loss and duration.

**Verification:**

```powershell
python -m pytest tests/unit/test_capability.py -v
python -m soc_perfetto_analyzer.cli inspect `
  --trace samples/<provided>.perfetto-trace `
  --out out/inspect_sample
```

**Exit Criteria:**

- Report lists available/unavailable data sources.
- Missing or incomplete PMU produces capability caveat plus `pbtx` recommendation, not failure.
- Missing trace file produces clear error.

### Phase 2. YAML Loader and Target Resolution

**Goal:** Scenario/rule YAML을 validated config로 만들고 thread/category/HW target을 resolve한다.

**Files:**

- Create `config/schema.py`
- Create `config/loader.py`
- Create `config/defaults.py`
- Create `normalize/categories.py`
- Create `hardware/rules.py`
- Create `tests/unit/test_config_loader.py`
- Create `tests/unit/test_category_matching.py`

**Steps:**

- [ ] Validate required YAML fields.
- [ ] Compile regex/glob rules.
- [ ] Apply category priority.
- [ ] Emit matched/unmatched/ambiguous match records.
- [ ] Add `--dry-run` mode to `analyze`.

**Verification:**

```powershell
python -m pytest tests/unit/test_config_loader.py tests/unit/test_category_matching.py -v
python -m soc_perfetto_analyzer.cli analyze `
  --trace samples/<provided>.perfetto-trace `
  --scenario configs/scenarios/camera_preview_30fps.yaml `
  --rules configs/rules/vendor_generic.yaml `
  --dry-run
```

**Exit Criteria:**

- category별 matched thread count가 출력된다.
- zero-match pattern이 Appendix input으로 저장된다.
- duplicate match는 priority-first로 귀속되고 warning으로 남는다.

### Phase 3. Runtime and Wakeup Jitter Metrics

**Goal:** R3의 핵심인 thread runtime distribution과 wakeup jitter를 계산한다.

**Files:**

- Create `metrics/running_spans.py`
- Create `metrics/thread_runtime.py`
- Create `metrics/wakeup_jitter.py`
- Create `normalize/time.py`
- Create `tests/unit/test_running_spans.py`
- Create `tests/unit/test_wakeup_jitter.py`

**Steps:**

- [ ] Reconstruct running spans by thread.
- [ ] Pair wakeup to first running transition.
- [ ] Compute min/avg/p50/p95/p99/max/CoV/MAD.
- [ ] Compute cluster ratio.
- [ ] Save `thread_runtime.json`.
- [ ] Save `wakeup_jitter.json`.

**Verification:**

```powershell
python -m pytest tests/unit/test_running_spans.py tests/unit/test_wakeup_jitter.py -v
python -m soc_perfetto_analyzer.cli analyze `
  --trace samples/<provided>.perfetto-trace `
  --scenario configs/scenarios/camera_preview_30fps.yaml `
  --rules configs/rules/vendor_generic.yaml `
  --out out/runtime_jitter
```

**Exit Criteria:**

- category/thread별 runtime table에 필요한 metric이 모두 생성된다.
- wakeup source가 없으면 N/A/caveat로 처리된다.

### Phase 4. Window Selection and Period Inference

**Goal:** 분석 window와 frame/operation period를 결정한다.

**Files:**

- Create `normalize/windows.py`
- Create `periods/detector.py`
- Create `periods/camera.py`
- Create `periods/display.py`
- Create `periods/codec.py`
- Create `periods/audio.py`
- Create `tests/unit/test_window_selection.py`
- Create `tests/unit/test_period_detector.py`

**Steps:**

- [ ] Implement manual/marker/activity/warmup window selection.
- [ ] Implement camera marker and shot proxy detector.
- [ ] Implement display vsync detector.
- [ ] Implement codec IRQ/activity fallback.
- [ ] Attach confidence and source to each period.
- [ ] Save `periods.json`.

**Verification:**

```powershell
python -m pytest tests/unit/test_window_selection.py tests/unit/test_period_detector.py -v
python -m soc_perfetto_analyzer.cli analyze `
  --trace samples/<provided>.perfetto-trace `
  --scenario configs/scenarios/camera_preview_30fps.yaml `
  --rules configs/rules/vendor_generic.yaml `
  --out out/periods
```

**Exit Criteria:**

- analysis window is shown in report data.
- marker absent case still produces a caveat and fallback period when possible.

### Phase 5. HW Usage Detection

**Goal:** R1 HW usage map을 생성한다.

**Files:**

- Create `hardware/evidence.py`
- Create `hardware/detector.py`
- Create `tests/unit/test_hardware_detector.py`

**Steps:**

- [ ] Match evidence rules against counters, ftrace/IRQ, slices, threads.
- [ ] Aggregate evidence per HW block.
- [ ] Compute verdict/confidence.
- [ ] Compute IRQ count, driver runtime, util/freq counter status.
- [ ] Save `hardware_usage.json`.

**Verification:**

```powershell
python -m pytest tests/unit/test_hardware_detector.py -v
python -m soc_perfetto_analyzer.cli analyze `
  --trace samples/<provided>.perfetto-trace `
  --scenario configs/scenarios/camera_preview_30fps.yaml `
  --rules configs/rules/vendor_generic.yaml `
  --out out/hw_usage
```

**Exit Criteria:**

- GPU/DPU/CODEC/ISP/NPU each have verdict, confidence, evidence examples.
- No HW util counter means utilization N/A, not zero.

### Phase 6. PMU-first SW Portion Metrics

**Goal:** R2 CPU-side SW portion을 PMU cycle/instruction 중심으로 계산하고, scheduler time%를 baseline/fallback으로 병기한다.

**Files:**

- Create `metrics/sw_portion.py`
- Create `metrics/pmu.py`
- Create `tests/unit/test_sw_portion.py`
- Create `tests/unit/test_pmu_policy.py`

**Steps:**

- [ ] Compute PMU denominator for selected multimedia threads and total CPU samples.
- [ ] Compute selected multimedia CPU running denominator.
- [ ] Compute total CPU busy secondary denominator.
- [ ] Attribute SW intervals to HW evidence intervals.
- [ ] Compute sampled cycle/instruction percentage and IPC when PMU quality gate passes.
- [ ] Compute time percentage in all cases where scheduler data exists.
- [ ] If PMU is absent/incomplete, mark cycle/instruction/IPC N/A and attach `pbtx` recommendation.
- [ ] Save `sw_portion.json`.

**Verification:**

```powershell
python -m pytest tests/unit/test_sw_portion.py tests/unit/test_pmu_policy.py -v
python -m soc_perfetto_analyzer.cli analyze `
  --trace samples/<provided>.perfetto-trace `
  --scenario configs/scenarios/camera_preview_30fps.yaml `
  --rules configs/rules/vendor_generic.yaml `
  --out out/sw_portion
```

**Exit Criteria:**

- Time% denominator is explicit.
- PMU sample count, event names, and mapping quality are explicit.
- PMU absent/incomplete does not create fake cycle/instruction values.
- Report can reproduce `PMU data absent/incomplete -> time% fallback` note and recommended `pbtx` additions.

### Phase 7. CPU Clock and Influence Candidate Analysis

**Goal:** R4 CPU clock 변화와 target SW interval overlap을 분석한다.

**Files:**

- Create `metrics/cpu_clock.py`
- Create `tests/unit/test_cpu_clock.py`

**Steps:**

- [ ] Extract CPU frequency segments.
- [ ] Map CPU to cluster.
- [ ] Compute residency by core/cluster.
- [ ] Join target running spans with frequency segments.
- [ ] Detect clock drop events.
- [ ] Compute runtime/jitter overlap candidate.
- [ ] Save `cpu_clock.json`.

**Verification:**

```powershell
python -m pytest tests/unit/test_cpu_clock.py -v
python -m soc_perfetto_analyzer.cli analyze `
  --trace samples/<provided>.perfetto-trace `
  --scenario configs/scenarios/camera_preview_30fps.yaml `
  --rules configs/rules/vendor_generic.yaml `
  --out out/cpu_clock
```

**Exit Criteria:**

- cluster/core frequency residency is generated.
- influence candidates use non-causal wording unless thermal evidence exists.

### Phase 8. Contention and Issue Ranking

**Goal:** report dashboard의 top issues와 contention section을 생성한다.

**Files:**

- Create `metrics/contention.py`
- Create `metrics/issues.py`
- Create `tests/unit/test_contention.py`
- Create `tests/unit/test_issue_ranking.py`

**Steps:**

- [ ] Find top co-runners during outlier windows.
- [ ] Find preemptor overlap where scheduler data supports it.
- [ ] Extract waker chain if wakeup source is available.
- [ ] Add issue ranking for p99 jitter, runtime CoV, clock drop overlap, missing data.
- [ ] Save `contention.json`.

**Verification:**

```powershell
python -m pytest tests/unit/test_contention.py tests/unit/test_issue_ranking.py -v
```

**Exit Criteria:**

- Dashboard top issues can be generated from structured metrics.
- Contention candidates are marked as candidates, not proven root cause.

### Phase 8.5. Narrative, Baseline, and Issue Objects (v2 — new)

**Goal:** 모든 섹션 verdict 문장과 issue object를 metric JSON으로부터 **결정론적으로**
생성하는 단일 모듈을 만든다. report 렌더는 이 출력만 소비한다(자유작문 금지).

**Files:**

- Create `metrics/baseline.py`
- Create `metrics/narrative.py`
- Create `tests/unit/test_baseline.py`
- Create `tests/unit/test_narrative.py`

**Steps:**

- [ ] in-trace peer / cluster / threshold / self baseline 계산 (§9.11).
- [ ] 섹션별 verdict template 채우기 (§6.4 표).
- [ ] issue rule set 평가 → issue object 리스트 (§9.10).
- [ ] comparison 문자열 생성기 (값 → "N× baseline / exceeds thr / % of denom").
- [ ] severity → color/placement 매핑.

**Verification:**

```powershell
python -m pytest tests/unit/test_baseline.py tests/unit/test_narrative.py -v
```

**Exit Criteria:**

- 동일 metric JSON 입력 시 verdict/issue가 결정론적(byte-identical).
- 모든 issue가 comparison 또는 next_step을 보유.
- verdict 문장이 metric 값과 모순 없음(test로 검증).

### Phase 9. Report Renderer (v2 — verdict-first, charts.py-driven)

**Goal:** §1.5 doctrine과 §6.4 contract를 만족하는 self-contained HTML report를
생성한다. 모든 차트는 부록 C `charts.py` named builder로만 만든다.

**Files:**

- Create `report/model.py`        (metric JSON → report model; verdict/issue 주입)
- Create `report/charts.py`        (부록 C 규격 — 차트 형식의 단일 소스)
- Create `report/html.py`          (Jinja2 render)
- Create `report/template.html.j2` (부록 B 규격 — verdict 슬롯/캡션/issue/tier 배지)
- Create `report/style.css`        (color class 단일 정의)
- Create `tests/unit/test_report_model.py`
- Create `tests/integration/test_render_report.py`
- Create `tests/unit/test_quality_gate.py`   (v2 — 아래 gate 강제)

**Steps:**

- [ ] model.py: 각 섹션에 narrative.py의 verdict 문장 주입.
- [ ] charts.py: 부록 C의 named builder 구현(엔진은 데이터만 전달).
- [ ] §0~§8 렌더. 각 차트는 `{html, caption}`을 받아 캡션과 함께 출력.
- [ ] §3 tier 배지, §5 cluster CDF, §6 active-span 음영, scatter r 표기 반영.
- [ ] issue object를 dashboard top-issue로 렌더(headline+comparison+cross_ref).
- [ ] 단일 HTML 파일 출력.

**Exit Criteria (v2 Quality Gate — 전 항목 통과 필수):**

- [ ] §2–§7 각 섹션에 verdict 문장이 1개 이상 존재.
- [ ] dashboard top issues가 issue object schema 충족(headline + comparison/next_step).
- [ ] 강조 수치에 comparison 문자열 동반(정규식 `×|vs|exceeds|of ` 검사).
- [ ] 각 그래프에 캡션 존재(빈 캡션 0건).
- [ ] 모든 N/A에 사유 문자열 동반(빈 N/A 0건).
- [ ] §5↔§6 cross_ref anchor 최소 1개.
- [ ] PMU 없을 때 cycle/inst 열이 숫자가 아님(조작 방지).
- [ ] severity 색 매핑 일관(critical=빨강 only 등).
- [ ] `out/<job>/report.html` 단일 파일, nav §0–§8 포함, HW badge 5개 포함.

### Phase 10. Device Trace Capture and Quality Check

**Goal:** 실제 device trace dump와 trace quality check를 지원하고, PMU/HW/clock signal이 부족할 때 보완 `pbtx`를 제안한다.

**Files:**

- Create `device/capture.py`
- Create `configs/perfetto/camera_trace_config.pbtx`
- Create `docs/TRACE_CAPTURE.md`
- Create `trace/quality.py`
- Create `tests/unit/test_trace_quality.py`

**Steps:**

- [ ] Add recommended Perfetto config.
- [ ] Document adb capture commands.
- [ ] Implement `check-trace` command.
- [ ] Generate missing signal recommendations.
- [ ] Generate PMU-specific recommendation when `linux.perf`, cycle event, instruction event, or thread mapping is missing.

**Verification:**

```powershell
python -m pytest tests/unit/test_trace_quality.py -v
python -m soc_perfetto_analyzer.cli check-trace `
  --trace samples/<provided>.perfetto-trace `
  --scenario configs/scenarios/camera_preview_30fps.yaml `
  --rules configs/rules/vendor_generic.yaml
```

**Exit Criteria:**

- Missing scheduler/PMU/cpu_freq/HW counter signals produce actionable recommendations.
- PMU gaps include a concrete `linux.perf` `pbtx` snippet or patch suggestion.

### Phase 11. Sample Calibration and Golden Regression

**Goal:** 제공된 sample trace 기준으로 analyzer output을 calibration하고 regression으로 고정한다.

**Files:**

- Create `tests/fixtures/sample_catalog.yaml`
- Create `tests/golden/<sample>/expected_summary.json`
- Create `tests/integration/test_camera_sample.py`
- Create `docs/CALIBRATION.md`

**Steps:**

- [ ] Add sample catalog with scenario/rules/expected output.
- [ ] Add tolerance-based metric comparison.
- [ ] Add golden report summary.
- [ ] Document manual Perfetto UI cross-check procedure.

**Verification:**

```powershell
python -m pytest tests/integration/test_camera_sample.py -v
```

**Exit Criteria:**

- Known sample trace produces stable expected summary.
- Calibration doc explains any accepted tolerance.

## 11. Milestone Order

| Milestone | Phases | User-visible result |
|---|---|---|
| M0 Bootstrap | Phase 0 | CLI skeleton and config samples |
| M1 Trace Audit MVP | Phase 1-2 | capability/inventory JSON and dry-run target matching |
| M2 Runtime/Jitter MVP | Phase 3-4 | R3 thread runtime and wakeup jitter metrics |
| M3 HW Usage MVP | Phase 5 | R1 HW usage map |
| M4 SW Portion MVP | Phase 6 | R2 PMU cycle/inst primary metrics plus time% fallback |
| M5 Clock Influence MVP | Phase 7 | R4 CPU clock correlation |
| M6 Issue/Contention MVP | Phase 8 | top issues and contention candidates |
| M7 HTML Report MVP | Phase 9 | report matching `report_sample.html` format |
| M8 Capture/Regression | Phase 10-11 | repeatable trace capture and golden tests |

Recommended implementation order:

```text
M0 -> M1 -> M2 -> M3 -> M4 -> M5 -> M6 -> M7 -> M8
```

Reason:

- Capability and matching must come before metric claims.
- Runtime/jitter is the most reliable immediate value.
- HW usage and SW portion need calibrated rules.
- HTML report should render real metric JSON, not hard-coded mock data.

## 12. Verification Strategy

### 12.1 Unit Tests

Must cover:

- YAML schema validation
- regex/glob matching
- duplicate category policy
- percentile/CoV/MAD calculation
- wakeup-running pairing
- HW confidence aggregation
- PMU quality gate and recommendation policy
- CPU freq overlap
- issue ranking
- report model consistency

### 12.2 Synthetic Table Tests

Use synthetic trace-like tables for:

- wakeup followed by migration
- multiple wakeups before running
- missing wakeup
- overlapping HW intervals
- missing CPU freq counter
- marker absent period fallback
- weak-only HW evidence
- PMU present vs absent
- category duplicate match

### 12.3 Integration Tests

Use real sample trace when provided:

- capability summary
- HW usage summary
- matched thread count
- top SW running consumers
- wakeup p95/p99
- CPU clock residency
- report section existence
- `report.json` schema

### 12.4 Manual Cross-check

First sample report must be reviewed against Perfetto UI:

- selected analysis window
- thread runtime distribution for at least 2 target threads
- wakeup latency outlier sample
- CPU frequency timeline
- HW evidence examples
- period/FPS estimate

## 13. Trace Capture Guidance

Recommended capture must include:

| Signal | Why |
|---|---|
| `sched/sched_switch` | runtime and CPU assignment |
| `sched/sched_wakeup` or `sched/sched_waking` | wakeup latency |
| `power/cpu_frequency` | CPU clock analysis |
| `power/cpu_idle` | idle/throttle context |
| IRQ/ftrace events | HW usage evidence |
| HW util/freq counters | HW utilization and confidence |
| Android atrace categories: camera/gfx/view/sched/freq/idle/binder_driver/hal/video/audio | framework/HAL slices |
| `linux.perf` instructions/cpu-cycles | primary R2 cycle/instruction/IPC |
| vsync/display events | display period |
| camera shot/SOF/EOF markers if available | camera period confidence |

### 13.1 Quality Checker Behavior

```text
if sched_switch missing:
  R3 runtime and R2 time% unavailable
if sched_wakeup/sched_waking missing:
  wakeup jitter unavailable
if cpu_frequency missing:
  R4 CPU clock unavailable
if linux.perf missing:
  cycle/inst/IPC unavailable; recommend linux.perf pbtx additions
if linux.perf present but HW_CPU_CYCLES or HW_INSTRUCTIONS missing:
  cycle/inst/IPC incomplete; recommend adding missing followers
if linux.perf samples cannot map to thread/process:
  cycle/inst attribution incomplete; recommend process_stats and sched sources
if HW-specific counter/ftrace missing:
  HW usage confidence may drop
if marker missing:
  period detection falls back to heuristic
```

### 13.2 PMU `pbtx` Baseline Recommendation

Perfetto 공식 `linux.perf` 예시는 timebase event와 follower counter를 함께 사용해 per-CPU perf counter를 sample한다. Analyzer의 `check-trace`는 PMU data가 부족할 때 아래 baseline block 또는 기존 config에 대한 equivalent patch를 제안한다.

```protobuf
buffers {
  size_kb: 40960
  fill_policy: DISCARD
}

data_sources {
  config {
    name: "linux.perf"
    perf_event_config {
      timebase {
        frequency: 1000
        counter: SW_CPU_CLOCK
        timestamp_clock: PERF_CLOCK_MONOTONIC
      }
      followers {
        counter: HW_CPU_CYCLES
      }
      followers {
        counter: HW_INSTRUCTIONS
      }
    }
  }
}

data_sources {
  config {
    name: "linux.ftrace"
    ftrace_config {
      ftrace_events: "sched/sched_switch"
      ftrace_events: "sched/sched_waking"
      ftrace_events: "power/cpu_frequency"
      ftrace_events: "power/cpu_idle"
    }
  }
}

data_sources {
  config {
    name: "linux.process_stats"
    process_stats_config {
      scan_all_processes_on_start: true
    }
  }
}
```

Notes:

- `frequency: 1000` is a starting point. The capture owner may lower it if overhead or trace size is too high.
- `HW_CPU_CYCLES` and `HW_INSTRUCTIONS` are required for R2 cycle/instruction/IPC.
- `sched_switch` and process stats are required to attribute PMU samples back to target threads.
- Device/kernel permission failures must be reported as trace-quality issues, not analyzer bugs.

### 13.3 `check-trace` Recommendation Output

When PMU is missing or incomplete, `check-trace` should print a concise actionable message:

```text
PMU status: incomplete
Missing: HW_INSTRUCTIONS follower
Impact: R2 instruction% and IPC unavailable. cycle% remains available.
Recommended pbtx addition:
  data_sources { config { name: "linux.perf" perf_event_config { followers { counter: HW_INSTRUCTIONS } } } }
```

For multiple missing signals, output should group recommendations by impact:

- `Required for R2 PMU portion`
- `Required for R3 scheduler jitter`
- `Required for R4 CPU clock`
- `Improves R1 HW confidence`

## 14. Agent Prompts

### 14.0 Quality Constraints (모든 agent prompt에 공통 삽입 — v2)

```text
QUALITY CONSTRAINTS (필수):
- 각 report 섹션은 verdict 문장으로 시작한다 (narrative.py 규칙 사용, 자유작문 금지).
- 분포/시계열/상관은 표가 아니라 그래프가 1차 표현이다 (charts.py named builder만 사용).
- 모든 강조 수치는 baseline/임계/분모와 함께 표시한다 (comparison 문자열).
- 모든 N/A는 사유와 next-step을 동반한다.
- 인과 단정 금지: thermal 확증 없으면 "overlap/inferred"만.
- 완료 전 §15.1 Quality Gate 전 항목을 self-check 하고 결과를 보고한다.
```

### 14.05 Narrative Agent (v2 — new)

```text
Implement Phase 8.5 only.
Goal: metric JSON -> deterministic verdict sentences + issue objects + baselines.
Use §6.4 verdict templates and §9.10 issue rule set. No free-form writing.
Verify:
  pytest tests/unit/test_baseline.py tests/unit/test_narrative.py -v
  - same input -> byte-identical output (determinism)
  - every issue has comparison or next_step
Stop after Phase 8.5.
```



### 14.1 Bootstrap Agent

```text
Read D:/YHJOO/SOC_Perfetto_Analyzer/soc_perfetto_analyzer_integrated_plan.md.
Implement Phase 0 only.

Constraints:
- Do not implement trace analysis logic yet.
- Create Python package skeleton and CLI help.
- Create scenario/rule YAML examples from the plan.
- Keep vendor-private decoding out of common code.

Verify:
python -m soc_perfetto_analyzer.cli --help
python -m soc_perfetto_analyzer.cli inspect --help
python -m soc_perfetto_analyzer.cli analyze --help
python -m soc_perfetto_analyzer.cli check-trace --help
python -m soc_perfetto_analyzer.cli render-report --help

Stop after Phase 0 and report changed files plus verification output.
```

### 14.2 Trace Audit Agent

```text
Read D:/YHJOO/SOC_Perfetto_Analyzer/soc_perfetto_analyzer_integrated_plan.md.
Implement Phase 1 only.

Goal:
- Open a Perfetto trace through TraceProcessor.
- Extract inventory and capability/integrity JSON.
- Do not compute HW usage or runtime metrics yet.

Verify:
python -m pytest tests/unit/test_capability.py -v
python -m soc_perfetto_analyzer.cli inspect --trace samples/<provided>.perfetto-trace --out out/inspect_sample

Stop after Phase 1.
```

### 14.3 Runtime/Jitter Agent

```text
Read D:/YHJOO/SOC_Perfetto_Analyzer/soc_perfetto_analyzer_integrated_plan.md.
Implement Phases 2 and 3 only.

Goal:
- Load YAML.
- Resolve category/thread targets.
- Compute running span stats and wakeup jitter.

Verify:
python -m pytest tests/unit/test_config_loader.py tests/unit/test_category_matching.py tests/unit/test_running_spans.py tests/unit/test_wakeup_jitter.py -v
python -m soc_perfetto_analyzer.cli analyze --trace samples/<provided>.perfetto-trace --scenario configs/scenarios/camera_preview_30fps.yaml --rules configs/rules/vendor_generic.yaml --out out/runtime_jitter

Stop before HW detection.
```

### 14.4 HW Usage Agent

```text
Read D:/YHJOO/SOC_Perfetto_Analyzer/soc_perfetto_analyzer_integrated_plan.md.
Implement Phase 5 only.

Goal:
- Detect GPU/DPU/CODEC/ISP/NPU usage from rule-based evidence.
- Produce hardware_usage.json.

Constraints:
- Do not hard-code private vendor tracepoint names in Python logic.
- Use YAML rules for vendor naming.

Verify:
python -m pytest tests/unit/test_hardware_detector.py -v
python -m soc_perfetto_analyzer.cli analyze --trace samples/<provided>.perfetto-trace --scenario configs/scenarios/camera_preview_30fps.yaml --rules configs/rules/vendor_generic.yaml --out out/hw_usage

Stop after Phase 5.
```

### 14.5 Report Renderer Agent

```text
Read D:/YHJOO/SOC_Perfetto_Analyzer/soc_perfetto_analyzer_integrated_plan.md.
Implement Phase 9 only.

Goal:
- Render metric JSON into a self-contained HTML report matching report_sample.html section structure and visual density.

Required sections:
- §0 Executive dashboard
- §1 Capability & integrity audit
- §2 HW usage map
- §3 HW SW portion
- §4 Thread runtime profile
- §5 Wakeup / scheduling jitter
- §6 CPU clock & influence
- §7 Contention attribution
- §8 Appendix

Verify:
python -m pytest tests/unit/test_report_model.py tests/integration/test_render_report.py -v
python -m soc_perfetto_analyzer.cli render-report --metrics out/<job> --out out/<job>/report.html

Stop after Phase 9.
```

## 15. Definition of Done

MVP complete means:

1. Analyzer accepts a provided camera scenario Perfetto trace.
2. Analyzer emits `report.html` and `report.json`.
3. Report uses the same section structure and compact dashboard style as `report_sample.html`.
4. R1 HW usage verdict exists for GPU, DPU, CODEC, ISP, NPU.
5. R2 CPU-side SW portion is shown with explicit denominator.
6. PMU sample이 있으면 cycle/instruction/IPC가 R2 primary metric으로 표시된다.
7. PMU absence/incomplete 상태에서는 cycle/instruction/IPC가 N/A로 표시되고 `pbtx` 보완안이 제안된다.
8. R3 category/thread runtime distribution includes p50/p95/p99/CoV/MAD.
9. R3 wakeup jitter includes p50/p95/p99/max and outlier samples.
10. R4 CPU clock analysis includes core/cluster residency and influence candidates.
11. Capability audit explains every N/A or low-confidence metric.
12. Appendix lists unmatched and ambiguous config patterns.
13. At least one sample trace has golden regression coverage.
14. Manual Perfetto UI cross-check has been documented for the first sample.

### 15.1 v2 Report Quality Gate (Phase 9 exit — automatic)

15. 각 섹션(§2–§7)에 verdict 문장이 존재한다.
16. dashboard top issues가 issue object schema(headline + comparison/next_step)를 만족한다.
17. 모든 강조 수치가 baseline/임계/분모 comparison과 함께 표시된다.
18. 모든 그래프에 캡션이 있고, 모든 N/A에 사유가 있다.
19. §5↔§6 cross-reference anchor가 최소 1개 존재한다.
20. PMU 부재 시 cycle/inst 칸이 숫자가 아니라 N/A이며 tier 배지가 표시된다.
21. 모든 차트가 `charts.py` named builder를 통해 생성되어 형식이 일관된다.

### 15.2 v2 Manual Review (CALIBRATION.md)

- 비전문가 1명이 dashboard만 보고 "무엇이 문제인지" 30초 내 진술 가능.
- SoC 엔지니어가 §5 outlier 1건을 Perfetto UI 좌표로 찾아 확인 가능.
- verdict 문장이 표의 숫자와 모순 없음.

## 16. Immediate Next Step

Start with Phase 0, then provide a sample trace for Phase 1.

If no sample trace is available yet:

- Phase 0 can still be completed.
- Phase 1 can implement backend abstraction and clear "trace required" error paths.
- Runtime/HW metrics should wait until a trace or synthetic fixture exists.

Recommended first command for the implementation agent:

```text
Read D:/YHJOO/SOC_Perfetto_Analyzer/soc_perfetto_analyzer_integrated_plan.md and implement Phase 0 only.
```


---

## Appendix A — Why v2 (이전 report 품질 문제의 진단)

이전 산출 report가 빈약했던 원인은 다음과 같이 진단되었고, 본 통합본이 각각을 해소한다.

| 결함 | 증상 | 본 통합본의 해소 위치 |
|---|---|---|
| F1 "필드 나열"만 | 숫자 표만, "그래서 뭐?" 없음 | §1.5 doctrine, §6.4 verdict-first |
| F2 시각화 사양 부재 | 그래프 없음/무의미 | §6.4 viz 표, 부록 C charts.py |
| F3 narrative 규칙 부재 | issue 기계적 나열 | §9.10 issue object, Phase 8.5 |
| F4 섹션 단절 | story 없음 | P6 cross-ref, §6.4 cross_ref 열 |
| F5 DoD가 "필드 존재"만 | 해석 가능성 미검증 | §15.1 Quality Gate |
| F6 baseline 부재 | 좋은지 나쁜지 판단 불가 | §9.11 Baseline Model |
| F7 위계 부재 | 모든 필드 동등 | P5, §6.4 collapse 규칙 |

한 줄: 이전 계획은 "데이터를 정확히 추출"하지만 "분석을 보여주지 못했다". v2는
**말하게(narrative)·그리게(visualization)·비교하게(baseline)·검증하게(quality gate)** 한다.

---

## Appendix B — Report Template 규격 (`report/template.html.j2`)

아래는 Phase 9가 그대로 사용할 **참조 구현**이다. verdict 슬롯(`.verdict`),
차트 캡션 슬롯(`.fig .cap`), issue object 렌더, PMU tier 배지(`.pill.tier-*`),
cross-ref 링크를 포함한다. data contract는 `report/model.py`가 채운다.

```html
<!DOCTYPE html>
<html lang="en">
{#
  SoC Perfetto Analyzer — HTML report skeleton v2
  Encodes the v2 supplement: verdict-first sections (P1), mandatory chart
  captions (D), issue objects (C.2), PMU tier badges (H), cross-refs (P6).
  Data contract: see report_model.py. Charts arrive pre-rendered from
  charts.py as {html, caption} pairs — the template never styles a chart.
#}
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{{ r.meta.scenario }} — SoC Perfetto Report</title>
<style>
  :root{
    --bg:#0f1115; --surface:#171a21; --surface2:#1e222b; --line:#2a2f3a;
    --txt:#e6e8ee; --txt2:#9aa2b1; --txt3:#6b7280;
    --ok:#2dd4a7; --warn:#f0b429; --bad:#f0625b;
    --na:#5b6270; --info:#5b9bf0; --accent:#7c83ff;
    --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    --radius:10px;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);font-family:var(--sans);
       font-size:15px;line-height:1.6;-webkit-font-smoothing:antialiased}
  a{color:var(--info);text-decoration:none} a:hover{text-decoration:underline}
  code{font-family:var(--mono);font-size:.88em;background:var(--surface2);
       padding:1px 5px;border-radius:4px;color:#cdd3e0}
  h1{font-size:22px;font-weight:600;margin:0}
  h2{font-size:18px;font-weight:600;margin:0 0 4px}
  h3{font-size:14px;font-weight:600;margin:14px 0 8px;color:var(--txt2)}
  .wrap{display:flex;max-width:1180px;margin:0 auto}
  nav{position:sticky;top:0;align-self:flex-start;width:190px;height:100vh;
      padding:22px 14px;border-right:1px solid var(--line);flex:none;overflow:auto}
  nav .brand{font-size:13px;color:var(--txt3);letter-spacing:.04em;
             text-transform:uppercase;margin-bottom:14px}
  nav a{display:block;color:var(--txt2);padding:6px 10px;border-radius:6px;
        font-size:14px;margin-bottom:2px}
  nav a:hover{background:var(--surface);color:var(--txt);text-decoration:none}
  main{flex:1;min-width:0;padding:26px 34px 80px}
  header.top{display:flex;justify-content:space-between;align-items:flex-end;
             padding-bottom:18px;border-bottom:1px solid var(--line);margin-bottom:24px}
  .sub{color:var(--txt2);font-size:13px;margin-top:4px}
  section{margin:34px 0;scroll-margin-top:20px}
  .sec-head{display:flex;align-items:center;gap:10px;margin-bottom:8px}
  .sec-head .id{font-family:var(--mono);color:var(--txt3);font-size:13px}
  /* P1: verdict line — the one-sentence answer that opens every section */
  .verdict{font-size:14.5px;color:var(--txt);background:var(--surface);
           border-left:3px solid var(--accent);padding:9px 14px;
           border-radius:0 8px 8px 0;margin:6px 0 14px}
  .note{background:var(--surface);border-left:3px solid var(--info);
        padding:10px 14px;border-radius:0 8px 8px 0;color:var(--txt2);
        font-size:13.5px;margin:12px 0}
  .note.warn{border-left-color:var(--warn)}
  .note.bad{border-left-color:var(--bad)}
  .dot{display:inline-block;width:9px;height:9px;border-radius:50%;vertical-align:middle}
  .s-ok{background:var(--ok)} .s-warn{background:var(--warn)}
  .s-bad{background:var(--bad)} .s-na{background:var(--na)} .s-info{background:var(--info)}
  .t-ok{color:var(--ok)} .t-warn{color:var(--warn)}
  .t-bad{color:var(--bad)} .t-na{color:var(--na)} .t-info{color:var(--info)}
  .pill{display:inline-block;font-size:11px;padding:2px 9px;border-radius:20px;
        background:var(--surface2);color:var(--txt2)}
  .pill.tier-measured{background:#11302a;color:var(--ok)}
  .pill.tier-estimated{background:#3a2f12;color:var(--warn)}
  .pill.tier-time_only{background:#22262f;color:var(--txt2)}
  .grid{display:grid;gap:12px}
  .badges{grid-template-columns:repeat(auto-fit,minmax(120px,1fr))}
  .kpis{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
  .card{background:var(--surface);border:1px solid var(--line);
        border-radius:var(--radius);padding:14px}
  .card .lab{font-size:12px;color:var(--txt2)}
  .card .big{font-size:22px;font-weight:600;margin-top:3px}
  .badge{text-align:center}
  .badge .hw{font-size:13px;color:var(--txt2);margin-bottom:4px}
  .badge .st{font-size:12.5px;font-weight:600}
  .badge .pc{font-size:20px;font-weight:600;margin-top:2px}
  table{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:6px}
  th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
  th{color:var(--txt2);font-weight:500;font-size:12px;
     text-transform:uppercase;letter-spacing:.03em}
  td.num,th.num{text-align:right;font-family:var(--mono)}
  tr:hover td{background:var(--surface)}
  .muted{color:var(--txt3)}
  details{margin-top:8px} summary{cursor:pointer;color:var(--txt2);font-size:13.5px;padding:4px 0}
  /* chart block: figure + MANDATORY caption (D common rule 3) */
  .fig{margin:10px 0;background:var(--surface);border:1px solid var(--line);
       border-radius:var(--radius);padding:10px 10px 4px}
  .fig .cap{font-size:12px;color:var(--txt3);padding:2px 4px 6px;line-height:1.45}
  /* issue object (C.2) */
  .issue{display:flex;gap:10px;align-items:flex-start;padding:8px 0;
         border-bottom:1px solid var(--line);font-size:13.5px}
  .issue:last-child{border-bottom:0}
  .issue .body{flex:1}
  .issue .hl{color:var(--txt);font-weight:500}
  .issue .meta{color:var(--txt3);font-size:12.5px;margin-top:2px}
  .issue .xref{font-size:12px}
  .foot{margin-top:50px;padding-top:16px;border-top:1px solid var(--line);
        color:var(--txt3);font-size:12.5px}
</style>
</head>
<body>
<div class="wrap">
  <nav>
    <div class="brand">SoC Trace</div>
    <a href="#s0">§0 Dashboard</a>
    <a href="#s1">§1 Capability</a>
    <a href="#s2">§2 HW Usage</a>
    <a href="#s3">§3 HW Portion</a>
    <a href="#s4">§4 Runtime</a>
    <a href="#s5">§5 Jitter</a>
    <a href="#s6">§6 CPU Clock</a>
    {% if r.contention %}<a href="#s7">§7 Contention</a>{% endif %}
    <a href="#s8">§8 Appendix</a>
  </nav>
  <main>
  <header class="top">
    <div>
      <h1>SoC Multimedia Trace Report</h1>
      <div class="sub"><code>{{ r.meta.scenario }}</code> · {{ r.meta.device }} ·
        {{ r.meta.soc }} · {{ r.meta.duration_s }}s · window {{ r.meta.window_s }}</div>
    </div>
    <div class="sub">generated {{ r.meta.generated }}</div>
  </header>

  {# ===================== §0 DASHBOARD ===================== #}
  <section id="s0">
    <div class="sec-head"><span class="id">§0</span><h2>Executive dashboard</h2></div>
    <div class="grid badges">
      {% for hw in r.hw_usage %}
      <div class="card badge">
        <div class="hw">{{ hw.name }}</div>
        <div class="st t-{{ hw.status }}"><span class="dot s-{{ hw.status }}"></span> {{ hw.state_label }}</div>
        <div class="pc {% if hw.status=='na' %}t-na{% endif %}">{{ hw.portion_label }}</div>
      </div>
      {% endfor %}
    </div>
    <div class="grid kpis" style="margin-top:12px">
      {% for k in r.kpis %}
      <div class="card"><div class="lab">{{ k.label }}</div>
        <div class="big">{{ k.value }} <span class="t-{{ k.status }}" style="font-size:14px">●</span></div>
        {% if k.sub %}<div class="sub" style="margin-top:0">{{ k.sub }}</div>{% endif %}
      </div>
      {% endfor %}
    </div>
    <div class="card" style="margin-top:12px">
      <div class="lab" style="margin-bottom:4px">Top issues</div>
      {% for it in r.top_issues %}
      <div class="issue">
        <span class="t-{{ it.severity }}" style="margin-top:2px">●</span>
        <div class="body">
          <div class="hl">{{ it.headline }}</div>
          <div class="meta">
            {% if it.comparison %}{{ it.comparison }}{% endif %}
            {% if it.confidence %} · <span class="muted">{{ it.confidence }}</span>{% endif %}
            {% if it.next_step %} · {{ it.next_step }}{% endif %}
          </div>
          {% if it.cross_ref %}<div class="xref"><a href="#{{ it.cross_ref_anchor }}">→ {{ it.cross_ref }}</a></div>{% endif %}
        </div>
      </div>
      {% else %}<div class="muted">No issues above threshold.</div>{% endfor %}
    </div>
  </section>

  {# ===================== §1 CAPABILITY ===================== #}
  <section id="s1">
    <div class="sec-head"><span class="id">§1</span><h2>Capability &amp; integrity audit</h2></div>
    <div class="verdict">{{ r.verdicts.capability }}</div>
    <h3>Data sources</h3>
    <table><thead><tr><th>Source</th><th>Present</th><th>Affects</th></tr></thead><tbody>
      {% for d in r.capability.sources %}
      <tr><td><code>{{ d.name }}</code></td>
        <td><span class="dot s-{{ d.status }}"></span> <span class="t-{{ d.status }}">{{ d.present }}</span></td>
        <td class="muted">{{ d.affects }}</td></tr>
      {% endfor %}
    </tbody></table>
    <h3>Integrity</h3>
    <table><thead><tr><th>Item</th><th class="num">Value</th><th>Status</th></tr></thead><tbody>
      {% for i in r.capability.integrity %}
      <tr><td>{{ i.item }}</td><td class="num">{{ i.value }}</td><td><span class="dot s-{{ i.status }}"></span></td></tr>
      {% endfor %}
    </tbody></table>
  </section>

  {# ===================== §2 HW USAGE ===================== #}
  <section id="s2">
    <div class="sec-head"><span class="id">§2</span><h2>HW usage map</h2><span class="pill">R1</span></div>
    <div class="verdict">{{ r.verdicts.hw }}</div>
    {% if r.figures.hw_map %}<div class="fig">{{ r.figures.hw_map.html|safe }}
      <div class="cap">{{ r.figures.hw_map.caption }}</div></div>{% endif %}
    <details open><summary>Detection evidence</summary>
    <table><thead><tr><th>HW</th><th>Verdict</th><th>Confidence</th>
      <th class="num">IRQ</th><th class="num">Driver rt</th><th>Util</th><th>Freq</th></tr></thead><tbody>
      {% for hw in r.hw_usage %}
      <tr><td>{{ hw.name }}</td>
        <td><span class="dot s-{{ hw.status }}"></span> <span class="t-{{ hw.status }}">{{ hw.state_label }}</span></td>
        <td class="muted">{{ hw.confidence }}</td>
        <td class="num">{{ hw.irq_count }}</td><td class="num">{{ hw.driver_runtime }}</td>
        <td>{{ hw.util_counter }}</td><td>{{ hw.freq_counter }}</td></tr>
      {% endfor %}
    </tbody></table></details>
    {% if r.hw_usage_note %}<div class="note warn">{{ r.hw_usage_note }}</div>{% endif %}
  </section>

  {# ===================== §3 HW SW PORTION ===================== #}
  <section id="s3">
    <div class="sec-head"><span class="id">§3</span><h2>HW SW portion</h2>
      <span class="pill">R2</span>
      <span class="pill tier-{{ r.portion.tier }}">{{ r.portion.tier_label }}</span></div>
    <div class="verdict">{{ r.verdicts.portion }}</div>
    <div class="note">Portion = <b>CPU-side SW cost</b> to drive each HW, not HW utilization.
      Denominator: <code>{{ r.portion.denominator }}</code>.</div>
    {% if r.figures.portion %}<div class="fig">{{ r.figures.portion.html|safe }}
      <div class="cap">{{ r.figures.portion.caption }}</div></div>{% endif %}
    <table><thead><tr><th>HW</th><th class="num">Time %</th>
      <th class="num">Cycle %</th><th class="num">Inst %</th><th class="num">IPC</th></tr></thead><tbody>
      {% for p in r.portion.rows %}
      <tr><td>{{ p.name }}</td><td class="num">{{ p.time_pct }}</td>
        <td class="num {% if p.pmu_na %}t-na{% endif %}">{{ p.cycle_pct }}</td>
        <td class="num {% if p.pmu_na %}t-na{% endif %}">{{ p.inst_pct }}</td>
        <td class="num {% if p.pmu_na %}t-na{% endif %}">{{ p.ipc }}</td></tr>
      {% endfor %}
    </tbody></table>
    {% if r.portion.tier == 'time_only' %}
      <div class="note warn">PMU absent → cycle/inst/IPC N/A. {{ r.portion.pbtx_hint }}</div>
    {% elif r.portion.tier == 'estimated' %}
      <div class="note warn">Cycle % is <b>estimated</b> from freq×runtime (megacycles). Instruction%/IPC require PMU and remain N/A.</div>
    {% else %}
      <div class="sub">* cycle/inst are PMU-sample estimations (multiplexing-scaled).</div>
    {% endif %}
  </section>

  {# ===================== §4 RUNTIME ===================== #}
  <section id="s4">
    <div class="sec-head"><span class="id">§4</span><h2>Thread runtime profile</h2><span class="pill">R3a</span></div>
    <div class="verdict">{{ r.verdicts.runtime }}</div>
    {% if r.figures.runtime_box %}<div class="fig">{{ r.figures.runtime_box.html|safe }}
      <div class="cap">{{ r.figures.runtime_box.caption }}</div></div>{% endif %}
    <details><summary>Per-thread statistics ({{ r.runtime.rows|length }} threads) — µs</summary>
    <table><thead><tr><th>Category</th><th>Thread</th><th class="num">N</th>
      <th class="num">min</th><th class="num">avg</th><th class="num">p50</th>
      <th class="num">p95</th><th class="num">p99</th><th class="num">max</th>
      <th class="num">CoV</th><th>core b/m/L</th></tr></thead><tbody>
      {% for t in r.runtime.rows %}
      <tr><td class="muted">{{ t.category }}</td><td><code>{{ t.thread }}</code></td>
        <td class="num">{{ t.n }}</td><td class="num">{{ t.min }}</td><td class="num">{{ t.avg }}</td>
        <td class="num">{{ t.p50 }}</td><td class="num">{{ t.p95 }}</td><td class="num">{{ t.p99 }}</td>
        <td class="num">{{ t.max }}</td><td class="num t-{{ t.cov_status }}">{{ t.cov }}</td>
        <td class="muted">{{ t.core_mix }}</td></tr>
      {% endfor %}
    </tbody></table></details>
  </section>

  {# ===================== §5 JITTER ===================== #}
  <section id="s5">
    <div class="sec-head"><span class="id">§5</span><h2>Wakeup / scheduling jitter</h2><span class="pill">R3b</span></div>
    {% if r.capability.has_waking %}
      <div class="verdict">{{ r.verdicts.jitter }}</div>
      {% if r.figures.wakeup_cdf %}<div class="fig">{{ r.figures.wakeup_cdf.html|safe }}
        <div class="cap">{{ r.figures.wakeup_cdf.caption }}</div></div>{% endif %}
      {% if r.figures.jitter_rank %}<div class="fig">{{ r.figures.jitter_rank.html|safe }}
        <div class="cap">{{ r.figures.jitter_rank.caption }}</div></div>{% endif %}
      {% if r.figures.interval_strip %}<div class="fig">{{ r.figures.interval_strip.html|safe }}
        <div class="cap">{{ r.figures.interval_strip.caption }}</div></div>{% endif %}
      <details><summary>Per-thread jitter metrics</summary>
      <table><thead><tr><th>Thread</th><th class="num">p50</th><th class="num">p95</th>
        <th class="num">p99</th><th class="num">CoV</th><th class="num">MAD</th>
        <th class="num">interval σ</th><th>verdict</th><th>see</th></tr></thead><tbody>
        {% for j in r.jitter.rows %}
        <tr><td><code>{{ j.thread }}</code></td><td class="num">{{ j.p50 }}</td>
          <td class="num">{{ j.p95 }}</td><td class="num t-{{ j.p99_status }}">{{ j.p99 }}</td>
          <td class="num">{{ j.cov }}</td><td class="num">{{ j.mad }}</td>
          <td class="num">{{ j.interval_sigma }}</td>
          <td><span class="dot s-{{ j.status }}"></span></td>
          <td>{% if j.cross_ref_anchor %}<a href="#{{ j.cross_ref_anchor }}" class="xref">{{ j.cross_ref }}</a>{% endif %}</td></tr>
        {% endfor %}
      </tbody></table></details>
    {% else %}
      <div class="note bad"><code>sched_waking</code> absent — wakeup latency &amp; jitter
        unmeasurable (core R3 capability). {{ r.jitter.pbtx_hint }}</div>
    {% endif %}
  </section>

  {# ===================== §6 CPU CLOCK ===================== #}
  <section id="s6">
    <div class="sec-head"><span class="id">§6</span><h2>CPU clock &amp; influence</h2><span class="pill">R4</span></div>
    <div class="verdict">{{ r.verdicts.clock }}</div>
    {% if r.figures.freq_ts %}<div class="fig">{{ r.figures.freq_ts.html|safe }}
      <div class="cap">{{ r.figures.freq_ts.caption }}</div></div>{% endif %}
    {% if r.figures.freq_residency %}<div class="fig">{{ r.figures.freq_residency.html|safe }}
      <div class="cap">{{ r.figures.freq_residency.caption }}</div></div>{% endif %}
    {% if r.figures.freq_corr %}<div class="fig">{{ r.figures.freq_corr.html|safe }}
      <div class="cap">{{ r.figures.freq_corr.caption }}</div></div>{% endif %}
    {% if r.clock.throttle_rows %}
    <details open><summary>Clock-drop events</summary>
    <table><thead><tr><th class="num">t (s)</th><th>cluster</th><th>drop</th>
      <th>concurrent runtime Δ</th><th>thermal?</th></tr></thead><tbody>
      {% for e in r.clock.throttle_rows %}
      <tr><td class="num">{{ e.t }}</td><td>{{ e.cluster }}</td><td>{{ e.drop }}</td>
        <td class="t-warn">{{ e.runtime_delta }}</td><td class="muted">{{ e.thermal }}</td></tr>
      {% endfor %}
    </tbody></table></details>{% endif %}
  </section>

  {# ===================== §7 CONTENTION ===================== #}
  {% if r.contention %}
  <section id="s7">
    <div class="sec-head"><span class="id">§7</span><h2>Contention attribution</h2></div>
    <div class="verdict">{{ r.verdicts.contention }}</div>
    <h3>Top co-runners for <code>{{ r.contention.target }}</code></h3>
    <table><thead><tr><th>Preemptor</th><th class="num">overlap</th><th class="num">count</th></tr></thead><tbody>
      {% for c in r.contention.corunners %}
      <tr><td><code>{{ c.name }}</code></td><td class="num t-{{ c.status }}">{{ c.overlap }}</td>
        <td class="num">{{ c.count }}</td></tr>
      {% endfor %}
    </tbody></table>
    {% if r.figures.waker_chain %}<div class="fig">{{ r.figures.waker_chain.html|safe }}
      <div class="cap">{{ r.figures.waker_chain.caption }}</div></div>{% endif %}
  </section>{% endif %}

  {# ===================== §8 APPENDIX ===================== #}
  <section id="s8">
    <div class="sec-head"><span class="id">§8</span><h2>Appendix</h2></div>
    {% if r.appendix.unmatched %}<h3>Unmatched keywords</h3>
    <div>{% for k in r.appendix.unmatched %}<span class="pill" style="margin:2px">{{ k }}</span>{% endfor %}</div>{% endif %}
    {% if r.appendix.ambiguous %}<h3>Ambiguous matches</h3>
    <div>{% for k in r.appendix.ambiguous %}<span class="pill" style="margin:2px">{{ k }}</span>{% endfor %}</div>{% endif %}
    <h3>Caveats</h3><ul class="sub">{% for c in r.appendix.caveats %}<li>{{ c }}</li>{% endfor %}</ul>
    <h3>Versions</h3><table><tbody>
      {% for k,v in r.appendix.versions.items() %}<tr><td class="muted">{{ k }}</td><td><code>{{ v }}</code></td></tr>{% endfor %}
    </tbody></table>
    <details><summary>Config dump</summary>
      <pre style="background:var(--surface2);padding:12px;border-radius:8px;overflow:auto;font-size:12.5px"><code>{{ r.appendix.config_dump }}</code></pre>
    </details>
  </section>

  <div class="foot">SoC Perfetto Analyzer · denominator: {{ r.portion.denominator }} ·
    window: {{ r.meta.window_s }} · {{ r.meta.generated }}</div>
  </main>
</div>
</body>
</html>

```

---

## Appendix C — Chart Builder 규격 (`report/charts.py`)

**"차트 형식이 미묘하게 다른" 문제의 해결책.** 모든 차트는 이 모듈의 named builder를
통해서만 생성한다. 엔진은 데이터만 전달하고 Plotly layout을 직접 만지지 않는다.
이로써 theme/축/색/캡션/degrade가 단일 소스로 고정된다.

builder 목록:
- `portion_bar(rows, denominator)` — §3 100% stacked, 분모를 제목에
- `runtime_box(threads, baseline_us)` — §4 box + peer baseline 선
- `wakeup_cdf(cluster_samples)` — §5 cluster별 CDF (분리 필수)
- `jitter_rank(rows)` — §5 p99×runnable 정렬 bar, 색=severity
- `interval_strip(intervals_ms, target_ms, thread)` — §5 주기 thread만
- `freq_timeline(cluster_series, active_spans)` — §6 active 음영 overlay
- `freq_residency(clusters, buckets, data)` — §6 grouped bar
- `runtime_vs_freq(freq, runtime, r_value)` — §6 scatter + r 표기

각 builder는 `(html, caption)`을 반환하며 캡션은 필수다. 데이터가 비면
`"no data: <reason>"` placeholder를 반환한다(빈 캔버스 금지).

```python
"""
charts.py — Visualization Contract (v2 supplement §D) codified as code.

WHY THIS FILE EXISTS
--------------------
The report felt inconsistent because each chart was built ad-hoc. This module
is the SINGLE source of chart formatting. Engines pass DATA ONLY; they never
touch Plotly layout. Every chart therefore has identical theme, axis style,
caption handling, and degrade behavior.

RULES (enforced here, not left to the agent):
- One dark theme (THEME). Transparent background. No rainbow palettes.
- Semantic colors only: severity (ok/warn/bad/na), fixed cluster colors,
  fixed HW colors.
- Every chart returns (html, caption). Caption is mandatory.
- Empty data -> a "no data: <reason>" placeholder card, never a blank canvas.
- First emitted figure inlines plotly.js; the rest reuse it (size control).
"""
from __future__ import annotations
import plotly.graph_objects as go

# ----------------------------------------------------------------------------
# THEME — the one place chart appearance is defined
# ----------------------------------------------------------------------------
COL = {
    "ok": "#2dd4a7", "warn": "#f0b429", "bad": "#f0625b", "na": "#5b6270",
    "info": "#5b9bf0", "txt": "#e6e8ee", "txt2": "#9aa2b1", "line": "#2a2f3a",
    "surface": "#171a21",
}
# fixed cluster colors (never change across charts)
CLUSTER_COL = {"little": "#f0b429", "mid": "#5b9bf0", "big": "#2dd4a7"}
# fixed HW colors
HW_COL = {"GPU": "#2dd4a7", "DPU": "#f0b429", "CODEC": "#5b9bf0",
          "ISP": "#7c83ff", "NPU": "#e06c9f", "Other": "#2a2f3a"}
SEV_COL = {"ok": COL["ok"], "warn": COL["warn"], "bad": COL["bad"],
           "info": COL["info"], "na": COL["na"]}

_AXIS = dict(gridcolor=COL["line"], zerolinecolor=COL["line"],
             linecolor=COL["line"], tickfont=dict(size=11, color=COL["txt2"]))

def _layout(height=300, **over):
    base = dict(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=COL["txt2"], size=12,
                  family="-apple-system,Segoe UI,Roboto,sans-serif"),
        margin=dict(l=54, r=20, t=14, b=42), height=height,
        xaxis={**_AXIS}, yaxis={**_AXIS},
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=11),
                    orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1),
        hoverlabel=dict(bgcolor=COL["surface"], font_size=12),
    )
    # deep-merge axis overrides
    for k, v in over.items():
        if k in ("xaxis", "yaxis") and isinstance(v, dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base

_FIRST = {"v": True}
def _emit(fig) -> str:
    inc = "inline" if _FIRST["v"] else False
    _FIRST["v"] = False
    return fig.to_html(full_html=False, include_plotlyjs=inc,
                       config={"displayModeBar": False, "responsive": True})

def reset_plotlyjs():
    """Call once per report before building charts."""
    _FIRST["v"] = True

def _placeholder(reason: str) -> str:
    return (f'<div style="height:120px;display:flex;align-items:center;'
            f'justify-content:center;color:{COL["na"]};font-size:13px;'
            f'border:1px dashed {COL["line"]};border-radius:8px">'
            f'no data: {reason}</div>')

# ============================================================================
# §3  HW SW PORTION  — 100% stacked horizontal bar (donut alt)
#   spec: denominator IN TITLE; HW fixed colors; Other = remainder
# ============================================================================
def portion_bar(rows, denominator: str):
    """rows: [{name, time_pct}] (already includes 'Other')."""
    if not rows:
        return _placeholder("no SW portion (sched data absent)"), ""
    fig = go.Figure()
    for r in rows:
        fig.add_trace(go.Bar(
            y=["SW"], x=[r["time_pct"]], name=r["name"], orientation="h",
            marker_color=HW_COL.get(r["name"], "#888"),
            text=f'{r["name"]} {r["time_pct"]}%', textposition="inside",
            insidetextanchor="middle", textfont=dict(size=11)))
    fig.update_layout(**_layout(
        height=130, barmode="stack", showlegend=False,
        xaxis=dict(title=f"% of {denominator}", range=[0, 100]),
        yaxis=dict(showticklabels=False)))
    cap = (f"HW-driving SW as share of {denominator}. "
           f"This is CPU cost to drive each block — not HW utilization.")
    return _emit(fig), cap

# ============================================================================
# §4  THREAD RUNTIME  — box per thread + baseline line
#   spec: y=runtime µs; baseline = peer p50 overlay; outliers shown
# ============================================================================
def runtime_box(threads, baseline_us=None):
    """threads: [{thread, samples:[us...], severity}]"""
    if not threads:
        return _placeholder("no runtime (sched data absent)"), ""
    fig = go.Figure()
    for t in threads:
        fig.add_trace(go.Box(
            y=t["samples"], name=t["thread"],
            marker_color=SEV_COL.get(t.get("severity", "ok")),
            boxpoints="outliers", line=dict(width=1.3),
            marker=dict(size=3)))
    if baseline_us is not None:
        fig.add_hline(y=baseline_us, line_dash="dash",
                      line_color=COL["txt2"], line_width=1,
                      annotation_text=f"peer p50 {baseline_us}µs",
                      annotation_font=dict(size=10, color=COL["txt2"]),
                      annotation_position="top left")
    fig.update_layout(**_layout(
        height=320, showlegend=False,
        yaxis=dict(title="runtime (µs)")))
    cap = ("Per-thread running-burst distribution. Wider box = less stable; "
           "dots are outliers. Dashed line = stable-peer baseline.")
    return _emit(fig), cap

# ============================================================================
# §5  WAKEUP CDF  — one line per cluster (MANDATORY split)
#   spec: x=latency µs; y=P(x<=t); compare WITHIN a cluster
# ============================================================================
def wakeup_cdf(cluster_samples):
    """cluster_samples: {cluster: [latency_us...]}"""
    series = {k: v for k, v in (cluster_samples or {}).items() if v}
    if not series:
        return _placeholder("sched_waking absent — wakeup latency unmeasurable"), ""
    fig = go.Figure()
    for cl, vals in series.items():
        s = sorted(vals); n = len(s)
        ys = [(i + 1) / n for i in range(n)]
        fig.add_trace(go.Scatter(
            x=s, y=ys, mode="lines", name=cl,
            line=dict(color=CLUSTER_COL.get(cl, "#888"), width=2)))
    fig.update_layout(**_layout(
        height=300,
        xaxis=dict(title="wakeup latency (µs)"),
        yaxis=dict(title="P(x ≤ t)", range=[0, 1])))
    cap = ("Wakeup-latency CDF split by CPU cluster. Little cores are slower "
           "by design — compare a thread against its own cluster, not across.")
    return _emit(fig), cap

# ============================================================================
# §5  JITTER RANKING  — horizontal bar, sorted by p99 x runnable
#   spec: color = severity; label = p99 + runnable%
# ============================================================================
def jitter_rank(rows):
    """rows: [{thread, p99_us, runnable_pct, severity}] (any order)."""
    if not rows:
        return _placeholder("no jitter rows"), ""
    rs = sorted(rows, key=lambda r: r["p99_us"] * max(r["runnable_pct"], 0.1))
    fig = go.Figure(go.Bar(
        y=[r["thread"] for r in rs], x=[r["p99_us"] for r in rs],
        orientation="h",
        marker_color=[SEV_COL.get(r["severity"], COL["na"]) for r in rs],
        text=[f'{r["p99_us"]}µs · runnable {r["runnable_pct"]}%' for r in rs],
        textposition="outside", textfont=dict(size=10)))
    fig.update_layout(**_layout(
        height=60 + 34 * len(rs), showlegend=False,
        xaxis=dict(title="wakeup p99 (µs)"),
        margin=dict(l=130, r=80, t=14, b=42)))
    cap = ("Threads ranked by actionable jitter (p99 × runnable-ratio). "
           "Red = exceeds threshold.")
    return _emit(fig), cap

# ============================================================================
# §5  INTERVAL STRIP  — activation interval vs target period (periodic only)
# ============================================================================
def interval_strip(intervals_ms, target_ms, thread):
    if not intervals_ms:
        return _placeholder("not periodic / no period detected"), ""
    xs = list(range(len(intervals_ms)))
    fig = go.Figure(go.Scatter(
        x=xs, y=intervals_ms, mode="lines",
        line=dict(color=COL["info"], width=1)))
    fig.add_hline(y=target_ms, line_dash="dash", line_color=COL["txt2"],
                  line_width=1, annotation_text=f"target {target_ms}ms",
                  annotation_font=dict(size=10, color=COL["txt2"]))
    fig.update_layout(**_layout(
        height=200,
        xaxis=dict(title=f"activation # ({thread})"),
        yaxis=dict(title="interval (ms)")))
    cap = ("Time between activations vs target period. Spikes above the line "
           "are scheduling jitter.")
    return _emit(fig), cap

# ============================================================================
# §6  FREQ TIMELINE  — cluster lines + target-active shading
# ============================================================================
def freq_timeline(cluster_series, active_spans=None):
    """cluster_series: {cluster: ([t_s...],[ghz...])}; active_spans: [(t0,t1)]"""
    if not cluster_series:
        return _placeholder("cpu_frequency counter absent"), ""
    fig = go.Figure()
    for cl, (ts, gh) in cluster_series.items():
        fig.add_trace(go.Scatter(
            x=ts, y=gh, mode="lines", name=cl,
            line=dict(color=CLUSTER_COL.get(cl, "#888"), width=1.4)))
    for (t0, t1) in (active_spans or []):
        fig.add_vrect(x0=t0, x1=t1, fillcolor=COL["info"], opacity=0.10,
                      line_width=0)
    fig.update_layout(**_layout(
        height=280,
        xaxis=dict(title="time (s)"),
        yaxis=dict(title="freq (GHz)")))
    cap = ("Per-cluster clock over time. Shaded bands = target thread active. "
           "Dips under shading suggest clock-limited runtime.")
    return _emit(fig), cap

# ============================================================================
# §6  RESIDENCY  — grouped bar, freq bucket x cluster
# ============================================================================
def freq_residency(clusters, buckets, data):
    """data: {cluster: [pct per bucket]}"""
    if not data:
        return _placeholder("cpu_frequency counter absent"), ""
    fig = go.Figure()
    for cl in clusters:
        fig.add_trace(go.Bar(name=cl, x=buckets, y=data[cl],
                             marker_color=CLUSTER_COL.get(cl, "#888")))
    fig.update_layout(**_layout(
        height=260, barmode="group",
        xaxis=dict(title="frequency bucket"),
        yaxis=dict(title="residency %")))
    cap = "Share of time each cluster spent at each frequency."
    return _emit(fig), cap

# ============================================================================
# §6  RUNTIME vs FREQ  — scatter + correlation r
# ============================================================================
def runtime_vs_freq(freq_ghz, runtime_us, r_value):
    if not freq_ghz:
        return _placeholder("cpu_frequency counter absent"), ""
    fig = go.Figure(go.Scatter(
        x=freq_ghz, y=runtime_us, mode="markers",
        marker=dict(color=COL["info"], size=6, opacity=0.55)))
    fig.update_layout(**_layout(
        height=280,
        xaxis=dict(title="instant freq (GHz)"),
        yaxis=dict(title="runtime (µs)")))
    sign = "negative" if r_value < -0.3 else ("positive" if r_value > 0.3 else "weak")
    cap = (f"Runtime vs instantaneous frequency (r = {r_value:+.2f}, {sign}). "
           f"Negative correlation suggests DVFS-limited execution.")
    return _emit(fig), cap

```

---

## Appendix D — v2 변경 요약 (기존 plan 대비)

| 위치 | 변경 |
|---|---|
| §1.5 (신규) | Report Authoring Doctrine P1–P7 |
| §6.4 (치환) | "Must show 필드나열" → verdict-first 3단 contract + viz 표 |
| §9.10 (신규) | Issue object schema + rule set |
| §9.11 (신규) | Baseline Model |
| §9.12 (신규) | R2 SW Portion 3-tier (measured/estimated/time-only) |
| Phase 6 | 3-tier 반영 (T2 megacycle 추정 추가) |
| Phase 8 | issue ranking → issue object |
| Phase 8.5 (신규) | Narrative/Baseline 단일 모듈 |
| Phase 9 (강화) | charts.py 강제 + Quality Gate exit |
| §14.0 / §14.05 (신규) | Agent quality constraints + Narrative Agent |
| §15.1 / §15.2 (신규) | Report Quality Gate (auto + manual) |
| 부록 B/C (신규) | template.html.j2 / charts.py 참조 구현 |
