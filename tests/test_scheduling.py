"""Tests for the adaptive scheduling system."""

from __future__ import annotations

import datetime

import pytest

from kindex.config import Config, ReminderConfig, ScheduleTier
from kindex.store import Store


@pytest.fixture
def store(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    s = Store(cfg)
    yield s
    s.close()


@pytest.fixture
def config(tmp_path):
    return Config(data_dir=str(tmp_path))


def _future(seconds: int) -> str:
    """ISO timestamp N seconds from now."""
    return (datetime.datetime.now() + datetime.timedelta(seconds=seconds)).isoformat(
        timespec="seconds"
    )


def _past(seconds: int) -> str:
    """ISO timestamp N seconds ago."""
    return (datetime.datetime.now() - datetime.timedelta(seconds=seconds)).isoformat(
        timespec="seconds"
    )


# ── nearest_pending_reminder ──────────────────────────────────────


class TestNearestPendingReminder:
    def test_no_reminders(self, store):
        assert store.nearest_pending_reminder() is None

    def test_single_active(self, store):
        due = _future(3600)
        store.add_reminder("Test", due)
        result = store.nearest_pending_reminder()
        assert result == due

    def test_picks_nearest(self, store):
        far = _future(86400)
        near = _future(600)
        store.add_reminder("Far", far)
        store.add_reminder("Near", near)
        assert store.nearest_pending_reminder() == near

    def test_ignores_completed(self, store):
        due = _future(3600)
        rid = store.add_reminder("Done", due)
        store.complete_reminder(rid)
        assert store.nearest_pending_reminder() is None

    def test_includes_snoozed(self, store):
        due = _future(86400)
        rid = store.add_reminder("Snoozed", due)
        snooze_until = _future(1800)
        store.snooze_reminder(rid, snooze_until)
        # Should return the snooze_until, which is earlier than next_due
        result = store.nearest_pending_reminder()
        assert result == snooze_until

    def test_ignores_cancelled(self, store):
        due = _future(3600)
        rid = store.add_reminder("Cancelled", due)
        store.update_reminder(rid, status="cancelled")
        assert store.nearest_pending_reminder() is None


# ── compute_optimal_interval ──────────────────────────────────────


class TestComputeOptimalInterval:
    def test_no_reminders_returns_zero(self, store, config):
        from kindex.scheduling import compute_optimal_interval

        assert compute_optimal_interval(store, config) == 0

    def test_far_reminder_daily(self, store, config):
        from kindex.scheduling import compute_optimal_interval

        store.add_reminder("Far", _future(8 * 86400))  # 8 days
        assert compute_optimal_interval(store, config) == 86400

    def test_days_away_hourly(self, store, config):
        from kindex.scheduling import compute_optimal_interval

        store.add_reminder("Days", _future(3 * 86400))  # 3 days
        assert compute_optimal_interval(store, config) == 3600

    def test_hours_away_ten_min(self, store, config):
        from kindex.scheduling import compute_optimal_interval

        store.add_reminder("Hours", _future(4 * 3600))  # 4 hours
        assert compute_optimal_interval(store, config) == 600

    def test_minutes_away_five_min(self, store, config):
        from kindex.scheduling import compute_optimal_interval

        store.add_reminder("Soon", _future(1800))  # 30 min
        assert compute_optimal_interval(store, config) == 300

    def test_past_due_five_min(self, store, config):
        from kindex.scheduling import compute_optimal_interval

        store.add_reminder("Overdue", _past(600))  # 10 min ago
        assert compute_optimal_interval(store, config) == 300

    def test_adaptive_disabled_uses_fixed(self, store, tmp_path):
        from kindex.scheduling import compute_optimal_interval

        cfg = Config(
            data_dir=str(tmp_path),
            reminders=ReminderConfig(
                adaptive_scheduling=False,
                check_interval=600,
            ),
        )
        store.add_reminder("Test", _future(86400 * 10))
        assert compute_optimal_interval(store, cfg) == 600

    def test_custom_tiers(self, store, tmp_path):
        from kindex.scheduling import compute_optimal_interval

        cfg = Config(
            data_dir=str(tmp_path),
            reminders=ReminderConfig(
                schedule_tiers=[
                    ScheduleTier(threshold=3600, interval=1800),
                    ScheduleTier(threshold=0, interval=60),
                ],
                min_interval=60,
            ),
        )
        store.add_reminder("Test", _future(7200))  # 2 hours > 1 hour threshold
        assert compute_optimal_interval(store, cfg) == 1800

    def test_min_interval_respected(self, store, tmp_path):
        from kindex.scheduling import compute_optimal_interval

        cfg = Config(
            data_dir=str(tmp_path),
            reminders=ReminderConfig(
                min_interval=600,
                schedule_tiers=[
                    ScheduleTier(threshold=0, interval=60),  # below min_interval
                ],
            ),
        )
        store.add_reminder("Test", _future(300))
        assert compute_optimal_interval(store, cfg) == 600


# ── nearest_reminder_seconds ──────────────────────────────────────


class TestNearestReminderSeconds:
    def test_no_reminders(self, store):
        from kindex.scheduling import nearest_reminder_seconds

        assert nearest_reminder_seconds(store) is None

    def test_future_reminder(self, store):
        from kindex.scheduling import nearest_reminder_seconds

        store.add_reminder("Test", _future(3600))
        secs = nearest_reminder_seconds(store)
        assert secs is not None
        assert 3500 <= secs <= 3700  # roughly 1 hour

    def test_past_reminder_returns_zero(self, store):
        from kindex.scheduling import nearest_reminder_seconds

        store.add_reminder("Past", _past(600))
        secs = nearest_reminder_seconds(store)
        assert secs == 0


# ── repack_schedule ───────────────────────────────────────────────


class TestRepackSchedule:
    def test_skipped_when_disabled(self, store, tmp_path):
        from kindex.scheduling import repack_schedule

        cfg = Config(
            data_dir=str(tmp_path),
            reminders=ReminderConfig(enabled=False),
        )
        result = repack_schedule(store, cfg)
        assert result["action"] == "skipped"

    def test_unchanged_when_same_interval(self, store, config):
        from kindex.scheduling import repack_schedule

        # Set meta to current expected interval (0 = no reminders)
        store.set_meta("cron_interval", "0")
        result = repack_schedule(store, config)
        assert result["action"] == "unchanged"
        assert result["interval"] == 0

    def test_tracks_interval_in_meta(self, store, config):
        from kindex.scheduling import repack_schedule

        store.add_reminder("Test", _future(1800))
        # First repack should set meta
        result = repack_schedule(store, config)
        assert result["interval"] == 300
        assert store.get_meta("cron_interval") == "300"


# ── ScheduleTier config ──────────────────────────────────────────


class TestScheduleTierConfig:
    def test_default_tiers(self):
        cfg = ReminderConfig()
        assert len(cfg.schedule_tiers) == 4
        # Verify they're sorted by threshold descending in the defaults
        thresholds = [t.threshold for t in cfg.schedule_tiers]
        assert thresholds == [604800, 86400, 3600, 0]

    def test_custom_tiers_from_dict(self):
        cfg = ReminderConfig(
            schedule_tiers=[
                {"threshold": 7200, "interval": 1800},
                {"threshold": 0, "interval": 120},
            ]
        )
        assert len(cfg.schedule_tiers) == 2
        assert cfg.schedule_tiers[0].threshold == 7200
        assert cfg.schedule_tiers[1].interval == 120
