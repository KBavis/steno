from pathlib import Path

from steno.poc.config_loader import read_config

EXAMPLE = Path(__file__).parents[2] / "config" / "steno.example.yaml"


def test_example_config_parses():
    config = read_config(EXAMPLE)
    assert config.organization
    assert {r.connector for r in config.repositories} <= {c.name for c in config.connectors}
