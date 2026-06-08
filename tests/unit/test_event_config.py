from pathlib import Path

from soc_perfetto_analyzer.config import load_cpu_topology_config, load_event_config


def test_legacy_event_config_extracts_task_threads():
    config = load_event_config(Path("event_config.json"))

    target_names = [target.thread for target in config.thread_targets]

    assert "WNC-DnsResult" in target_names
    assert "WNC-SensorHWPro" in target_names
    assert "Uni:PERSONAL_IM" in target_names
    assert "WNC-IspRequest" in target_names
    assert "WNC-EisPlugin" in target_names
    assert config.event_count >= 8
    categories = {target.thread: target.category for target in config.thread_targets}
    assert categories["Uni:PERSONAL_IM"] == "camera_hal"


def test_event_config_yaml_path_is_supported():
    config = load_event_config(Path("event_config.yaml"))

    assert config.config_version == "1.0"
    assert any(target.thread == "WNC-IspRequest" for target in config.thread_targets)
    assert config.cpu_topology.clusters == []


def test_event_config_extracts_optional_cpu_topology(tmp_path):
    config_path = tmp_path / "event_config_with_topology.yaml"
    config_path.write_text(
        """
events:
  - event_name: CameraTask
    event_type: Task
    start_condition:
      event: sched_switch
      match_field: next_comm
      match_value: CameraThread
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
""",
        encoding="utf-8",
    )

    config = load_event_config(config_path)

    assert config.cpu_topology.source == "project_config"
    assert [cluster.name for cluster in config.cpu_topology.clusters] == ["little", "mid"]
    assert config.cpu_topology.clusters[0].cpus == [0, 1]
    assert config.cpu_topology.clusters[0].role == "efficiency"
    assert config.cpu_topology.clusters[0].freq_hint_ghz == (0.3, 1.4)
    assert config.cpu_topology.cluster_for_cpu(5) == "mid"


def test_event_config_extracts_clock_change_report_filter(tmp_path):
    config_path = tmp_path / "event_config_with_clock_filter.yaml"
    config_path.write_text(
        """
events: []
report_filters:
  clock_change:
    baseline: duration_weighted_mean
    ramp_delta_pct: 12.5
    drop_delta_pct: 20
    min_duration_ms: 7
    merge_gap_ms: 4
    max_rows: 12
    include_unknown: true
""",
        encoding="utf-8",
    )

    config = load_event_config(config_path)

    clock_filter = config.report_filters.clock_change
    assert clock_filter.baseline == "duration_weighted_mean"
    assert clock_filter.ramp_delta_pct == 12.5
    assert clock_filter.drop_delta_pct == 20.0
    assert clock_filter.min_duration_ms == 7.0
    assert clock_filter.merge_gap_ms == 4.0
    assert clock_filter.max_rows == 12
    assert clock_filter.include_unknown is True


def test_event_config_uses_default_clock_change_report_filter():
    config = load_event_config(Path("event_config.yaml"))

    clock_filter = config.report_filters.clock_change
    assert clock_filter.baseline == "duration_weighted_mean"
    assert clock_filter.ramp_delta_pct == 15.0
    assert clock_filter.drop_delta_pct == 15.0
    assert clock_filter.min_duration_ms == 5.0
    assert clock_filter.merge_gap_ms == 3.0
    assert clock_filter.max_rows == 20
    assert clock_filter.include_unknown is False


def test_load_cpu_topology_config_accepts_topology_only_file(tmp_path):
    topology_path = tmp_path / "topology.yaml"
    topology_path.write_text(
        """
source: override_file
clusters:
  - name: little
    cpus: [0, 1, 2, 3]
  - name: big
    cpus: [4, 5, 6, 7]
""",
        encoding="utf-8",
    )

    topology = load_cpu_topology_config(topology_path)

    assert topology.source == "override_file"
    assert topology.cluster_for_cpu(6) == "big"
