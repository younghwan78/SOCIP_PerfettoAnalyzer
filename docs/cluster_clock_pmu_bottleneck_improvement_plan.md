# Cluster Clock 및 PMU 함수 병목 분석 개선 방안

작성일: 2026-06-07

## 목적

현재 SOCIP Perfetto Analyzer에 다음 두 분석 축을 추가하기 위한 구현 검토와 개선안을 정리한다.

1. Cluster별 CPU frequency를 분석하고, 평균 대비 clock이 상승한 구간에서 원인을 구분한다.
   - 새로운 task 또는 co-runner가 추가되어 cluster 부하가 증가한 경우
   - camera scenario처럼 주기성을 가진 target task들이 몰려 상위 cluster로 migration되며 clock이 증가한 경우
2. `event_config.yaml`에 정의된 thread에 대해 Perfetto profiling 중 ARM telemetry/PMU event가 수집된 경우, thread별 top3 cycle 소모 함수를 찾고 병목 성격과 원인을 report에 추가한다.

이 문서는 구현 코드를 변경하지 않는다. 우선순위, 데이터 계약, 알고리즘, report 반영 위치, 테스트 전략을 정의한다.

## 결론 요약

- 현재 analyzer는 `sched`, `thread_state`, `cpu_frequency`를 사용해 runtime, wakeup jitter, CPU clock chart를 만들지만, clock 상승 원인 attribution은 구현되어 있지 않다.
- 현재 cluster 분류는 `cpu<=3 little`, `4..6 mid`, `>=7 big`으로 하드코딩되어 있다. 샘플 trace에서는 CPU0-1만 max 1.344GHz이고 CPU2-7은 max 2.630GHz로 관측되어, 실제 SoC topology와 불일치할 수 있다. 과제마다 cluster 구성이 다를 수 있으므로 config 기반 override를 1순위로 둔다.
- 현재 `ThreadRuntime`은 `samples_us`, `cpus`, `starts_s`를 병렬 list로 들고 있다. clock 상승 원인을 분석하려면 run burst별 `ts/dur/cpu/cluster/freq/thread/utid`가 하나의 record로 보존되어야 한다.
- 현재 PMU는 `counter_track` 이름으로 존재 여부만 확인한다. PMU가 present이면 `portion_rows`에서 time%를 cycle%/inst%에 그대로 복사하므로, 실제 PMU sample 기반 분석은 아직 없다.
- 샘플 `android-perfetto-FHD30-S24U.pftrace`에는 `perf_sample`, `cpu_profile_stack_sample`, `stack_profile_*` row가 0개다. 따라서 PMU 함수 병목 기능은 PMU/callstack이 들어있는 별도 trace에서 measured path를 검증해야 한다.
- 권장 구현은 analysis layer에 증거 데이터를 추가하고, report layer는 계산 결과만 표시하는 방식이다. Chart 스타일은 기존 원칙대로 `charts.py`에만 추가한다.

## 현재 구현 검토

### 실행 흐름

현재 CLI 흐름은 단순하고 유지하기 좋다.

- `soc_perfetto_analyzer/cli.py`
  - `analyze`: config load -> trace analysis -> report model -> HTML render -> quality gate -> output bundle
  - `render-report`: config load -> trace analysis -> report model -> HTML render
  - `check-trace`: config load -> trace analysis -> report model -> quality gate
- 확장은 `analysis.py`의 `AnalysisResult`에 measured data를 추가하고, `report/model.py`, `template.html.j2`, `json_report.py`, `quality_gate.py`가 이를 소비하도록 하는 방향이 맞다.

### event_config 처리

현재 `soc_perfetto_analyzer/config.py`는 `event_type == Task`만 추출한다.

- `ThreadTarget(event_name, thread, category, merge_gap_ms)`만 만든다.
- thread name은 `wake_condition`, `start_condition`, `end_condition`의 `comm`, `next_comm`, `prev_comm`, `newcomm`, `oldcomm`에서 찾는다.
- PMU profile scope, target period, cluster topology, trace capture hint는 아직 config model에 없다.
- 기존 loader가 raw config를 보존하므로, backward-compatible 확장은 가능하다.

### Runtime 및 scheduling 분석

현재 `soc_perfetto_analyzer/analysis.py`의 핵심 동작은 다음과 같다.

