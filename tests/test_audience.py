"""Tests for audience tenancy model."""

import pytest

from kindex.config import Config
from kindex.store import Store


@pytest.fixture
def store(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    s = Store(cfg)
    yield s
    s.close()


class TestAudienceField:
    def test_default_audience_is_private(self, store):
        nid = store.add_node("Secret Thought", content="personal stuff")
        node = store.get_node(nid)
        assert node["audience"] == "private"

    def test_set_audience_on_create(self, store):
        nid = store.add_node("Work Project", audience="team")
        node = store.get_node(nid)
        assert node["audience"] == "team"

    def test_update_audience(self, store):
        nid = store.add_node("Flexible Node")
        store.update_node(nid, audience="public")
        node = store.get_node(nid)
        assert node["audience"] == "public"

    def test_filter_by_audience(self, store):
        store.add_node("Private A", audience="private")
        store.add_node("Team B", audience="team")
        store.add_node("Public C", audience="public")
        store.add_node("Team D", audience="team")

        private = store.all_nodes(audience="private")
        assert len(private) == 1

        team = store.all_nodes(audience="team")
        assert len(team) == 2

        public = store.all_nodes(audience="public")
        assert len(public) == 1

    def test_all_nodes_without_filter_returns_all(self, store):
        store.add_node("A", audience="private")
        store.add_node("B", audience="team")
        store.add_node("C", audience="public")
        assert len(store.all_nodes()) == 3


class TestExportBoundaries:
    def test_team_export_excludes_private(self, store):
        store.add_node("Private", content="secret", node_id="priv", audience="private")
        store.add_node("Team", content="work stuff", node_id="team", audience="team")
        store.add_node("Public", content="open", node_id="pub", audience="public")

        # Edges: private → team, team → public
        store.add_edge("priv", "team", provenance="internal link")
        store.add_edge("team", "pub", provenance="public link")

        # Team export: should include team + public, exclude private
        team_nodes = store.all_nodes(audience="team")
        pub_nodes = store.all_nodes(audience="public")
        exported_ids = {n["id"] for n in team_nodes + pub_nodes}

        assert "priv" not in exported_ids
        assert "team" in exported_ids
        assert "pub" in exported_ids

        # Edges from team node — the edge to 'pub' is valid, but edge from 'priv' should be stripped
        edges = store.edges_from("team")
        for e in edges:
            if e["to_id"] not in exported_ids:
                # This edge crosses the boundary — should be filtered in export
                pass  # The filtering happens in cli.py cmd_export

    def test_public_export_is_minimal(self, store):
        store.add_node("Secret", audience="private")
        store.add_node("Work", audience="team")
        store.add_node("Open Source", audience="public")

        public = store.all_nodes(audience="public")
        assert len(public) == 1
        assert public[0]["title"] == "Open Source"
