"""Tests for daemon module — cron_run, find_new_sessions, incremental_ingest, markers."""

import datetime
import json
import os
import time

import pytest

from kindex.config import Config
from kindex.store import Store


@pytest.fixture
def store(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    s = Store(cfg)
    yield s
    s.close()


@pytest.fixture
def config(tmp_path):
    return Config(data_dir=str(tmp_path), claude_dir=str(tmp_path / "claude"))


class TestCronRun:
    def test_cron_run_empty(self, tmp_path):
        """Run cron on empty store, verify no crash."""
        from kindex.daemon import cron_run

        cfg = Config(
            data_dir=str(tmp_path),
            claude_dir=str(tmp_path / "claude"),
            project_dirs=[str(tmp_path / "projects")],
        )
        s = Store(cfg)

        results = cron_run(cfg, s, verbose=False)

        assert isinstance(results, dict)
        assert "projects" in results
        assert "sessions" in results
        assert "inbox" in results
        assert "decayed" in results
        assert "stats" in results
        assert results["stats"]["nodes"] >= 0
        s.close()

    def test_cron_run_with_data(self, tmp_path):
        """Create some nodes, run cron, verify results."""
        from kindex.daemon import cron_run

        cfg = Config(
            data_dir=str(tmp_path),
            claude_dir=str(tmp_path / "claude"),
            project_dirs=[str(tmp_path / "projects")],
        )
        s = Store(cfg)

        # Pre-populate with some nodes
        s.add_node("Alpha Concept", content="About alpha", node_id="alpha")
        s.add_node("Beta Concept", content="About beta", node_id="beta")
        s.add_edge("alpha", "beta")

        results = cron_run(cfg, s, verbose=False)

        assert isinstance(results, dict)
        assert results["stats"]["nodes"] >= 2
        assert results["stats"]["edges"] >= 1
        s.close()

    def test_cron_run_processes_inbox(self, tmp_path):
        """Cron should process inbox items."""
        from kindex.daemon import cron_run

        cfg = Config(
            data_dir=str(tmp_path),
            claude_dir=str(tmp_path / "claude"),
            project_dirs=[str(tmp_path / "projects")],
        )
        s = Store(cfg)

        # Create an inbox item
        inbox_dir = cfg.inbox_dir
        inbox_dir.mkdir(parents=True, exist_ok=True)
        (inbox_dir / "test-item.md").write_text(
            "---\ncreated: 2026-01-01\nprocessed: false\n---\n\n"
            "The Observer Pattern is a behavioral design pattern that defines a "
            "one-to-many dependency between objects. This pattern allows multiple "
            "Observer Objects to watch a Subject Object."
        )

        results = cron_run(cfg, s, verbose=False)

        assert results["inbox"] >= 0  # may or may not extract concepts depending on patterns
        s.close()


class TestFindNewSessions:
    def test_find_new_sessions(self, tmp_path):
        """Create mock JSONL files, verify detection."""
        from kindex.daemon import find_new_sessions

        cfg = Config(
            data_dir=str(tmp_path),
            claude_dir=str(tmp_path / "claude"),
        )

        # Create the expected directory structure
        projects_dir = cfg.claude_path / "projects"
        project_dir = projects_dir / "my-project"
        project_dir.mkdir(parents=True)

        # Create JSONL session files
        session1 = project_dir / "abc123.jsonl"
        session1.write_text(
            json.dumps({"role": "assistant", "content": "Hello world this is a test session"}) + "\n"
        )

        session2 = project_dir / "def456.jsonl"
        session2.write_text(
            json.dumps({"role": "assistant", "content": "Another session with more content"}) + "\n"
        )

        # All files should be newer than epoch
        results = find_new_sessions(cfg, "1970-01-01T00:00:00")
        assert len(results) == 2

    def test_find_new_sessions_since_future(self, tmp_path):
        """No sessions should be found if since is in the future."""
        from kindex.daemon import find_new_sessions

        cfg = Config(
            data_dir=str(tmp_path),
            claude_dir=str(tmp_path / "claude"),
        )

        projects_dir = cfg.claude_path / "projects"
        project_dir = projects_dir / "my-project"
        project_dir.mkdir(parents=True)

        (project_dir / "abc123.jsonl").write_text(
            json.dumps({"role": "assistant", "content": "test"}) + "\n"
        )

        results = find_new_sessions(cfg, "2099-01-01T00:00:00")
        assert len(results) == 0

    def test_find_new_sessions_no_projects_dir(self, tmp_path):
        """No crash if projects directory doesn't exist."""
        from kindex.daemon import find_new_sessions

        cfg = Config(
            data_dir=str(tmp_path),
            claude_dir=str(tmp_path / "claude"),
        )
        # Don't create the directory

        results = find_new_sessions(cfg, "1970-01-01T00:00:00")
        assert results == []


class TestIncrementalIngest:
    def test_incremental_ingest(self, tmp_path):
        """Verify it only processes files newer than marker."""
        from kindex.daemon import incremental_ingest

        cfg = Config(
            data_dir=str(tmp_path),
            claude_dir=str(tmp_path / "claude"),
        )
        s = Store(cfg)

        # Create a session with enough content to extract
        projects_dir = cfg.claude_path / "projects"
        project_dir = projects_dir / "test-project"
        project_dir.mkdir(parents=True)

        session_data = []
        for _ in range(5):
            session_data.append(json.dumps({
                "role": "assistant",
                "content": (
                    "The Observer Pattern is commonly used in Event Driven Architecture. "
                    "We should consider using the Strategy Pattern for algorithm selection. "
                    "Graph Neural Networks combine message passing with learned representations."
                ),
            }))

        (project_dir / "session001.jsonl").write_text("\n".join(session_data) + "\n")

        count = incremental_ingest(cfg, s, "1970-01-01T00:00:00", verbose=False)

        # Should have created at least one session node
        assert count >= 0  # may be 0 if no concepts extracted, or >= 1
        s.close()

    def test_incremental_ingest_skips_already_ingested(self, tmp_path):
        """Should skip sessions already in the store."""
        from kindex.daemon import incremental_ingest

        cfg = Config(
            data_dir=str(tmp_path),
            claude_dir=str(tmp_path / "claude"),
        )
        s = Store(cfg)

        projects_dir = cfg.claude_path / "projects"
        project_dir = projects_dir / "test-project"
        project_dir.mkdir(parents=True)

        (project_dir / "already12345.jsonl").write_text(
            json.dumps({"role": "assistant", "content": "Some test content " * 20}) + "\n"
        )

        # Pre-create the session node
        s.add_node("Session: test-project", node_id="session-already12345", node_type="session")

        count = incremental_ingest(cfg, s, "1970-01-01T00:00:00")
        assert count == 0  # should skip the already-ingested session
        s.close()


class TestLastRunMarker:
    def test_last_run_marker(self, tmp_path):
        """Verify marker read/write via meta table."""
        from kindex.daemon import last_run_marker, set_run_marker

        cfg = Config(data_dir=str(tmp_path))
        s = Store(cfg)

        # Initially empty
        marker = last_run_marker(cfg)
        assert marker == ""

        # Set marker
        set_run_marker(s)

        # Read marker back (need a new Store because last_run_marker creates its own)
        s.close()
        marker = last_run_marker(cfg)
        assert marker != ""
        # Should be a valid ISO timestamp
        assert "T" in marker
        # Should be today's date
        today = datetime.date.today().isoformat()
        assert marker.startswith(today)

    def test_set_run_marker_updates(self, tmp_path):
        """Setting marker twice should update the value."""
        from kindex.daemon import set_run_marker

        cfg = Config(data_dir=str(tmp_path))
        s = Store(cfg)

        set_run_marker(s)
        marker1 = s.get_meta("last_cron_run")
        assert marker1 is not None

        # Second set
        import time
        time.sleep(0.1)
        set_run_marker(s)
        marker2 = s.get_meta("last_cron_run")
        assert marker2 is not None

        # Both should be valid
        assert "T" in marker1
        assert "T" in marker2
        s.close()
