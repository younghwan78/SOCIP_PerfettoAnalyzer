from pathlib import Path

from soc_perfetto_analyzer.config import load_event_config


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
