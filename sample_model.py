"""Sample v2 model — builds every chart via charts.py (regularized format) and
fills verdicts/issues/tier so the skeleton can be validated end-to-end."""
import random, math
import charts as ch

random.seed(7)


def build_model():
    ch.reset_plotlyjs()

    # ---- charts (all via charts.py — identical formatting guaranteed) ----
    hw_map = {"html": _hw_map_svg(), "caption":
              "Active blocks shaded; NPU indeterminate (no IRQ/counter)."}

    portion = ch.portion_bar(
        [{"name": "GPU", "time_pct": 12.3}, {"name": "CODEC", "time_pct": 8.0},
         {"name": "ISP", "time_pct": 6.2}, {"name": "DPU", "time_pct": 4.1},
         {"name": "Other", "time_pct": 69.4}],
        denominator="multimedia CPU running time")

    runtime_box = ch.runtime_box([
        {"thread": "CamX_ReqProc",
         "samples": [max(.3, random.gauss(3.2, 1.6)) for _ in range(200)],
         "severity": "warn"},
        {"thread": "C2_dec_worker",
         "samples": [max(.3, random.gauss(4.0, 2.4)) for _ in range(200)],
         "severity": "bad"},
        {"thread": "hwc_main",
         "samples": [max(.3, random.gauss(1.8, .5)) for _ in range(200)],
         "severity": "ok"},
    ], baseline_us=1.6)

    wakeup_cdf = ch.wakeup_cdf({
        "little": [max(1, random.gauss(60, 36)) for _ in range(300)],
        "mid": [max(1, random.gauss(35, 21)) for _ in range(300)],
        "big": [max(1, random.gauss(22, 13)) for _ in range(300)],
    })

    jitter_rank = ch.jitter_rank([
        {"thread": "C2_dec_worker", "p99_us": 920, "runnable_pct": 5.1, "severity": "bad"},
        {"thread": "CamX_ReqProc", "p99_us": 480, "runnable_pct": 3.2, "severity": "warn"},
        {"thread": "hwc_main", "p99_us": 210, "runnable_pct": 1.8, "severity": "ok"},
    ])

    interval_strip = ch.interval_strip(
        [33.3 + random.gauss(0, 1.5) + (8 if i % 37 == 0 else 0) for i in range(160)],
        target_ms=33.3, thread="CamX_ReqProc")

    xs = [i * 0.1 for i in range(124)]
    freq_ts = ch.freq_timeline({
        "big": (xs, [2.2 + .6 * abs(math.sin(x * 1.3)) - (.7 if 7.1 < x < 7.8 else 0) for x in xs]),
        "mid": (xs, [1.6 + .4 * abs(math.sin(x * 1.1)) for x in xs]),
    }, active_spans=[(3.0, 3.6), (7.0, 7.9)])

    freq_residency = ch.freq_residency(
        ["CPU0-3", "CPU4-6", "CPU7"], ["0.8G", "1.2G", "1.8G", "2.4G", "2.8G"],
        {"CPU0-3": [40, 30, 20, 8, 2], "CPU4-6": [10, 20, 30, 28, 12],
         "CPU7": [5, 10, 25, 35, 25]})

    fq = [random.gauss(2.0, 0.45) for _ in range(120)]
    rt = [max(.5, 8 - 1.8 * f + random.gauss(0, .8)) for f in fq]
    freq_corr = ch.runtime_vs_freq(fq, rt, r_value=-0.54)

    figs = {k: {"html": v[0], "caption": v[1]} for k, v in {
        "portion": portion, "runtime_box": runtime_box, "wakeup_cdf": wakeup_cdf,
        "jitter_rank": jitter_rank, "interval_strip": interval_strip,
        "freq_ts": freq_ts, "freq_residency": freq_residency, "freq_corr": freq_corr,
    }.items()}
    figs["hw_map"] = hw_map
    figs["waker_chain"] = None

    return {
      "meta": {"scenario": "camera_preview_30fps", "device": "S25 EVT",
               "soc": "vendor_soc_a", "duration_s": 12.4,
               "window_s": "2.0–12.4s", "generated": "2026-05-31 02:00"},
      # P1 verdicts — one sentence opening each section (deterministic)
      "verdicts": {
        "capability": "Scheduler + clock data present; PMU absent, so R2 falls back to time-only portion.",
        "hw": "4/5 HW blocks active (GPU, DPU, CODEC, ISP); NPU indeterminate — no IRQ or counter evidence.",
        "portion": "Multimedia SW is 30.6% of multimedia CPU running time; GPU is the largest driver at 12.3%.",
        "runtime": "3 vendor threads profiled; C2_dec_worker is most variable (CoV 0.61, 2.4× the stable-peer baseline).",
        "jitter": "Wakeup tail worst on C2_dec_worker (p99 920µs, ~4× big-cluster baseline); little-core threads within norm.",
        "clock": "3 clock-drop events on the big cluster; 1 overlaps codec runtime outliers (see scatter, r=-0.54).",
        "contention": "During codec outliers, a background service is the dominant co-runner (candidate, not confirmed).",
      },
      "hw_usage": [
        {"name": "GPU", "status": "ok", "state_label": "USED", "portion_label": "12.3%",
         "confidence": "confirmed", "irq_count": "4,210", "driver_runtime": "312 ms", "util_counter": "18% avg", "freq_counter": "varies"},
        {"name": "DPU", "status": "ok", "state_label": "USED", "portion_label": "4.1%",
         "confidence": "confirmed", "irq_count": "6,001", "driver_runtime": "88 ms", "util_counter": "N/A", "freq_counter": "varies"},
        {"name": "CODEC", "status": "ok", "state_label": "USED", "portion_label": "8.0%",
         "confidence": "confirmed", "irq_count": "1,840", "driver_runtime": "205 ms", "util_counter": "N/A", "freq_counter": "varies"},
        {"name": "ISP", "status": "ok", "state_label": "USED", "portion_label": "6.2%",
         "confidence": "confirmed", "irq_count": "3,560", "driver_runtime": "140 ms", "util_counter": "N/A", "freq_counter": "varies"},
        {"name": "NPU", "status": "na", "state_label": "UNKNOWN", "portion_label": "—",
         "confidence": "unavailable", "irq_count": "0", "driver_runtime": "0", "util_counter": "N/A", "freq_counter": "N/A"},
      ],
      "hw_usage_note": "Util counters exist for GPU only — DPU/Codec/ISP usage is confirmed via IRQ, but HW utilization (busy %) is N/A for those blocks.",
      "kpis": [
        {"label": "Est. FPS", "value": "29.4 /30", "status": "ok", "sub": "heuristic (no marker)"},
        {"label": "Worst jitter CoV", "value": "0.61", "status": "bad", "sub": "C2_dec_worker"},
        {"label": "Max wakeup p99", "value": "920µs", "status": "warn", "sub": "big cluster"},
        {"label": "Clock-drop events", "value": "3", "status": "warn", "sub": "1 overlaps outliers"},
      ],
      # C.2 issue objects
      "top_issues": [
        {"severity": "bad", "headline": "C2_dec_worker wakeup p99 is 920µs, exceeding the 500µs threshold",
         "comparison": "≈4× the big-cluster wakeup baseline; runnable 5.1%",
         "confidence": "confirmed", "next_step": "check co-runners in §7",
         "cross_ref": "§5 Jitter", "cross_ref_anchor": "s5"},
        {"severity": "warn", "headline": "CamX_ReqProc runtime is unstable (CoV 0.58)",
         "comparison": "2.3× the stable-peer baseline (hwc_main 0.25)",
         "confidence": "confirmed", "next_step": None,
         "cross_ref": "§4 Runtime", "cross_ref_anchor": "s4"},
        {"severity": "warn", "headline": "Big-cluster clock drop overlaps codec runtime outliers",
         "comparison": "runtime +22% during the 7.2s drop",
         "confidence": "estimated", "next_step": "thermal source not captured — inferred only",
         "cross_ref": "§6 CPU clock", "cross_ref_anchor": "s6"},
      ],
      "capability": {
        "has_waking": True,
        "sources": [
          {"name": "sched_switch", "present": "yes", "status": "ok", "affects": "R3, R4"},
          {"name": "sched_waking", "present": "yes", "status": "ok", "affects": "wakeup jitter"},
          {"name": "cpu_frequency", "present": "yes", "status": "ok", "affects": "R4, R2 estimated"},
          {"name": "irq events", "present": "yes", "status": "ok", "affects": "R1"},
          {"name": "HW util counters", "present": "partial (GPU)", "status": "warn", "affects": "R1 utilization"},
          {"name": "linux.perf (PMU)", "present": "no", "status": "bad", "affects": "R2 cycle/inst"},
          {"name": "vsync", "present": "yes", "status": "ok", "affects": "display period"},
        ],
        "integrity": [
          {"item": "ftrace data loss", "value": "0 events", "status": "ok"},
          {"item": "trace duration", "value": "12.4 s", "status": "ok"},
          {"item": "analysis window", "value": "2.0–12.4 s", "status": "ok"},
          {"item": "clock sync", "value": "OK", "status": "ok"},
        ],
      },
      "figures": figs,
      "portion": {
        "denominator": "multimedia CPU running time", "tier": "time_only",
        "tier_label": "time-only", "pbtx_hint": "Add linux.perf with HW_CPU_CYCLES + HW_INSTRUCTIONS followers to enable cycle/inst.",
        "rows": [
          {"name": "GPU", "time_pct": "12.3", "cycle_pct": "N/A", "inst_pct": "N/A", "ipc": "N/A", "pmu_na": True},
          {"name": "CODEC", "time_pct": "8.0", "cycle_pct": "N/A", "inst_pct": "N/A", "ipc": "N/A", "pmu_na": True},
          {"name": "ISP", "time_pct": "6.2", "cycle_pct": "N/A", "inst_pct": "N/A", "ipc": "N/A", "pmu_na": True},
          {"name": "DPU", "time_pct": "4.1", "cycle_pct": "N/A", "inst_pct": "N/A", "ipc": "N/A", "pmu_na": True},
        ],
      },
      "runtime": {"rows": [
        {"category": "camera_hal", "thread": "CamX_ReqProc", "n": "1,204", "min": "0.8", "avg": "3.2",
         "p50": "2.9", "p95": "7.1", "p99": "11.2", "max": "18", "cov": "0.58", "cov_status": "warn", "core_mix": "40/50/10"},
        {"category": "codec_hal", "thread": "C2_dec_worker", "n": "980", "min": "1.1", "avg": "4.0",
         "p50": "3.5", "p95": "9.8", "p99": "14.0", "max": "22", "cov": "0.61", "cov_status": "bad", "core_mix": "20/60/20"},
        {"category": "hwc", "thread": "hwc_main", "n": "744", "min": "0.6", "avg": "1.8",
         "p50": "1.6", "p95": "3.4", "p99": "5.0", "max": "8", "cov": "0.25", "cov_status": "ok", "core_mix": "70/30/0"},
      ]},
      "jitter": {"pbtx_hint": "Re-capture with sched/sched_waking enabled.", "rows": [
        {"thread": "C2_dec_worker", "p50": "28", "p95": "420", "p99": "920", "p99_status": "bad",
         "cov": "0.61", "mad": "1.8", "interval_sigma": "4.2ms", "status": "bad", "cross_ref": "§6", "cross_ref_anchor": "s6"},
        {"thread": "CamX_ReqProc", "p50": "31", "p95": "260", "p99": "480", "p99_status": "warn",
         "cov": "0.42", "mad": "1.1", "interval_sigma": "2.1ms", "status": "warn", "cross_ref": "", "cross_ref_anchor": ""},
        {"thread": "hwc_main", "p50": "18", "p95": "120", "p99": "210", "p99_status": "ok",
         "cov": "0.25", "mad": "0.6", "interval_sigma": "0.9ms", "status": "ok", "cross_ref": "", "cross_ref_anchor": ""},
      ]},
      "clock": {"throttle_rows": [
        {"t": "7.2", "cluster": "big", "drop": "2.8→1.8G", "runtime_delta": "codec +22%", "thermal": "inferred"},
      ]},
      "contention": {"target": "C2_dec_worker", "corunners": [
        {"name": "some_bg_service", "overlap": "42 ms", "count": "310", "status": "bad"},
        {"name": "kworker/u16:3", "overlap": "18 ms", "count": "980", "status": "warn"},
      ]},
      "appendix": {
        "unmatched": ["*apv*", "*npu*util*"], "ambiguous": ["C2* (codec_hal + codec block)"],
        "caveats": [
          "PMU absent → R2 reported as time-only (tier).",
          "Util counters present for GPU only → other HW utilization N/A.",
          "Frame period inferred from vsync/shot proxy (no explicit marker).",
          "Throttling thermal cause inferred, not confirmed.",
        ],
        "versions": {"analyzer": "0.2.0", "trace_processor": "47.0", "python": "3.12", "plotly": "5.x"},
        "config_dump": "meta:\n  scenario: camera_preview_30fps\nmatching:\n  mode: regex\n  ...",
      },
    }


