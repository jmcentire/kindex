"""Tests for project-scoped .kin config and work policy."""

import os
import subprocess
import sys
from pathlib import Path

from kindex.config import load_config, resolve_project_root


def _write_kin_config(directory: Path, content: str) -> Path:
    kin_dir = directory / ".kin"
    kin_dir.mkdir(exist_ok=True)
    config = kin_dir / "config"
    config.write_text(content)
    return config


def test_load_config_from_explicit_project_path(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _write_kin_config(
        project,
        "data_dir: /tmp/kindex-project\n"
        "work_policy:\n"
        "  require_active_tag: true\n"
        "  linear:\n"
        "    enabled: true\n"
        "    require_issue: true\n"
        "    team: ENG\n",
    )

    cfg = load_config(project_path=project)

    assert cfg.data_dir == "/tmp/kindex-project"
    assert cfg.work_policy.require_active_tag is True
    assert cfg.work_policy.linear.enabled is True
    assert cfg.work_policy.linear.require_issue is True
    assert cfg.work_policy.linear.team == "ENG"


def test_kin_project_config_inheritance_merges_lists_and_policy(tmp_path):
    org = tmp_path / "org"
    project = tmp_path / "project"
    org.mkdir()
    project.mkdir()
    org_config = _write_kin_config(
        org,
        "domains: [engineering]\n"
        "work_policy:\n"
        "  require_active_tag: true\n",
    )
    _write_kin_config(
        project,
        f"inherits:\n  - {org_config}\n"
        "domains: [python]\n"
        "work_policy:\n"
        "  linear:\n"
        "    enabled: true\n",
    )

    cfg = load_config(project_path=project)

    assert cfg.work_policy.require_active_tag is True
    assert cfg.work_policy.linear.enabled is True


def test_resolve_project_root_prefers_kin_project_env(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("KIN_PROJECT", str(project))

    assert resolve_project_root() == project.resolve()


def test_policy_check_allows_absent_policy(tmp_path):
    data_dir = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    _write_kin_config(project, "name: personal-project\n")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "kindex.cli",
            "policy",
            "check",
            "--project-path",
            str(project),
            "--data-dir",
            str(data_dir),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert "Policy check passed" in result.stdout


def test_policy_check_blocks_linear_only_when_enabled(tmp_path):
    data_dir = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    _write_kin_config(
        project,
        "work_policy:\n"
        "  linear:\n"
        "    enabled: true\n"
        "    require_issue: true\n",
    )

    env = os.environ.copy()
    env.pop("KIN_LINEAR_ID", None)
    env.pop("LINEAR_ISSUE", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "kindex.cli",
            "policy",
            "check",
            "--strict",
            "--project-path",
            str(project),
            "--data-dir",
            str(data_dir),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    assert result.returncode == 1
    assert "Linear issue required" in result.stderr


def test_config_set_with_project_path_writes_project_kin_config(tmp_path):
    data_dir = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "kindex.cli",
            "config",
            "set",
            "work_policy.require_active_tag",
            "true",
            "--project-path",
            str(project),
            "--data-dir",
            str(data_dir),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert (project / ".kin" / "config").exists()
    cfg = load_config(project_path=project)
    assert cfg.work_policy.require_active_tag is True
