"""Tests for .kin file inheritance and resolution chain."""

from pathlib import Path

import pytest

from kindex.ingest import load_project_context, merge_kin_chain, resolve_kin_chain


@pytest.fixture
def kin_tree(tmp_path):
    """Create a hierarchy of .kin files for testing inheritance."""
    # Root org .kin
    org_dir = tmp_path / "org"
    org_dir.mkdir()
    (org_dir / ".kin").write_text(
        "name: acme-corp\n"
        "audience: team\n"
        "domains: [engineering]\n"
        "privacy: team\n"
    )

    # Team .kin (inherits from org)
    team_dir = tmp_path / "team"
    team_dir.mkdir()
    (team_dir / ".kin").write_text(
        "name: platform-team\n"
        "audience: team\n"
        "domains: [platform, infrastructure]\n"
        f"inherits:\n  - {org_dir / '.kin'}\n"
    )

    # Project .kin (inherits from team)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".kin").write_text(
        "name: wander-integrations\n"
        "audience: team\n"
        "domains: [integrations, python]\n"
        f"inherits:\n  - {team_dir / '.kin'}\n"
        "shared_with:\n  - team: engineering\n"
    )

    # Personal .kin (never inherits upward)
    personal_dir = tmp_path / "personal"
    personal_dir.mkdir()
    (personal_dir / ".kin").write_text(
        "name: personal-notes\n"
        "audience: private\n"
        "domains: [personal, health]\n"
    )

    return {
        "org": org_dir,
        "team": team_dir,
        "project": project_dir,
        "personal": personal_dir,
    }


class TestResolveKinChain:
    def test_single_file(self, kin_tree):
        chain = resolve_kin_chain(kin_tree["personal"] / ".kin")
        assert len(chain) == 1
        assert chain[0]["name"] == "personal-notes"

    def test_two_level_inheritance(self, kin_tree):
        chain = resolve_kin_chain(kin_tree["team"] / ".kin")
        assert len(chain) == 2
        assert chain[0]["name"] == "platform-team"  # local first
        assert chain[1]["name"] == "acme-corp"       # ancestor second

    def test_three_level_inheritance(self, kin_tree):
        chain = resolve_kin_chain(kin_tree["project"] / ".kin")
        assert len(chain) == 3
        assert chain[0]["name"] == "wander-integrations"
        assert chain[1]["name"] == "platform-team"
        assert chain[2]["name"] == "acme-corp"

    def test_nonexistent_parent(self, tmp_path):
        (tmp_path / ".kin").write_text(
            "name: orphan\ninherits:\n  - /nonexistent/.kin\n"
        )
        chain = resolve_kin_chain(tmp_path / ".kin")
        assert len(chain) == 1  # just the local file

    def test_circular_reference(self, tmp_path):
        a_dir = tmp_path / "a"
        b_dir = tmp_path / "b"
        a_dir.mkdir()
        b_dir.mkdir()
        (a_dir / ".kin").write_text(f"name: a\ninherits:\n  - {b_dir / '.kin'}\n")
        (b_dir / ".kin").write_text(f"name: b\ninherits:\n  - {a_dir / '.kin'}\n")
        chain = resolve_kin_chain(a_dir / ".kin")
        assert len(chain) == 2  # stops at visited

    def test_max_depth(self, tmp_path):
        # Create a deep chain
        dirs = []
        for i in range(10):
            d = tmp_path / f"level{i}"
            d.mkdir()
            dirs.append(d)

        for i, d in enumerate(dirs):
            if i < len(dirs) - 1:
                (d / ".kin").write_text(
                    f"name: level{i}\ninherits:\n  - {dirs[i+1] / '.kin'}\n"
                )
            else:
                (d / ".kin").write_text(f"name: level{i}\n")

        chain = resolve_kin_chain(dirs[0] / ".kin", max_depth=3)
        assert len(chain) <= 3


class TestMergeKinChain:
    def test_local_overrides_ancestor(self):
        chain = [
            {"name": "local", "audience": "private"},
            {"name": "parent", "audience": "team"},
        ]
        merged = merge_kin_chain(chain)
        assert merged["name"] == "local"       # local wins
        assert merged["audience"] == "private"  # local wins

    def test_lists_concatenated(self):
        chain = [
            {"domains": ["python", "api"]},
            {"domains": ["engineering", "python"]},  # python deduped
        ]
        merged = merge_kin_chain(chain)
        assert "python" in merged["domains"]
        assert "api" in merged["domains"]
        assert "engineering" in merged["domains"]
        # No duplicates
        assert len([d for d in merged["domains"] if d == "python"]) == 1

    def test_ancestor_provides_defaults(self):
        chain = [
            {"name": "local"},
            {"name": "parent", "privacy": "team", "extra_field": "inherited"},
        ]
        merged = merge_kin_chain(chain)
        assert merged["name"] == "local"
        assert merged["privacy"] == "team"        # inherited
        assert merged["extra_field"] == "inherited"  # inherited

    def test_chain_tracking(self, kin_tree):
        chain = resolve_kin_chain(kin_tree["project"] / ".kin")
        merged = merge_kin_chain(chain)
        assert "_chain" in merged
        assert len(merged["_chain"]) == 3

    def test_empty_chain(self):
        merged = merge_kin_chain([])
        assert merged == {}


class TestLoadProjectContext:
    def test_full_resolution(self, kin_tree):
        ctx = load_project_context(kin_tree["project"] / ".kin")
        assert ctx["name"] == "wander-integrations"
        # Domains merged from all three levels
        assert "integrations" in ctx["domains"]
        assert "python" in ctx["domains"]
        assert "platform" in ctx["domains"]
        assert "engineering" in ctx["domains"]

    def test_private_stays_private(self, kin_tree):
        ctx = load_project_context(kin_tree["personal"] / ".kin")
        assert ctx["audience"] == "private"
        assert "personal" in ctx["domains"]

    def test_nonexistent_file(self, tmp_path):
        ctx = load_project_context(tmp_path / "nonexistent" / ".kin")
        assert ctx == {}


class TestRelativeInherits:
    def test_relative_path(self, tmp_path):
        parent = tmp_path / "parent"
        child = tmp_path / "parent" / "child"
        parent.mkdir()
        child.mkdir()
        (parent / ".kin").write_text("name: parent\ndomains: [base]\n")
        (child / ".kin").write_text("name: child\ndomains: [specific]\ninherits:\n  - ../.kin\n")

        chain = resolve_kin_chain(child / ".kin")
        assert len(chain) == 2
        merged = merge_kin_chain(chain)
        assert "base" in merged["domains"]
        assert "specific" in merged["domains"]
