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


def clock_ramp_attribution(rows):
    """rows: [{cluster, attribution, delta_pct_float}]"""
    if not rows:
        return _placeholder("no clock ramp attribution rows"), ""
    labels = []
    values = []
    colors = []
    for row in rows:
        labels.append(f"{row['cluster']} · {row['attribution']}")
        values.append(float(row.get("delta_pct_float", 0.0)))
        attribution = str(row.get("attribution", "unknown"))
        colors.append(
            {
                "added_task_pressure": COL["warn"],
                "periodic_target_migration": COL["info"],
                "mixed_pressure": COL["bad"],
                "unknown": COL["na"],
            }.get(attribution, COL["na"])
        )
    fig = go.Figure(go.Bar(
        y=labels,
        x=values,
        orientation="h",
        marker_color=colors,
        text=[f"+{value:.1f}%" for value in values],
        textposition="outside",
        textfont=dict(size=10),
    ))
    fig.update_layout(**_layout(
        height=80 + 32 * len(rows),
        showlegend=False,
        xaxis=dict(title="freq increase vs baseline (%)"),
        margin=dict(l=180, r=80, t=14, b=42),
    ))
    cap = "Clock ramp windows grouped by cluster and attribution; bar length is frequency increase versus local baseline."
    return _emit(fig), cap
