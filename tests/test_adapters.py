"""Tests for adapters (GitHub, git hooks, file watcher)."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from kindex.config import Config
from kindex.store import Store


@pytest.fixture
def store(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    s = Store(cfg)
    yield s
    s.close()


class TestGitHubAdapter:
    def test_gh_not_available(self):
        from kindex.adapters.github import is_gh_available
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert is_gh_available() is False

    def test_ingest_issues_mock(self, store):
        """Mock gh CLI to test issue ingestion."""
        from kindex.adapters.github import ingest_issues

        mock_issues = [
            {
                "number": 1,
                "title": "Test issue",
                "body": "Issue body text",
                "state": "OPEN",
                "labels": [{"name": "bug"}],
                "author": {"login": "testuser"},
                "createdAt": "2026-02-24T10:00:00Z",
                "url": "https://github.com/test/repo/issues/1"
            }
        ]

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_issues)

        with patch("subprocess.run", return_value=mock_result):
            count = ingest_issues(store, "test/repo")

        assert count == 1
        node = store.get_node("gh-issue-test-repo-1")
        assert node is not None
        assert "#1: Test issue" in node["title"]
        assert "github" in node.get("domains", [])

    def test_ingest_prs_mock(self, store):
        from kindex.adapters.github import ingest_prs

        mock_prs = [
            {
                "number": 10,
                "title": "Add feature",
                "body": "PR description",
                "state": "MERGED",
                "labels": [],
                "author": {"login": "dev"},
                "createdAt": "2026-02-23T10:00:00Z",
                "url": "https://github.com/test/repo/pulls/10",
                "mergedAt": "2026-02-24T10:00:00Z"
            }
        ]

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_prs)

        with patch("subprocess.run", return_value=mock_result):
            count = ingest_prs(store, "test/repo")

        assert count == 1
        node = store.get_node("gh-pr-test-repo-10")
        assert node is not None
        assert node["status"] == "archived"  # merged

    def test_ingest_issues_skips_existing(self, store):
        """Already-ingested issues should be skipped."""
        from kindex.adapters.github import ingest_issues

        # Pre-create the node
        store.add_node("Test issue", node_id="gh-issue-test-repo-1")

        mock_issues = [{"number": 1, "title": "Test issue", "body": "",
                        "state": "OPEN", "labels": [], "author": {"login": "x"},
                        "createdAt": "2026-02-24T10:00:00Z", "url": ""}]

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_issues)

        with patch("subprocess.run", return_value=mock_result):
            count = ingest_issues(store, "test/repo")

        assert count == 0  # skipped

    def test_ingest_issues_returns_zero_on_failure(self, store):
        """If gh command fails, return 0."""
        from kindex.adapters.github import ingest_issues

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            count = ingest_issues(store, "test/repo")

        assert count == 0

    def test_ingest_prs_closed_is_archived(self, store):
        """CLOSED PRs should have status=archived."""
        from kindex.adapters.github import ingest_prs

        mock_prs = [
            {
                "number": 5,
                "title": "Closed PR",
                "body": "",
                "state": "CLOSED",
                "labels": [],
                "author": {"login": "dev"},
                "createdAt": "2026-02-20T10:00:00Z",
                "url": "https://github.com/test/repo/pulls/5",
                "mergedAt": None
            }
        ]

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_prs)

        with patch("subprocess.run", return_value=mock_result):
            count = ingest_prs(store, "test/repo")

        assert count == 1
        node = store.get_node("gh-pr-test-repo-5")
        assert node["status"] == "archived"

    def test_ingest_prs_open_is_active(self, store):
        """OPEN PRs should have status=active."""
        from kindex.adapters.github import ingest_prs

        mock_prs = [
            {
                "number": 7,
                "title": "Open PR",
                "body": "WIP",
                "state": "OPEN",
                "labels": [{"name": "enhancement"}],
                "author": {"login": "dev"},
                "createdAt": "2026-02-24T10:00:00Z",
                "url": "https://github.com/test/repo/pulls/7",
                "mergedAt": None
            }
        ]

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_prs)

        with patch("subprocess.run", return_value=mock_result):
            count = ingest_prs(store, "test/repo")

        assert count == 1
        node = store.get_node("gh-pr-test-repo-7")
        assert node["status"] == "active"

    def test_gh_available_when_authenticated(self):
        """When gh auth status succeeds, is_gh_available returns True."""
        from kindex.adapters.github import is_gh_available

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            assert is_gh_available() is True

    def test_gh_timeout_returns_false(self):
        """Timeout during gh auth check returns False."""
        from kindex.adapters.github import is_gh_available

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=5)):
            assert is_gh_available() is False


class TestGitHooksAdapter:
    def test_install_hooks(self, tmp_path):
        """Install git hooks in a mock repo."""
        from kindex.adapters.git_hooks import install_hooks

        # Create a mock git repo structure
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)

        cfg = Config(data_dir=str(tmp_path))
        actions = install_hooks(str(tmp_path), cfg)

        assert any("post-commit" in a for a in actions)
        assert any("pre-push" in a for a in actions)

        # Verify hooks are executable
        assert (git_dir / "post-commit").exists()
        assert (git_dir / "pre-push").exists()

    def test_install_hooks_idempotent(self, tmp_path):
        from kindex.adapters.git_hooks import install_hooks

        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)

        cfg = Config(data_dir=str(tmp_path))
        install_hooks(str(tmp_path), cfg)
        actions2 = install_hooks(str(tmp_path), cfg)

        assert any("already" in a for a in actions2)

    def test_uninstall_hooks(self, tmp_path):
        from kindex.adapters.git_hooks import install_hooks, uninstall_hooks

        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)

        cfg = Config(data_dir=str(tmp_path))
        install_hooks(str(tmp_path), cfg)
        actions = uninstall_hooks(str(tmp_path))

        assert any("Removed" in a for a in actions)

    def test_install_hooks_not_git_repo(self, tmp_path):
        """Should return error if not a git repo."""
        from kindex.adapters.git_hooks import install_hooks

        cfg = Config(data_dir=str(tmp_path))
        actions = install_hooks(str(tmp_path), cfg)

        assert any("not a git repository" in a for a in actions)

    def test_ingest_recent_commits_mock(self, store):
        from kindex.adapters.git_hooks import ingest_recent_commits

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "abc12345|Add feature X|John Doe|2026-02-24T10:00:00-05:00\ndef67890|Fix bug in Y|Jane|2026-02-23T10:00:00-05:00\n"

        with patch("subprocess.run", return_value=mock_result):
            count = ingest_recent_commits(store, "/tmp/fake-repo")

        assert count == 2
        assert store.get_node("commit-abc12345") is not None
        assert store.get_node("commit-def67890") is not None

    def test_ingest_commits_skips_existing(self, store):
        """Already-ingested commits should be skipped."""
        from kindex.adapters.git_hooks import ingest_recent_commits

        # Pre-create the node
        store.add_node("Add feature X", node_id="commit-abc12345")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "abc12345|Add feature X|John Doe|2026-02-24T10:00:00-05:00\n"

        with patch("subprocess.run", return_value=mock_result):
            count = ingest_recent_commits(store, "/tmp/fake-repo")

        assert count == 0

    def test_ingest_commits_git_not_available(self, store):
        """Should return 0 if git is not available."""
        from kindex.adapters.git_hooks import ingest_recent_commits

        with patch("subprocess.run", side_effect=FileNotFoundError):
            count = ingest_recent_commits(store, "/tmp/fake-repo")

        assert count == 0

    def test_uninstall_no_hooks(self, tmp_path):
        """Uninstall when no hooks exist should report nothing found."""
        from kindex.adapters.git_hooks import uninstall_hooks

        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)

        actions = uninstall_hooks(str(tmp_path))
        assert any("No Kindex hooks found" in a for a in actions)


class TestFileAdapter:
    def test_sha256_file(self, tmp_path):
        from kindex.adapters.files import sha256_file

        f = tmp_path / "test.txt"
        f.write_text("hello world")

        h = sha256_file(f)
        assert len(h) == 64  # SHA-256 hex digest

        # Same content = same hash
        f2 = tmp_path / "test2.txt"
        f2.write_text("hello world")
        assert sha256_file(f2) == h

    def test_sha256_different_content(self, tmp_path):
        from kindex.adapters.files import sha256_file

        f1 = tmp_path / "a.txt"
        f1.write_text("hello")

        f2 = tmp_path / "b.txt"
        f2.write_text("world")

        assert sha256_file(f1) != sha256_file(f2)

    def test_scan_registered_files(self, store, tmp_path):
        from kindex.adapters.files import scan_registered_files

        # Create a file and register it
        test_file = tmp_path / "notes.txt"
        test_file.write_text("Initial content")

        nid = store.add_node("My Notes",
                             extra={"file_paths": [str(test_file)]})

        # First scan should detect the file
        count = scan_registered_files(store)
        assert count == 1

        # Same content, no change
        count2 = scan_registered_files(store)
        assert count2 == 0

        # Modify the file
        test_file.write_text("Updated content!!!")
        count3 = scan_registered_files(store)
        assert count3 == 1

    def test_scan_registered_files_missing_file(self, store, tmp_path):
        """Missing files should not crash the scan."""
        from kindex.adapters.files import scan_registered_files

        nid = store.add_node("Ghost Notes",
                             extra={"file_paths": [str(tmp_path / "nonexistent.txt")]})

        count = scan_registered_files(store)
        assert count == 0

    def test_ingest_directory(self, store, tmp_path):
        from kindex.adapters.files import ingest_directory

        # Create some test files
        (tmp_path / "notes.md").write_text("# My Notes\nSome content")
        (tmp_path / "todo.txt").write_text("TODO: finish tests")
        (tmp_path / "code.py").write_text("# Not included by default")

        count = ingest_directory(store, tmp_path)
        assert count == 2  # md and txt only

        # Verify node content
        nodes = store.all_nodes(node_type="document")
        titles = [n["title"] for n in nodes]
        assert any("Notes" in t for t in titles)

    def test_ingest_directory_custom_extensions(self, store, tmp_path):
        from kindex.adapters.files import ingest_directory

        (tmp_path / "script.py").write_text("print('hello')")

        count = ingest_directory(store, tmp_path, extensions=[".py"])
        assert count == 1

    def test_ingest_directory_skips_hidden(self, store, tmp_path):
        """Hidden/dot directories should be skipped."""
        from kindex.adapters.files import ingest_directory

        hidden_dir = tmp_path / ".hidden"
        hidden_dir.mkdir()
        (hidden_dir / "secret.md").write_text("secret")
        (tmp_path / "visible.md").write_text("visible")

        count = ingest_directory(store, tmp_path)
        assert count == 1  # only visible.md

    def test_ingest_directory_idempotent(self, store, tmp_path):
        """Running ingest twice should not create duplicate nodes."""
        from kindex.adapters.files import ingest_directory

        (tmp_path / "notes.md").write_text("# Notes")

        count1 = ingest_directory(store, tmp_path)
        assert count1 == 1

        count2 = ingest_directory(store, tmp_path)
        assert count2 == 0  # already exists

    def test_ingest_directory_nonexistent(self, store, tmp_path):
        """Non-existent directory should return 0."""
        from kindex.adapters.files import ingest_directory

        count = ingest_directory(store, tmp_path / "does-not-exist")
        assert count == 0

    def test_ingest_directory_stores_file_hash(self, store, tmp_path):
        """Ingested files should have file_hashes in extra."""
        from kindex.adapters.files import ingest_directory

        (tmp_path / "readme.md").write_text("Hello")
        ingest_directory(store, tmp_path)

        nodes = store.all_nodes(node_type="document")
        assert len(nodes) == 1
        extra = nodes[0].get("extra", {})
        assert "file_hashes" in extra
        assert "file_paths" in extra


class TestIngestCLI:
    def test_ingest_files(self, tmp_path):
        d = str(tmp_path)

        def run(*args, data_dir=None):
            cmd = [sys.executable, "-m", "kindex.cli", *args]
            if data_dir:
                cmd.extend(["--data-dir", data_dir])
            return subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        run("init", data_dir=d)
        r = run("ingest", "files", data_dir=d)
        assert r.returncode == 0

    def test_ingest_commits(self, tmp_path):
        d = str(tmp_path)

        def run(*args, data_dir=None):
            cmd = [sys.executable, "-m", "kindex.cli", *args]
            if data_dir:
                cmd.extend(["--data-dir", data_dir])
            return subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        run("init", data_dir=d)
        r = run("ingest", "commits", data_dir=d)
        assert r.returncode == 0


class TestGitHookCLI:
    def test_git_hook_install(self, tmp_path):
        # Create mock git repo
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)

        d = str(tmp_path)

        def run(*args, data_dir=None):
            cmd = [sys.executable, "-m", "kindex.cli", *args]
            if data_dir:
                cmd.extend(["--data-dir", data_dir])
            return subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        run("init", data_dir=d)
        r = run("git-hook", "install", "--repo-path", str(tmp_path), data_dir=d)
        assert r.returncode == 0
        assert (git_dir / "post-commit").exists()

    def test_git_hook_uninstall(self, tmp_path):
        """Install then uninstall should work."""
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)

        d = str(tmp_path)

        def run(*args, data_dir=None):
            cmd = [sys.executable, "-m", "kindex.cli", *args]
            if data_dir:
                cmd.extend(["--data-dir", data_dir])
            return subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        run("init", data_dir=d)
        run("git-hook", "install", "--repo-path", str(tmp_path), data_dir=d)
        r = run("git-hook", "uninstall", "--repo-path", str(tmp_path), data_dir=d)
        assert r.returncode == 0
