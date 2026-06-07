from pathlib import Path

from soc_perfetto_analyzer.analysis import SchedRun, _cluster_name_for_cpu, _configured_cpu_clusters, _detect_clock_ramp_windows
from soc_perfetto_analyzer.config import CpuCluster, CpuTopology, EventConfig


def test_configured_cpu_topology_overrides_default_cluster_mapping():
    config = EventConfig(
        path=Path("event_config.yaml"),
        description="fixture",
        config_version="1.0",
        event_count=0,
        thread_targets=[],
        raw={},
        cpu_topology=CpuTopology(
            source="project_config",
            clusters=[
                CpuCluster(name="little", cpus=[0, 1], role="efficiency", freq_hint_ghz=(0.3, 1.4)),
                CpuCluster(name="mid", cpus=[2, 3, 4, 5, 6], role="performance", freq_hint_ghz=(0.5, 2.6)),
                CpuCluster(name="big", cpus=[7], role="prime", freq_hint_ghz=(0.6, 3.4)),
            ],
        ),
    )

    clusters = _configured_cpu_clusters(config)

    assert [cluster.name for cluster in clusters] == ["little", "mid", "big"]
    assert clusters[0].cpus == [0, 1]
    assert clusters[0].max_freq_ghz == 1.4
    assert clusters[0].source == "event_config:project_config"
    assert _cluster_name_for_cpu(3, clusters) == "mid"
    assert _cluster_name_for_cpu(7, clusters) == "big"


def test_cpu_cluster_mapping_falls_back_when_config_absent():
    config = EventConfig(
        path=Path("event_config.yaml"),
        description="fixture",
        config_version="1.0",
        event_count=0,
        thread_targets=[],
        raw={},
    )

    assert _configured_cpu_clusters(config) == []
    assert _cluster_name_for_cpu(0, []) == "little"
    assert _cluster_name_for_cpu(5, []) == "mid"
    assert _cluster_name_for_cpu(7, []) == "big"


def test_clock_ramp_attribution_detects_added_task_pressure():
    runs = [
        SchedRun("Camera", "camera_hal", "CameraThread", 1, 0.11, 200.0, 2, "mid", 1.4, True),
        SchedRun("other", "other", "RenderThread", 2, 0.11, 900.0, 3, "mid", 1.4, False),
        SchedRun("other", "other", "BinderWorker", 3, 0.12, 700.0, 4, "mid", 1.4, False),
    ]
    freq_series = {"mid": ([0.0, 0.10, 0.20, 0.30], [1.0, 1.4, 1.4, 1.0])}

    windows = _detect_clock_ramp_windows(freq_series, runs)

    assert len(windows) == 1
    assert windows[0].attribution == "added_task_pressure"
    assert windows[0].cluster == "mid"
    assert windows[0].new_non_target_threads == 2
    assert windows[0].top_corunners[0]["thread"] == "RenderThread"


def test_clock_ramp_attribution_detects_periodic_target_migration():
    runs = [
        SchedRun("Camera", "camera_hal", "CameraThread", 1, 0.066, 600.0, 0, "little", 1.0, True),
        SchedRun("Camera", "camera_hal", "CameraThread", 1, 0.100, 800.0, 4, "mid", 1.4, True),
        SchedRun("Camera", "camera_hal", "CameraThread", 1, 0.133, 700.0, 4, "mid", 1.4, True),
        SchedRun("Camera", "camera_hal", "CameraThread", 1, 0.166, 650.0, 4, "mid", 1.4, True),
    ]
    freq_series = {"mid": ([0.0, 0.10, 0.20, 0.30], [1.0, 1.4, 1.4, 1.0])}

    windows = _detect_clock_ramp_windows(freq_series, runs)

    assert len(windows) == 1
    assert windows[0].attribution == "periodic_target_migration"
    assert windows[0].target_migrations_into_cluster == 1
    assert windows[0].periodicity_score >= 0.9
