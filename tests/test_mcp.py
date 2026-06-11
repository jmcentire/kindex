"""Tests for Kindex MCP server tool functions."""

import json
import os

import pytest

mcp = pytest.importorskip("mcp", reason="mcp not installed")


@pytest.fixture
def mcp_store(tmp_path):
    """Set up a Store + Config for MCP tool testing."""
    from kindex.config import load_config
    from kindex.store import Store

    d = str(tmp_path)
    cfg = load_config()
    cfg.data_dir = d
    store = Store(cfg)

    # Add test data
    id1 = store.add_node(title="Stigmergy", content="Coordination through environmental traces",
                         node_type="concept", domains=["biology", "ai"],
                         prov_activity="test")
    id2 = store.add_node(title="Python", content="Expert-level programming skill",
                         node_type="skill", domains=["engineering"],
                         prov_activity="test")
    store.add_node(title="Never break the API contract",
                   content="All public endpoints must maintain backward compatibility",
                   node_type="constraint", prov_activity="test",
                   extra={"trigger": "pre-deploy", "action": "block"})
    store.add_edge(id1, id2, edge_type="relates_to", weight=0.6)

    yield store, cfg
    store.close()


@pytest.fixture
def patch_store(mcp_store, monkeypatch):
    """Patch the MCP server's _get_store to use test store."""
    store, cfg = mcp_store
    import kindex.mcp_server as mcp_mod
    monkeypatch.setattr(mcp_mod, "_store", store)
    monkeypatch.setattr(mcp_mod, "_config", cfg)
    return store, cfg


class TestMCPSearch:
    def test_search_finds_results(self, patch_store):
        from kindex.mcp_server import search
        result = search("stigmergy")
        assert "Stigmergy" in result
        assert "stigmergy" in result.lower()

    def test_search_no_results(self, patch_store):
        from kindex.mcp_server import search
        result = search("zzzznonexistent")
        assert "No results" in result or "0 results" in result or "Found" in result


class TestMCPAdd:
    def test_add_creates_node(self, patch_store):
        from kindex.mcp_server import add
        result = add("Graph theory is about vertices and edges", node_type="concept")
        assert "Created node" in result

    def test_add_with_type(self, patch_store):
        from kindex.mcp_server import add
        result = add("Should we use Redis?", node_type="question")
        assert "Created node" in result
        assert "question" in result


class TestMCPContext:
    def test_context_with_topic(self, patch_store):
        from kindex.mcp_server import context
        result = context(topic="stigmergy", level="abridged")
        assert "Kindex" in result or "stigmergy" in result.lower()

    def test_context_empty_topic(self, patch_store):
        from kindex.mcp_server import context
        result = context()
        # Should return something (recent nodes fallback)
        assert isinstance(result, str)


class TestMCPShow:
    def test_show_by_title(self, patch_store):
        from kindex.mcp_server import show
        result = show("Stigmergy")
        assert "Stigmergy" in result
        assert "concept" in result

    def test_show_not_found(self, patch_store):
        from kindex.mcp_server import show
        result = show("nonexistent-node-id")
        assert "not found" in result.lower()


class TestMCPLink:
    def test_link_nodes(self, patch_store):
        from kindex.mcp_server import add, link
        add("Machine Learning fundamentals", node_type="concept")
        result = link("Python", "Machine Learning fundamentals",
                       relationship="implements", weight=0.8)
        assert "Linked" in result

    def test_link_not_found(self, patch_store):
        from kindex.mcp_server import link
        result = link("nonexistent", "also-nonexistent")
        assert "not found" in result.lower()


class TestMCPListNodes:
    def test_list_all(self, patch_store):
        from kindex.mcp_server import list_nodes
        result = list_nodes()
        assert "node(s)" in result
        assert "Stigmergy" in result

    def test_list_by_type(self, patch_store):
        from kindex.mcp_server import list_nodes
        result = list_nodes(node_type="skill")
        assert "Python" in result

    def test_list_empty(self, patch_store):
        from kindex.mcp_server import list_nodes
        result = list_nodes(node_type="nonexistent-type")
        assert "No nodes" in result


class TestMCPStatus:
    def test_status_returns_stats(self, patch_store):
        from kindex.mcp_server import status
        result = status()
        assert "Nodes:" in result
        assert "Edges:" in result


class TestMCPAsk:
    def test_ask_procedural(self, patch_store):
        from kindex.mcp_server import ask
        result = ask("How do I use stigmergy?")
        assert "procedural" in result.lower()

    def test_ask_factual(self, patch_store):
        from kindex.mcp_server import ask
        result = ask("What is Python?")
        assert "factual" in result.lower()

    def test_ask_decision(self, patch_store):
        from kindex.mcp_server import ask
        result = ask("Should I use stigmergy vs direct communication?")
        assert "decision" in result.lower()