- `thread` table에서 event_config target thread name을 exact match한다.
- matched thread별로 `sched` table에서 `ts`, `dur`, `cpu`를 읽는다.
- `ThreadRuntime.samples_us`, `cpus`, `starts_s`로 저장한다.
- `thread_state`에서 `state in ('R', 'R+')` runnable wait을 읽어 jitter 분석에 사용한다.
- fallback path는 문자열 inventory만 수행하며 runtime/freq를 만들지 않는다.

제약:

- run burst record가 독립 객체가 아니라 병렬 list라 migration, co-runner overlap, ramp window join이 어렵다.
- `sched`의 non-target thread runtime을 저장하지 않는다.
- waker/preemptor/co-runner attribution은 report에서 placeholder로 표시된다.
- event_config의 start/end condition을 이용한 task interval reconstruction은 README에 미구현으로 명시되어 있다.

### CPU frequency 및 cluster 분석

현재 CPU clock 경로는 다음 수준이다.

- `_query_frequency_series()`가 `counter` + `cpu_counter_track`에서 `type='cpu_frequency'`를 읽는다.
- CPU 번호를 `_cpu_cluster()`로 `little/mid/big`에 매핑한다.
- cluster별 최대 600 sample만 저장한다.
- `_clock_context()`가 target run start 시점의 cluster frequency를 찾아 runtime/frequency pair를 만든다.
- `_clock_rows()`는 high-clock 대비 low-clock에서 runtime이 늘어난 경우를 clock-drop event로 표시한다.

제약:

- 사용자 요구는 clock 상승 원인 분석인데, 현재는 clock-drop 탐지 중심이다.
- cluster mapping이 SoC별로 동적이지 않다.
- 분석용 frequency series도 chart 제한과 같은 600 sample cap을 공유한다.
- frequency 상승 window와 주변 scheduler pressure를 직접 join하지 않는다.
- target thread migration 방향, periodicity, co-runner 추가 여부를 계산하지 않는다.

### PMU 및 함수 단위 profiling

현재 PMU 처리는 availability check 수준이다.

- `_try_trace_processor_analysis()`에서 `counter_track` name에 `cycles`, `instructions`, `linux.perf`가 있는지 count한다.
- PMU가 없으면 caveat에 `linux.perf PMU samples absent or incomplete`를 추가한다.
- `report/model.py`의 `_portion_row()`는 PMU가 present이면 time%를 cycle%/inst%에 그대로 복사한다.
- function, symbol, callstack, event ratio, IPC, cache/stall event 분석은 없다.

제약:

- PMU counter가 있어도 sample table을 읽지 않는다.
- function topN, self/cumulative cycle, binary/library, symbolization confidence가 없다.
- IO bound와 compute bound를 구분할 scheduler wait, blocked function, syscall context, PMU ratio 결합 로직이 없다.

### Report 및 output bundle

현재 report section은 §0-§8 고정이다.

- §3: HW SW portion, PMU absent/present tier 표시
- §4: Thread runtime profile
- §5: Wakeup/scheduling jitter
- §6: CPU clock & influence
- §7: Contention attribution placeholder
- §8: Appendix

현재 output bundle:

- `metrics/cpu_clock.json`은 `model["clock"]`만 저장한다.
- `metrics/contention.json`은 placeholder를 저장한다.
- PMU function bottleneck용 JSON 파일은 없다.
- `appendix/sql_queries.txt`는 현재 항상 "TraceProcessor SQL was unavailable" 문구를 쓴다. 실제로 SQL을 실행하는 path에서도 query audit trail이 저장되지 않는다.

## 개선안 1: Cluster별 clock 상승 원인 attribution

### 목표

Cluster별 frequency가 baseline 또는 평균 대비 상승한 구간을 찾고, 각 ramp window에 대해 다음 중 하나로 원인을 분류한다.

- `added_task_pressure`: target 외 co-runner 또는 runnable task 증가가 clock 상승을 유발한 가능성이 높음
- `periodic_target_migration`: camera scenario 계열 target task가 주기적으로 몰리고 상위 cluster로 migration되며 clock 상승
- `mixed_pressure`: target migration과 non-target pressure가 함께 관측됨
- `unknown`: 필요한 증거가 부족함

### 접근안 비교

권장안은 B이다.

