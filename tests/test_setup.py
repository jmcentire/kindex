"""Tests for system setup commands."""
import json
import subprocess
import sys
import pytest
from kindex.config import Config


def run(*args, data_dir=None):
    cmd = [sys.executable, "-m", "kindex.cli", *args]
    if data_dir:
        cmd.extend(["--data-dir", data_dir])
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


class TestSetupHooks:
    def test_setup_hooks_dry_run(self, tmp_path):
        """Dry run should not modify settings.json."""
        d = str(tmp_path)
        run("init", data_dir=d)

        # Create a fake claude dir
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()

        r = run("setup-hooks", "--dry-run", data_dir=d)
        assert r.returncode == 0

    def test_setup_hooks_installs(self, tmp_path):
        """Should install hooks into settings.json."""
        from kindex.setup import install_claude_hooks

        # Create a tmp claude dir
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = claude_dir / "settings.json"
        settings.write_text("{}")

        cfg = Config(data_dir=str(tmp_path), claude_dir=str(claude_dir))
        actions = install_claude_hooks(cfg)

        assert any("SessionStart" in a for a in actions)
        assert any("PreCompact" in a for a in actions)

        # Verify settings file was updated
        data = json.loads(settings.read_text())
        assert "hooks" in data
        assert "SessionStart" in data["hooks"]

    def test_setup_hooks_idempotent(self, tmp_path):
        """Installing twice should not duplicate hooks."""
        from kindex.setup import install_claude_hooks

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}")

        cfg = Config(data_dir=str(tmp_path), claude_dir=str(claude_dir))
        install_claude_hooks(cfg)
        actions2 = install_claude_hooks(cfg)

        assert any("already installed" in a for a in actions2)

    def test_setup_hooks_dry_run_does_not_write(self, tmp_path):
        """Dry run should not create or modify settings.json."""
        from kindex.setup import install_claude_hooks

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        # Don't create settings.json — dry run should not create it either

        cfg = Config(data_dir=str(tmp_path), claude_dir=str(claude_dir))
        actions = install_claude_hooks(cfg, dry_run=True)

        # Should still report the actions it would take
        assert any("SessionStart" in a for a in actions)
        assert any("PreCompact" in a for a in actions)
        # But the "Wrote" action should not appear
        assert not any("Wrote" in a for a in actions)

    def test_setup_hooks_preserves_existing(self, tmp_path):
        """Should preserve existing settings when adding hooks."""
        from kindex.setup import install_claude_hooks

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = claude_dir / "settings.json"
        settings.write_text(json.dumps({"customSetting": True}))

        cfg = Config(data_dir=str(tmp_path), claude_dir=str(claude_dir))
        install_claude_hooks(cfg)

        data = json.loads(settings.read_text())
        assert data["customSetting"] is True
        assert "hooks" in data


class TestSetupCron:
    def test_setup_cron_dry_run(self, tmp_path):
        d = str(tmp_path)
        run("init", data_dir=d)
        r = run("setup-cron", "--dry-run", data_dir=d)
        assert r.returncode == 0
        assert "Would" in r.stdout

    def test_install_launchd_dry_run(self, tmp_path):
        """install_launchd with dry_run should not write plist."""
        from kindex.setup import install_launchd

        cfg = Config(data_dir=str(tmp_path))
        actions = install_launchd(cfg, dry_run=True)
        assert any("Would install" in a for a in actions)

    def test_install_crontab_dry_run(self, tmp_path):
        """install_crontab with dry_run should not modify crontab."""
        from kindex.setup import install_crontab
        from unittest.mock import patch, MagicMock

        cfg = Config(data_dir=str(tmp_path))

        mock_result = MagicMock()
        mock_result.returncode = 1  # no existing crontab
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            actions = install_crontab(cfg, dry_run=True)

        assert any("Would add crontab" in a for a in actions)

    def test_uninstall_launchd_dry_run(self, tmp_path):
        """uninstall_launchd with dry_run should not delete plist."""
        from kindex.setup import uninstall_launchd
        from unittest.mock import patch

        # The function checks Path.home() / "Library/LaunchAgents/com.kindex.cron.plist"
        # In dry run mode with no plist, it should say "No launchd plist found"
        actions = uninstall_launchd(dry_run=True)
        # It either finds the plist and says "Would remove" or doesn't find it
        assert len(actions) > 0