class TestMCPSuggest:
    def test_suggest_empty(self, patch_store):
        from kindex.mcp_server import suggest
        result = suggest()
        assert "No pending" in result or "suggestion" in result.lower()


class TestMCPLearn:
    def test_learn_extracts(self, patch_store):
        from kindex.mcp_server import learn
        result = learn("We decided to use Redis for caching because of its speed. "
                       "This connects to our Distributed Systems architecture.")
        assert "Extracted" in result


class TestMCPGraphStats:
    def test_graph_stats(self, patch_store):
        from kindex.mcp_server import graph_stats
        result = graph_stats()
        assert "Nodes:" in result
        assert "Density:" in result


class TestMCPChangelog:
    def test_changelog(self, patch_store):
        from kindex.mcp_server import changelog
        result = changelog(days=30)
        assert isinstance(result, str)


class TestMCPResources:
    def test_resource_status(self, patch_store):
        from kindex.mcp_server import resource_status
        result = resource_status()
        data = json.loads(result)
        assert "nodes" in data

    def test_resource_node(self, patch_store):
        from kindex.mcp_server import resource_node
        result = resource_node("Stigmergy")
        assert "Stigmergy" in result

    def test_resource_recent(self, patch_store):
        from kindex.mcp_server import resource_recent
        result = resource_recent()
        assert isinstance(result, str)

    def test_resource_orphans(self, patch_store):
        from kindex.mcp_server import resource_orphans
        result = resource_orphans()
        assert isinstance(result, str)


class TestMCPTags:
    def test_add_with_tags(self, patch_store):
        from kindex.mcp_server import add
        result = add("Tagged concept for testing", tags="python,ml")
        assert "Created node" in result
        store, _ = patch_store
        node = store.get_node_by_title("Tagged concept for testing")
        assert "python" in node["domains"]
        assert "ml" in node["domains"]

    def test_add_tags_supplement_domains(self, patch_store):
        from kindex.mcp_server import add
        result = add("Dual tagged item", tags="user-tag", domains="auto-tag")
        assert "Created node" in result
        store, _ = patch_store
        node = store.get_node_by_title("Dual tagged item")
        assert "user-tag" in node["domains"]
        assert "auto-tag" in node["domains"]

    def test_search_with_tags_filter(self, patch_store):
        from kindex.mcp_server import search
        result = search("coordination", tags="biology")
        assert "Stigmergy" in result

    def test_search_with_tags_excludes_non_matching(self, patch_store):
        from kindex.mcp_server import search
        result = search("coordination", tags="nonexistent")
        assert "No results" in result

    def test_list_nodes_with_tags(self, patch_store):
        from kindex.mcp_server import list_nodes
        result = list_nodes(tags="biology")
        assert "Stigmergy" in result

    def test_list_nodes_default_limit(self, patch_store):
        import inspect
        from kindex.mcp_server import list_nodes
        sig = inspect.signature(list_nodes)
        assert sig.parameters["limit"].default == 100


@pytest.fixture
def agent_env(monkeypatch):
    """Deterministic agent identity for collab tools."""
    monkeypatch.setenv("KIN_AGENT_ID", "mcp-agent")
    return "mcp-agent"


class TestMCPCoordLegacy:
    """First coverage of the pre-existing coord_* tools."""

    def test_start_post_read_list_end(self, patch_store, agent_env):
        from kindex.mcp_server import (
            coord_end,
            coord_list,
            coord_post,
            coord_read,
            coord_start,
        )

        result = coord_start("Build Plan", agent="agent-a")
        assert "Started coordination conversation" in result

        result = coord_post("build-plan", agent="agent-a", message="claimed parser")
        assert "Posted coordination message #1" in result

        result = coord_read("build-plan")
        assert "claimed parser" in result
        assert "agent-a" in result

        result = coord_list()
        assert "build-plan" in result

        result = coord_end("build-plan", summary="done")
        assert "Ended coordination conversation" in result
        assert "build-plan" not in coord_list()

    def test_post_to_missing_conversation_errors(self, patch_store, agent_env):
        from kindex.mcp_server import coord_post
        result = coord_post("ghost", message="anyone there?")
        assert "Could not post" in result

    def test_read_missing_conversation_errors(self, patch_store, agent_env):
        from kindex.mcp_server import coord_read
        result = coord_read("ghost")
        assert "Could not read" in result