| 접근 | 설명 | 장점 | 단점 |
| --- | --- | --- | --- |
| A. report/model.py에서 기존 `ThreadRuntime`만 이용 | 현재 list를 그대로 쓰고 `_clock_context()`를 확장 | 변경량이 작음 | co-runner, migration, periodicity 증거가 부족해 사용자 질문에 답하기 어렵다 |
| B. analysis.py에 clock attribution evidence model 추가 | run burst, freq, topology, co-runner window를 analysis 단계에서 계산 | report가 실제 증거를 표시할 수 있음, 테스트 가능 | `AnalysisResult`와 tests 업데이트 필요 |
| C. PerfettoSQL module 수준으로 큰 query/view 작성 | SQL에서 window join과 attribution을 대부분 계산 | 대용량 trace 처리에 유리 | 현재 작은 코드베이스에는 복잡도가 큼 |

### 새 데이터 계약

`soc_perfetto_analyzer/analysis.py`에 다음 개념을 추가한다. 이름은 구현 시 Python dataclass로 고정한다.

```python
@dataclass(frozen=True)
class CpuClusterInfo:
    name: str
    cpus: list[int]
    min_freq_ghz: float
    max_freq_ghz: float
    source: str  # topology_config, event_config, cpu_freq_table, freq_max_grouping, fallback

@dataclass(frozen=True)
class SchedRun:
    event_name: str
    category: str
    thread: str
    utid: int
    ts_s: float
    dur_us: float
    cpu: int
    cluster: str
    freq_ghz: float | None
    is_target: bool

@dataclass(frozen=True)
class ClockRampWindow:
    cluster: str
    start_s: float
    peak_s: float
    end_s: float
    baseline_ghz: float
    peak_ghz: float
    delta_pct: float
    target_runtime_us: float
    non_target_runtime_us: float
    new_non_target_threads: int
    target_migrations_into_cluster: int
    periodicity_score: float
    attribution: str
    confidence: str  # high, medium, low
    evidence: list[str]
    top_corunners: list[dict[str, str | float]]
```

`AnalysisResult`에는 다음 필드를 추가한다.

```python
cpu_clusters: list[CpuClusterInfo] = field(default_factory=list)
target_runs: list[SchedRun] = field(default_factory=list)
clock_ramp_windows: list[ClockRampWindow] = field(default_factory=list)
```

기존 `runtime_rows`, `freq_series`는 report 호환을 위해 유지한다. 새 분석은 `target_runs`와 `clock_ramp_windows`를 사용한다.

### Cluster topology 추론

현재 `_cpu_cluster(cpu)` 하드코딩은 보조 fallback으로만 남긴다. 권장안은 `event_config.yaml`에 top-level `cpu_topology`를 추가하고, 필요하면 별도 topology config를 CLI override로 받을 수 있게 하는 것이다.

우선순위:

1. CLI에 별도 topology config가 지정되면 최우선 적용한다.
2. `event_config.yaml`의 top-level `cpu_topology`가 있으면 적용한다.
3. Trace에 `cpu_freq` 또는 system info 기반 available frequencies가 있으면 CPU별 available frequency set으로 cluster를 묶는다.
4. 없으면 `cpu_frequency` counter의 CPU별 max frequency로 그룹화한다.
5. max frequency가 같은 CPU가 많아 세분화가 불가하면 CPU 번호 연속성과 observed frequency transition 동시성을 같이 본다.
6. 마지막 fallback만 현재 규칙을 사용하고 caveat에 남긴다.

권장 config 형태:

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

별도 config를 둘 경우 파일 형태는 동일하게 유지하고 CLI에서만 경로를 override한다.

```powershell
uv run python -m soc_perfetto_analyzer.cli analyze `
  --trace android-perfetto-FHD30-S24U.pftrace `
  --event-config event_config.yaml `
  --topology-config configs\s24u_cpu_topology.yaml `
  --out out\job
```

`event_config.yaml`에 넣는 방식은 한 과제의 scenario/thread/cluster 정의를 한 파일에서 관리할 수 있어 현재 저장소에는 가장 단순하다. 별도 topology config는 같은 event_config를 여러 SoC 또는 여러 과제에서 재사용할 때 필요하다.

샘플 trace에서 확인한 CPU별 max frequency:

```text
cpu0-1: max 1.344GHz
cpu2-7: max 2.630GHz
```

