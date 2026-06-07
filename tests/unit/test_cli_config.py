from soc_perfetto_analyzer.cli import _load_config


def test_cli_load_config_applies_topology_override(tmp_path):
    event_config = tmp_path / "event_config.yaml"
    event_config.write_text(
        """
events: []
cpu_topology:
  source: event_file
  clusters:
    - name: event_cluster
      cpus: [0]
""",
        encoding="utf-8",
    )
    topology_config = tmp_path / "topology.yaml"
    topology_config.write_text(
        """
source: override_file
clusters:
  - name: override_cluster
    cpus: [0, 1]
""",
        encoding="utf-8",
    )

    config = _load_config(event_config, topology_config)

    assert config.cpu_topology.source == "override_file"
    assert config.cpu_topology.cluster_for_cpu(1) == "override_cluster"