def _hw_map_svg():
    return """
<svg viewBox="0 0 640 250" style="width:100%;height:auto;font-family:sans-serif">
 <rect x="6" y="6" width="628" height="238" rx="10" fill="none" stroke="#2a2f3a"/>
 <rect x="40" y="90" width="110" height="70" rx="6" fill="#1e222b" stroke="#3a4150"/>
 <text x="95" y="120" text-anchor="middle" fill="#e6e8ee" font-size="13">CPU</text>
 <text x="95" y="138" text-anchor="middle" fill="#9aa2b1" font-size="10">b/m/L</text>
 <line x1="150" y1="110" x2="250" y2="65" stroke="#2dd4a7" stroke-width="2"/>
 <line x1="150" y1="125" x2="250" y2="125" stroke="#2dd4a7" stroke-width="2"/>
 <line x1="150" y1="140" x2="250" y2="185" stroke="#2dd4a7" stroke-width="2"/>
 <line x1="400" y1="125" x2="450" y2="125" stroke="#6b7280" stroke-width="2" stroke-dasharray="4 4"/>
 <g font-size="12">
  <rect x="250" y="40" width="120" height="52" rx="6" fill="#11302a" stroke="#2dd4a7"/>
  <text x="310" y="62" text-anchor="middle" fill="#2dd4a7">GPU</text>
  <text x="310" y="80" text-anchor="middle" fill="#7fe3c9" font-size="10">USED · 12.3%</text>
  <rect x="250" y="99" width="120" height="52" rx="6" fill="#11302a" stroke="#2dd4a7"/>
  <text x="310" y="121" text-anchor="middle" fill="#2dd4a7">ISP</text>
  <text x="310" y="139" text-anchor="middle" fill="#7fe3c9" font-size="10">USED · 6.2%</text>
  <rect x="250" y="158" width="120" height="52" rx="6" fill="#11302a" stroke="#2dd4a7"/>
  <text x="310" y="180" text-anchor="middle" fill="#2dd4a7">DPU</text>
  <text x="310" y="198" text-anchor="middle" fill="#7fe3c9" font-size="10">USED · 4.1%</text>
  <rect x="420" y="40" width="120" height="52" rx="6" fill="#11302a" stroke="#2dd4a7"/>
  <text x="480" y="62" text-anchor="middle" fill="#2dd4a7">CODEC(MFC)</text>
  <text x="480" y="80" text-anchor="middle" fill="#7fe3c9" font-size="10">USED · 8.0%</text>
  <rect x="450" y="99" width="120" height="52" rx="6" fill="#1e222b" stroke="#3a4150"/>
  <text x="510" y="121" text-anchor="middle" fill="#9aa2b1">NPU</text>
  <text x="510" y="139" text-anchor="middle" fill="#6b7280" font-size="10">UNKNOWN</text>
 </g>
</svg>"""