class TestMCPCoordCollab:
    def test_start_defaults_creator_to_resolved_agent(self, patch_store, agent_env):
        from kindex.coordination import get_conversation
        from kindex.mcp_server import coord_start

        store, _ = patch_store
        coord_start("Crew")
        extra = get_conversation(store, "crew")["extra"]
        assert extra["created_by"] == "mcp-agent"
        assert [m["agent"] for m in extra["members"]] == ["mcp-agent"]

    def test_join_idempotent_and_errors(self, patch_store, agent_env):
        from kindex.coordination import get_conversation
        from kindex.mcp_server import coord_join, coord_start

        store, _ = patch_store
        coord_start("Crew", agent="agent-a")
        assert "Joined crew as mcp-agent" in coord_join("crew")
        assert "Joined crew as mcp-agent" in coord_join("crew")
        members = get_conversation(store, "crew")["extra"]["members"]
        assert [m["agent"] for m in members] == ["agent-a", "mcp-agent"]

        assert "Could not join" in coord_join("ghost")

    def test_read_advances_default_agent_cursor(self, patch_store, agent_env):
        from kindex.coordination import active_collabs_for_agent
        from kindex.mcp_server import coord_join, coord_post, coord_read, coord_start

        store, _ = patch_store
        coord_start("Crew", agent="agent-a")
        coord_join("crew")  # mcp-agent joins
        coord_post("crew", agent="agent-a", message="news")
        assert active_collabs_for_agent(store, "mcp-agent")[0]["unread_count"] == 1

        coord_read("crew")
        assert active_collabs_for_agent(store, "mcp-agent")[0]["unread_count"] == 0

    def test_post_targeted_message(self, patch_store, agent_env):
        from kindex.coordination import read_messages
        from kindex.mcp_server import coord_post, coord_start

        store, _ = patch_store
        coord_start("Crew")
        coord_post("crew", message="for b", to="agent-b")
        msg = read_messages(store, "crew")["messages"][0]
        assert msg["author"] == "mcp-agent"
        assert msg["to"] == "agent-b"

    def test_attach_by_id_title_and_missing(self, patch_store, agent_env):
        from kindex.coordination import get_conversation
        from kindex.mcp_server import coord_attach, coord_start

        store, _ = patch_store
        coord_start("Crew")
        result = coord_attach("crew", "Stigmergy")  # by title
        assert "Attached" in result
        resources = get_conversation(store, "crew")["extra"]["resources"]
        assert len(resources) == 1

        # by id, idempotent
        coord_attach("crew", resources[0])
        assert get_conversation(store, "crew")["extra"]["resources"] == resources

        assert "Could not attach" in coord_attach("crew", "no-such-node-xyz")

    def test_inject_set_list_clear(self, patch_store, agent_env):
        from kindex.mcp_server import coord_inject, coord_start

        coord_start("Crew")
        result = coord_inject("crew", action="set", text="branch frozen")
        assert "Set inject message #1" in result
        result = coord_inject("crew", action="set", text="b: rebase", to="agent-b")
        assert "#2 " in result or "#2" in result

        listing = coord_inject("crew", action="list")
        assert "branch frozen" in listing
        assert "-> agent-b" in listing

        assert "Cleared 1" in coord_inject("crew", action="clear", message_id=1)
        assert "Cleared 1" in coord_inject("crew", action="clear")
        assert "No inject messages" in coord_inject("crew", action="list")

    def test_inject_unknown_action(self, patch_store, agent_env):
        from kindex.mcp_server import coord_inject, coord_start
        coord_start("Crew")
        assert "Unknown inject action" in coord_inject("crew", action="bogus")


class TestMCPLocks:
    def test_acquire_conflict_force_release(self, patch_store, agent_env, monkeypatch):
        from kindex.mcp_server import lock_acquire, lock_release

        result = lock_acquire("Stigmergy", ttl_minutes=30, note="editing")
        assert "Locked Stigmergy" in result
        assert "mcp-agent" in result

        # Another agent cannot take or release it without force
        monkeypatch.setenv("KIN_AGENT_ID", "other-agent")
        assert "Could not lock node" in lock_acquire("Stigmergy")
        assert "Could not unlock node" in lock_release("Stigmergy")
        assert "Locked Stigmergy" in lock_acquire("Stigmergy", force=True)

        # New holder releases cleanly
        assert "Unlocked Stigmergy" in lock_release("Stigmergy")
        assert "No lock on" in lock_release("Stigmergy")

    def test_lock_missing_node(self, patch_store, agent_env):
        from kindex.mcp_server import lock_acquire, lock_release
        assert "Node not found" in lock_acquire("no-such-node-xyz")
        assert "Node not found" in lock_release("no-such-node-xyz")


class TestMCPTaskClaimDefaults:
    def test_task_claim_defaults_agent(self, patch_store, agent_env):
        from kindex.mcp_server import task_add, task_claim

        store, _ = patch_store
        task_add("Ship the parser")
        task_id = store.all_nodes(node_type="task", limit=10)[0]["id"]

        result = task_claim(task_id)
        assert "mcp-agent" in result
        claim = (store.get_node(task_id).get("extra") or {}).get("claim")
        assert claim["agent"] == "mcp-agent"