따라서 현재 `cpu<=3 little` 규칙은 샘플에도 맞지 않을 수 있다. 이 기능을 넣기 전에 topology 추론을 먼저 분리해야 한다.

### Frequency 상승 window 탐지

분석용 frequency series는 chart용 downsampling과 분리한다.

1. Cluster별 full frequency event series를 유지한다.
2. 각 cluster에서 baseline을 계산한다.
   - 기본: 전체 trace median 또는 mean
   - 권장: sliding window median, 예: 500ms 또는 1s
3. Ramp 후보를 잡는다.
   - `freq >= baseline * 1.15` 또는 `freq >= cluster p75`
   - 이전 sample 대비 상승률이 `+10%` 이상이면 ramp start로 표시
4. 인접 후보를 merge한다.
   - 기본 merge gap: 20ms
   - camera 30fps 분석에서는 33.3ms와 16.7ms period window도 별도 비교
5. attribution window를 생성한다.
   - ramp 이전 lead window: `[-50ms, 0ms]`
   - ramp 중/직후 window: `[0ms, +100ms]`
   - trace duration이 짧거나 burst가 짧으면 window를 2배까지 축소하지 말고 sample count caveat를 남긴다.

### 원인 분류 feature

#### Added task pressure feature

다음 증거를 계산한다.

- Ramp window 내 non-target scheduler runtime 합
- Ramp 이전 baseline window 대비 non-target runtime 증가율
- Ramp window에 새로 등장한 non-target thread 수
- 같은 cluster에 runnable 상태가 증가한 thread 수
- top co-runner thread와 overlap runtime

분류 조건 예:

```text
if non_target_runtime_delta_pct >= 30
and new_non_target_threads >= 1
and target_migrations_into_cluster == 0:
    attribution = added_task_pressure
```

report evidence 문구 예:

```text
big cluster ramp at 4.210s: non-target runtime +64% vs baseline; top co-runner RenderThread 1.8ms.
```

#### Periodic target migration feature

다음 증거를 계산한다.

- target run start interval의 period score
- event_config target 여러 개가 같은 period window에 몰리는지
- lower cluster에서 mid/big cluster로 migration된 횟수
- migration 직전 runnable wait 증가 여부
- ramp peak와 target migration 시점의 lead/lag

Periodicity score:

```text
target_period_ms = 33.3 또는 16.7을 기본 후보로 사용
score = 1 - normalized_median_absolute_error(observed_intervals_ms, target_period_ms)
score 범위 = 0.0..1.0
```

분류 조건 예:

```text
if periodicity_score >= 0.70
and target_migrations_into_cluster >= 1
and target_runtime_us >= non_target_runtime_us:
    attribution = periodic_target_migration
```

report evidence 문구 예:

```text
mid cluster ramp at 5.033s: Uni:PERSONAL_IM migrated little->mid 2 times; activation interval aligns with 33.3ms period, score 0.82.
```

#### Mixed 및 unknown

둘 다 강하면 `mixed_pressure`로 표시한다. sample 수가 부족하거나 frequency 초기값 gap이 있으면 `unknown`으로 표시하고 caveat를 남긴다.

### Report 반영

기존 nav contract를 크게 흔들지 않기 위해 첫 구현은 §6 내부 확장으로 한다.

추가할 report 요소:

- §6 verdict를 clock-drop 중심에서 clock ramp attribution 중심으로 확장
- §6 table: `Clock ramp attribution`
  - time, cluster, baseline -> peak, attribution, confidence, target runtime, non-target runtime, migration count, top co-runner, evidence
- §6 chart:
  - 기존 `freq_ts`에 ramp marker와 cluster별 target-active shading 추가
  - 별도 `cluster_clock_ramp_bar` chart: cluster별 attribution count
- §7 contention placeholder를 실제 co-runner table로 대체
  - target thread별 top co-runner
  - ramp window와 runtime outlier window를 분리 표시

Output bundle:

```text
metrics/cluster_clock_attribution.json
metrics/contention.json
appendix/sql_queries.txt
```

`metrics/cpu_clock.json`에는 기존 `throttle_rows`와 함께 `ramp_windows`를 추가한다.

## 개선안 2: PMU event 기반 thread별 top3 함수 병목 분석

### 목표

