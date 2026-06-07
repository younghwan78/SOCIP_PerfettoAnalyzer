from pathlib import Path
from types import SimpleNamespace

from soc_perfetto_analyzer.analysis import PmuCapability, _query_function_hotspots
from soc_perfetto_analyzer.config import EventConfig, ThreadTarget


class FakeTraceProcessor:
    def query(self, sql):
        if "from perf_sample" not in sql:
            return []
        return [
            SimpleNamespace(thread="CameraThread", function="ProcessFrame", mapping="libcamera.so", self_samples=50),
            SimpleNamespace(thread="CameraThread", function="CopyPlane", mapping="libcamera.so", self_samples=30),
            SimpleNamespace(thread="CameraThread", function="WaitFence", mapping="libsync.so", self_samples=20),
            SimpleNamespace(thread="CameraThread", function="SmallHelper", mapping="libcamera.so", self_samples=5),
        ]


def test_query_function_hotspots_returns_top3_per_event_config_thread():
    config = EventConfig(
        path=Path("event_config.yaml"),
        description="fixture",
        config_version="1.0",
        event_count=1,
        thread_targets=[ThreadTarget(event_name="Camera", thread="CameraThread", category="camera_hal")],
        raw={},
    )
    capability = PmuCapability(
        has_perf_samples=True,
        has_callstacks=True,
        has_cycles=True,
        has_instructions=False,
        caveats=[],
    )

    hotspots = _query_function_hotspots(FakeTraceProcessor(), config, capability)

    assert list(hotspots) == ["CameraThread"]
    assert [hotspot.function for hotspot in hotspots["CameraThread"]] == ["ProcessFrame", "CopyPlane", "WaitFence"]
    assert hotspots["CameraThread"][0].sample_pct == 47.6
    assert hotspots["CameraThread"][0].classification == "unknown"
    assert "classifier PMU events unavailable" in hotspots["CameraThread"][0].reason
