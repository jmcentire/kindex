"""Tests for the notification channel system."""

from __future__ import annotations

import pytest

from kindex.config import Config
from kindex.notify import (
    NotifyResult,
    SystemChannel,
    SlackChannel,
    EmailChannel,
    ClaudeChannel,
    TerminalChannel,
    dispatch,
    get_channel,
    get_idle_seconds,
    is_user_idle,
)


@pytest.fixture
def config(tmp_path):
    return Config(data_dir=str(tmp_path))


@pytest.fixture
def reminder():
    return {
        "id": "test123",
        "title": "Test Reminder",
        "body": "This is a test",
        "priority": "normal",
    }


class TestSystemChannel:
    def test_is_available_check(self, config):
        ch = SystemChannel()
        # On macOS this should be True; on CI/Linux it may be False
        import platform
        expected = platform.system() == "Darwin"
        assert ch.is_available(config) == expected

    def test_send_fallback(self, config, reminder, monkeypatch):
        ch = SystemChannel()
        # Mock subprocess.run to simulate both failing
        call_count = {"n": 0}

        def mock_run(*args, **kwargs):
            call_count["n"] += 1

            class FakeResult:
                returncode = 1
                stdout = ""
                stderr = ""
            return FakeResult()

        import shutil
        monkeypatch.setattr(shutil, "which", lambda x: None)
        monkeypatch.setattr("kindex.notify.subprocess.run", mock_run)

        result = ch.send(reminder, config)
        # Should have attempted osascript fallback
        assert call_count["n"] >= 1


class TestSlackChannel:
    def test_not_available_without_webhook(self, config):
        ch = SlackChannel()
        assert not ch.is_available(config)

    def test_send_posts_webhook(self, config, reminder, monkeypatch):
        # Enable slack with a fake webhook
        config.reminders.channels.slack.enabled = True
        config.reminders.channels.slack.webhook_url = "https://hooks.slack.com/test"

        ch = SlackChannel()
        assert ch.is_available(config)

        # Mock urlopen
        import urllib.request
        requests_made = []

        def mock_urlopen(req, **kwargs):
            requests_made.append(req)

            class FakeResponse:
                status = 200

                def read(self):
                    return b"ok"
            return FakeResponse()

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

        result = ch.send(reminder, config)
        assert result.success
        assert len(requests_made) == 1


class TestEmailChannel:
    def test_not_available_without_smtp(self, config):
        ch = EmailChannel()
        assert not ch.is_available(config)


class TestClaudeChannel:
    def test_is_available(self, config):
        ch = ClaudeChannel()
        assert ch.is_available(config)

    def test_send_returns_success(self, config, reminder):
        ch = ClaudeChannel()
        result = ch.send(reminder, config)
        assert result.success
        assert "queued" in result.message


class TestTerminalChannel:
    def test_always_available(self, config):
        ch = TerminalChannel()
        assert ch.is_available(config)

    def test_send_prints(self, config, reminder, capsys):
        ch = TerminalChannel()
        result = ch.send(reminder, config)
        assert result.success
        captured = capsys.readouterr()
        assert "Test Reminder" in captured.err


class TestDispatch:
    def test_uses_default_channels(self, config, reminder, monkeypatch):
        sent = []

        def mock_send(self, r, c):
            sent.append(self.name)
            return NotifyResult(True, self.name)

        monkeypatch.setattr(SystemChannel, "send", mock_send)

        results = dispatch(reminder, config)
        # Default channel is "system"
        assert any(r.success for r in results)

    def test_fallback_to_terminal(self, config, reminder, monkeypatch):
        # Set channels to only a failing one
        config.reminders.default_channels = ["slack"]

        def mock_available(self, c):
            return False

        monkeypatch.setattr(SlackChannel, "is_available", mock_available)

        results = dispatch(reminder, config)
        # Terminal fallback should fire
        assert any(r.channel == "terminal" for r in results)

    def test_unknown_channel(self, config, reminder):
        results = dispatch(reminder, config, channel_names=["nonexistent"])
        assert any("Unknown channel" in r.message for r in results)


class TestChannelRegistry:
    def test_get_channel(self):
        for name in ("system", "slack", "email", "claude", "terminal"):
            assert get_channel(name) is not None

    def test_get_nonexistent(self):
        assert get_channel("nonexistent") is None


class TestActivityDetection:
    def test_get_idle_returns_float(self):
        idle = get_idle_seconds()
        assert isinstance(idle, float)
        assert idle >= 0.0

    def test_is_user_idle(self, config):
        # With a very high threshold, user should never be idle
        config.reminders.idle_suppress_after = 999999
        assert not is_user_idle(config)

    def test_is_user_idle_low_threshold(self, config, monkeypatch):
        monkeypatch.setattr("kindex.notify.get_idle_seconds", lambda: 1000.0)
        config.reminders.idle_suppress_after = 500
        assert is_user_idle(config)