`event_config.yaml`의 target thread가 Perfetto profiling 범위에 들어가고, ARM telemetry/PMU event와 callstack sample이 trace에 존재하면 다음을 계산한다.

- thread별 cycle self/cumulative 기준 top3 함수
- 함수별 binary/library, symbolization 상태
- cycle, instruction, IPC, cache/stall/branch event ratio
- 병목 성격
  - `compute_bound`
  - `memory_bound`
  - `frontend_bound`
  - `branch_bound`
  - `io_or_wait_bound`
  - `unknown`
- 함수가 cycle을 많이 소모한 구체적 이유
  - 높은 instruction volume
  - 낮은 IPC
  - cache miss 또는 backend stall
  - frontend stall 또는 branch miss
  - syscall/blocking wait와 결합된 IO wait
  - 특정 cluster migration 또는 high clock window와 동시 발생

### 중요한 해석 원칙

Cycle sample은 CPU가 running 중일 때 관측되는 비용이다. 순수 IO wait 시간은 sleeping/blocking 상태라 cycle sample top function에 잘 나타나지 않을 수 있다. 따라서 IO-bound 판단은 PMU만으로 결정하지 않고 다음 증거를 함께 써야 한다.

- `thread_state.io_wait`
- `thread_state.blocked_function`
- syscall slice 또는 kernel frame
- runnable/running/wait 비율
- cycle top 함수가 syscall 또는 driver wait path인지 여부

즉 report에는 `IO-bound`라는 단정 대신, 증거가 충분할 때 `io_or_wait_bound`로 표시하고 근거를 함께 보여준다.

### 접근안 비교

권장안은 B를 기본으로 하고, 필요한 경우 A를 fallback으로 둔다.

| 접근 | 설명 | 장점 | 단점 |
| --- | --- | --- | --- |
| A. raw table 직접 join | `perf_sample` 또는 `cpu_profile_stack_sample`을 `stack_profile_*`와 직접 join | TraceProcessor stdlib module이 없어도 동작 가능 | Perfetto version별 column 차이를 직접 처리해야 함 |
| B. Perfetto stdlib module 사용 | `stacks.cpu_profiling`, `linux.perf.samples` summary table 우선 사용 | self/cumulative count 계산이 안정적이고 간결 | module availability probe와 fallback 필요 |
| C. external symbolization pipeline 연계 | `traceconv bundle` 또는 외부 symbol file 후처리 사용 | symbol 품질 개선 | analyzer 단독 실행 범위가 커짐 |

### Trace capture 요구사항

PMU 함수 병목은 trace capture가 다음 조건을 만족해야 measured로 표시한다.

- `linux.perf` data source가 enabled
- callstack sampling enabled
- target process/thread가 profiling scope에 포함
- cycle 또는 CPU clock timebase가 존재
- instruction 및 보조 PMU event가 있으면 classifier confidence가 올라감
- `sched_switch`, `sched_waking`, `thread_state`, `cpu_frequency`가 같이 있으면 root cause 설명이 강화됨

Perfetto 공식 문서 기준으로 callstack profiling은 `linux.perf`에서 callstack sampling을 켜야 하며, sample은 `perf_sample` 또는 `cpu_profile_stack_sample` 계열 table과 `stack_profile_callsite/frame/mapping`으로 질의할 수 있다. 새 구현은 table existence를 고정 가정하지 말고 probe query로 확인한다.

### event_config 확장 제안

기존 `events` 구조를 깨지 않고 top-level optional block을 추가한다. 현재 loader는 `raw`를 보존하므로 backward-compatible하다.

```yaml
pmu_profile:
  enabled: true
  scope: event_config_threads
  top_n_functions: 3
  require_callstack: true
  cycle_events:
    - cpu-cycles
    - HW_CPU_CYCLES
    - armv8_pmuv3/cycles
  instruction_events:
    - instructions
    - HW_INSTRUCTIONS
    - armv8_pmuv3/instructions
  classifier_events:
    cache_misses:
      - cache-misses
      - L1D_CACHE_REFILL
      - LLC_MISS
    branch_misses:
      - branch-misses
      - BR_MIS_PRED
    frontend_stall:
      - STALL_FRONTEND
    backend_stall:
      - STALL_BACKEND
  symbolization:
    min_symbol_confidence: mapped
```

