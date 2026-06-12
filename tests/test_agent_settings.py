import subprocess
import sys

import yaml

from kindex.agent_settings import (
    agent_setting_value,
    agent_settings_summary,
    apply_agent_overrides,
)
from kindex.config import Config


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "kindex.cli", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_agent_overrides_apply_client_then_instance_without_mutating_base(tmp_path):
    cfg = Config(
        data_dir=str(tmp_path),
        agents={
            "clients": {
                "claude": {
                    "attention": {"tick_interval": 4, "display": "quiet"},
                    "hooks": {"prime_tokens": 900},
                },
            },
            "instances": {
                "claude:session-a": {
                    "client": "claude",
                    "attention": {"tick_interval": 1},
                    "sim": {"tick_interval": 2},
                    "hooks": {"prime_tokens": 1200},
                },
            },
        },
    )

    effective = apply_agent_overrides(
        cfg,
        client="claude",
        instance_key="claude:session-a",
    )

    assert cfg.attention.tick_interval != 1
    assert effective.attention.tick_interval == 1
    assert effective.attention.display == "quiet"
    assert effective.sim.tick_interval == 2
    assert agent_setting_value(
        cfg,
        client="claude",
        instance_key="claude:session-a",
        key="hooks.prime_tokens",
        default=750,
    ) == 1200


def test_agent_settings_summary_includes_effective_values(tmp_path):
    cfg = Config(
        data_dir=str(tmp_path),
        agents={
            "clients": {
                "antigravity": {"attention": {"tick_interval": 3}},
            },
        },
    )

    summary = agent_settings_summary(cfg, client="antigravity")

    assert summary["client"] == "antigravity"
    assert summary["client_overrides"]["attention.tick_interval"] == 3
    assert summary["effective"]["attention"]["tick_interval"] == 3


def test_agent_config_set_writes_client_override(tmp_path):
    config_path = tmp_path / "kin.yaml"

    result = run(
        "agent-config",
        "set",
        "attention.tick_interval",
        "2",
        "--client",
        "claude",
        "--config",
        str(config_path),
    )

    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(config_path.read_text())
    assert data["agents"]["clients"]["claude"]["attention"]["tick_interval"] == 2


def test_agent_config_set_writes_instance_override(tmp_path):
    config_path = tmp_path / "kin.yaml"

    result = run(
        "agent-config",
        "set",
        "sim.tick_interval",
        "3",
        "--client",
        "claude",
        "--scope",
        "instance",
        "--instance",
        "session-a",
        "--config",
        str(config_path),
    )

    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(config_path.read_text())
    instance = data["agents"]["instances"]["claude:session-a"]
    assert instance["client"] == "claude"
    assert instance["sim"]["tick_interval"] == 3


def test_agent_config_rejects_unsupported_keys(tmp_path):
    config_path = tmp_path / "kin.yaml"

    result = run(
        "agent-config",
        "set",
        "data_dir",
        "/tmp/nope",
        "--client",
        "claude",
        "--config",
        str(config_path),
    )

    assert result.returncode != 0
    assert "Unsupported agent setting" in result.stderr
    assert not config_path.exists()