이 block은 analyzer의 분석 의도와 report 기준을 정의한다. 실제 Perfetto capture config는 별도 `.pbtx`에서 관리하되, analyzer는 trace에 필요한 table/event가 있는지 capability로 검증한다.

### 새 데이터 계약

`soc_perfetto_analyzer/analysis.py`에 PMU 관련 모델을 추가한다.

```python
@dataclass(frozen=True)
class PmuCapability:
    has_perf_samples: bool
    has_callstacks: bool
    has_cycles: bool
    has_instructions: bool
    classifier_events: list[str]
    caveats: list[str]

@dataclass(frozen=True)
class FunctionHotspot:
    thread: str
    function: str
    mapping: str
    source_file: str | None
    line_number: int | None
    self_cycles: float | None
    cumulative_cycles: float | None
    self_samples: int
    cumulative_samples: int
    sample_pct: float
    ipc: float | None
    cache_miss_pct: float | None
    frontend_stall_pct: float | None
    backend_stall_pct: float | None
    wait_pct: float | None
    classification: str
    confidence: str
    reason: str
```

`AnalysisResult` 추가 필드:

```python
pmu_capability: PmuCapability | None = None
function_hotspots_by_thread: dict[str, list[FunctionHotspot]] = field(default_factory=dict)
```

### SQL probe 및 extraction

구현 순서:

1. PMU sample table probe
   - `select count(*) from perf_sample`
   - `select count(*) from cpu_profile_stack_sample`
   - 둘 다 실패하거나 0이면 PMU function path는 `N/A: perf callstack samples absent`
2. Callstack table probe
   - `stack_profile_callsite`
   - `stack_profile_frame`
   - `stack_profile_mapping`
3. Perfetto stdlib module probe
   - `INCLUDE PERFETTO MODULE stacks.cpu_profiling`
   - 가능하면 `cpu_profiling_samples`, `cpu_profiling_summary_tree` 사용
4. linux perf summary module probe
   - `INCLUDE PERFETTO MODULE linux.perf.samples`
   - 가능하면 `linux_perf_samples_summary_tree` 사용
5. fallback direct join
   - `perf_sample.callsite_id -> stack_profile_callsite.frame_id -> stack_profile_frame -> stack_profile_mapping`

직접 join의 기본 형태:

```sql
select
  s.ts,
  s.utid,
  t.name as thread_name,
  f.name as function_name,
  m.name as mapping_name
from perf_sample s
join thread t on s.utid = t.utid
join stack_profile_callsite c on s.callsite_id = c.id
join stack_profile_frame f on c.frame_id = f.id
join stack_profile_mapping m on f.mapping = m.id
where t.name in (...)
```

단, column 이름은 Perfetto version과 source format에 따라 달라질 수 있으므로 실제 구현은 `select * ... limit 1` probe로 column set을 확인하고 query builder를 선택한다.

### Top3 함수 선정 기준

기본 ranking:

1. cycle event가 function에 직접 attribution 가능하면 cumulative cycle 기준 top3
2. cycle count가 없고 callstack sample만 있으면 cumulative sample count 기준 top3
3. self와 cumulative를 둘 다 표시한다.
   - self: leaf frame에서 직접 소비한 비중
   - cumulative: call path에 포함된 비중

동률 처리:

```text
cumulative_cycles desc
self_cycles desc
self_samples desc
function name asc
```

### 병목 분류 rule

분류는 단일 metric이 아니라 증거 조합으로 결정한다.

```text
compute_bound:
  cycle share high
  instruction share high
  IPC가 thread median 이상
  cache/stall/wait evidence 낮음

memory_bound:
  backend stall 또는 cache miss event 비율 높음
  IPC 낮음
  같은 function이 high clock에서도 runtime 개선이 제한적

frontend_bound:
  frontend stall event 비율 높음
  branch miss가 낮거나 중간

branch_bound:
  branch miss event 비율 높음
  frontend stall 또는 low IPC 동반

io_or_wait_bound:
  thread wall time에서 thread_state io_wait 또는 blocked_function 비중 높음
  top function/call path가 syscall, driver, binder, poll/epoll/futex/wait 계열
  cycle sample만으로는 wait time을 대표하지 않는다는 caveat 표시

unknown:
  cycles 또는 classifier event가 부족해 top 함수는 알지만 성격 분류 불가
```

Report reason 예:

```text
compute_bound: 38.2% cumulative cycles in libfoo.so::ProcessFrame; IPC 2.1, cache miss low, wait 3.4%.
memory_bound: 24.7% cumulative cycles in libbar.so::CopyPlane; IPC 0.6, backend stall high, L1D refill high.
io_or_wait_bound: 12.5% samples in ioctl path, but 41% wall time in io_wait; cycle cost is secondary to driver wait.
```

### Report 반영

첫 구현은 새 nav section을 만들지 않고 기존 section 안에 넣는다.

- §3 HW SW portion
  - PMU tier를 실제 measured/partial/time-only로 분리
  - cycle%/inst%는 실제 PMU data가 있을 때만 numeric
- §4 Thread runtime profile
  - `Per-thread PMU top functions` table 추가
  - thread, rank, function, mapping, cumulative cycle/sample %, self %, IPC, classification, reason
- §8 Appendix
  - PMU capability probe 결과
  - symbolization caveat
  - executed SQL query names

Output bundle:

```text
metrics/thread_function_bottlenecks.json
metrics/sw_portion.json
appendix/pmu_sql_queries.sql
```

`report.json`에는 top-level key를 추가한다.

```json
{
  "thread_function_bottlenecks": [
    {
      "thread": "Uni:PERSONAL_IM",
      "top_functions": [
        {
          "rank": 1,
          "function": "libx.so::Foo",
          "mapping": "libx.so",
          "sample_pct": 38.2,
          "classification": "compute_bound",
          "reason": "..."
        }
      ]
    }
  ]
}
```

## 권장 구현 순서

### Step 1. Evidence model 추가

- `analysis.py`에 `CpuClusterInfo`, `SchedRun`, `ClockRampWindow`, `PmuCapability`, `FunctionHotspot` dataclass 추가
- `AnalysisResult`에 새 optional/list 필드 추가
- 기존 tests fixture가 깨지지 않도록 default 값을 둔다.

### Step 2. CPU topology 추론 분리

- `_cpu_cluster(cpu)` 직접 호출을 줄이고 `CpuTopology` helper를 만든다.
- `config.py`에 optional `cpu_topology` parser를 추가한다.
- CLI에는 선택적으로 `--topology-config`를 추가하되, 첫 구현은 `event_config.yaml` top-level `cpu_topology`만으로도 동작하게 한다.
- sample trace 기준으로 CPU별 max frequency grouping test를 추가한다.
- topology source와 caveat를 report appendix에 표시한다.

### Step 3. target run record 보존

- `_query_target_runtime()`에서 기존 `ThreadRuntime`과 함께 `SchedRun` list를 생성한다.
- 각 run에 cluster와 instantaneous freq를 붙인다.
- `ThreadRuntime`은 기존 report 호환용 summary로 유지한다.

### Step 4. clock ramp window와 attribution 구현

- cluster별 full frequency series로 ramp window를 찾는다.
- ramp window 주변 target/non-target scheduler pressure를 계산한다.
- migration, periodicity, top co-runner를 계산한다.
- `ClockRampWindow`를 `AnalysisResult.clock_ramp_windows`에 저장한다.

### Step 5. §6/§7 report 확장

- `report/model.py`에 `clock_ramp_rows`, `cluster_clock_summary`, real `contention.corunners`를 추가한다.
- `charts.py`에 ramp attribution chart builder를 추가한다.
- `template.html.j2` §6 table과 §7 table을 확장한다.
- `json_report.py`에 새 metrics 파일을 추가한다.

### Step 6. PMU capability probe 구현

- sample table, callstack table, stdlib module availability를 probe한다.
- 현재 샘플 trace에서는 measured path가 아니라 `N/A: perf callstack samples absent`가 나와야 한다.
- PMU availability와 function profiling availability를 분리한다.

### Step 7. PMU top function extraction 구현

- stdlib module path를 우선 사용한다.
- unavailable이면 direct join fallback을 사용한다.
- target thread name은 event_config exact match 결과를 기준으로 제한한다.
- top3 function list를 thread별로 저장한다.

### Step 8. PMU 병목 분류 구현

- cycle/instruction/cache/stall/branch event를 normalized ratio로 변환한다.
- `thread_state` wait/io evidence를 thread별로 join한다.
- classification과 reason string을 생성한다.
- classifier event가 부족하면 top function은 표시하되 `unknown` 또는 `partial` confidence로 둔다.

### Step 9. Quality gate 확장

- numeric PMU metric은 measured evidence가 있을 때만 허용한다.
- PMU absent 또는 callstack absent일 때 cycle/function table은 reasoned N/A여야 한다.
- chart caption과 severity vocabulary 검증에 새 figure key를 추가한다.
- report JSON에는 HTML fragment가 섞이지 않아야 한다.

## 테스트 전략

### Unit tests

- CPU topology
  - CPU0-1 max 1.3GHz, CPU2-7 max 2.6GHz fixture에서 cluster grouping 검증
  - config override가 있으면 override 우선 검증
- Clock ramp detection
  - baseline 1.0GHz에서 1.4GHz 상승 window 검출
  - 인접 ramp merge 검증
- Attribution
  - non-target runtime 증가 fixture -> `added_task_pressure`
  - 33.3ms 주기 + lower->mid migration fixture -> `periodic_target_migration`
  - 둘 다 있는 fixture -> `mixed_pressure`
- PMU capability
  - sample table absent -> measured false와 caveat 검증
  - callstack table absent -> function profiling N/A 검증
- PMU classification
  - high IPC/low miss -> `compute_bound`
  - low IPC/high backend stall -> `memory_bound`
  - high io_wait + syscall function -> `io_or_wait_bound`

### Integration tests

- 기존 sample trace
  - `analysis.capability.cpu_frequency is True`
  - `analysis.capability.pmu is False`
  - `thread_function_bottlenecks`는 empty 또는 N/A reason
  - cluster topology caveat/source가 기록됨
- PMU fixture trace
  - 별도 small trace를 추가하거나, fixture abstraction으로 TraceProcessor row를 mock한다.
  - 실제 `.pftrace` 추가가 어렵다면 unit-level fake TraceProcessor를 먼저 둔다.

### Report tests

- `template.html.j2`가 새 §6 ramp table과 §4 PMU table을 render한다.
- `quality_gate.py`가 새 figure key와 N/A reason을 통과시킨다.
- `report.json`과 `metrics/*.json`에 Plotly HTML이 섞이지 않는다.

## 구현 리스크 및 대응

| 리스크 | 영향 | 대응 |
| --- | --- | --- |
| SoC cluster topology 오분류 | clock 상승 원인 분석 전체가 틀어짐 | frequency table 기반 topology를 먼저 구현하고 fallback caveat 표시 |
| cpu_frequency 초기값 gap | trace 시작 직후 ramp baseline 오판 | initial frequency missing caveat, 첫 valid sample 이전 window 제외 |
| PMU sample은 있으나 callstack 없음 | 함수 단위 분석 불가 | thread-level PMU만 partial로 표시, function table은 reasoned N/A |
| symbol 미해결 | top 함수가 주소 또는 unknown으로 표시 | mapping 기준 grouping, symbolization confidence 표시 |
| PMU event 이름이 장비마다 다름 | classifier event miss | event alias list를 config raw block으로 확장 |
| IO-bound 오판 | cycle top 함수와 wall wait 혼동 | thread_state io_wait/blocked_function 증거 없으면 IO-bound로 단정하지 않음 |
| report nav/gate churn | 기존 품질 게이트 실패 | 첫 구현은 기존 §4/§6/§7 내부 확장으로 제한 |

## 문서화 업데이트 필요 항목

README에 다음을 추가한다.

- Cluster topology는 SoC별로 동적 추론하며 fallback 시 caveat가 표시된다는 설명
- Clock 상승 attribution의 분류 기준
- PMU function bottleneck은 `linux.perf` callstack sample이 있을 때만 measured라는 설명
- ARM PMU event alias 설정 예시
- sample trace는 PMU absent이므로 function bottleneck이 N/A로 표시된다는 설명

## 참고 자료

- Perfetto CPU frequency and idle states: https://perfetto.dev/docs/data-sources/cpu-freq
- Perfetto CPU profiling / linux.perf: https://perfetto.dev/docs/getting-started/cpu-profiling
- PerfettoSQL getting started and profiling table examples: https://perfetto.dev/docs/analysis/perfetto-sql-getting-started
- Perfetto trace processor overview: https://perfetto.dev/docs/analysis/trace-processor
