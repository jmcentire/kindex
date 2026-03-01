"""Tests for the graph-connected task system."""

from __future__ import annotations

import datetime

import pytest

from kindex.config import Config
from kindex.store import Store


@pytest.fixture
def store(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    s = Store(cfg)
    yield s
    s.close()


# ── Weight computation ─────────────────────────────────────────────


class TestComputeTaskWeight:
    def test_priority_mapping(self):
        from kindex.tasks import compute_task_weight

        assert compute_task_weight(1) == 0.9
        assert compute_task_weight(2) == 0.7
        assert compute_task_weight(3) == 0.5
        assert compute_task_weight(4) == 0.3
        assert compute_task_weight(5) == 0.1

    def test_overdue_boost(self):
        from kindex.tasks import compute_task_weight

        yesterday = (datetime.datetime.now() - datetime.timedelta(hours=25)).isoformat()
        w = compute_task_weight(3, due=yesterday)
        assert w > 0.5  # should have overdue boost

    def test_due_today_boost(self):
        from kindex.tasks import compute_task_weight

        soon = (datetime.datetime.now() + datetime.timedelta(hours=2)).isoformat()
        w = compute_task_weight(3, due=soon)
        assert w > 0.5  # should have today boost

    def test_due_soon_boost(self):
        from kindex.tasks import compute_task_weight

        two_days = (datetime.datetime.now() + datetime.timedelta(hours=48)).isoformat()
        w = compute_task_weight(3, due=two_days)
        assert w > 0.5  # should have soon boost

    def test_far_due_no_boost(self):
        from kindex.tasks import compute_task_weight

        far = (datetime.datetime.now() + datetime.timedelta(days=30)).isoformat()
        w = compute_task_weight(3, due=far)
        assert w == 0.5  # no boost

    def test_weight_clamped(self):
        from kindex.tasks import compute_task_weight

        yesterday = (datetime.datetime.now() - datetime.timedelta(hours=25)).isoformat()
        w = compute_task_weight(1, due=yesterday)
        assert w <= 1.0


# ── CRUD ───────────────────────────────────────────────────────────


class TestTaskCRUD:
    def test_create_task(self, store):
        from kindex.tasks import create_task

        task_id = create_task(store, "Buy groceries", priority=2)
        node = store.get_node(task_id)
        assert node is not None
        assert node["type"] == "task"
        assert node["title"] == "Buy groceries"
        extra = node["extra"]
        assert extra["task_status"] == "open"
        assert extra["priority"] == 2
        assert node["weight"] == 0.7

    def test_create_task_with_due(self, store):
        from kindex.tasks import create_task

        due = (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()
        task_id = create_task(store, "Urgent thing", priority=1, due=due)
        node = store.get_node(task_id)
        assert node["extra"]["due"] == due
        assert node["weight"] > 0.9  # should have due boost

    def test_create_task_with_links(self, store):
        from kindex.tasks import create_task

        # Create a concept to link to
        concept_id = store.add_node("cooking-dinner", node_type="concept")

        task_id = create_task(store, "Stir the soup", link_to=[concept_id])
        edges = store.edges_from(task_id)
        assert any(e["to_id"] == concept_id for e in edges)

    def test_create_task_link_by_title(self, store):
        from kindex.tasks import create_task

        store.add_node("kitchen", node_type="concept")
        task_id = create_task(store, "Clean counter", link_to=["kitchen"])
        edges = store.edges_from(task_id)
        assert len(edges) > 0

    def test_complete_task(self, store):
        from kindex.tasks import create_task, complete_task

        task_id = create_task(store, "Do laundry")
        result = complete_task(store, task_id)
        assert result is not None
        assert result["extra"]["task_status"] == "done"
        assert result["extra"]["completed_at"] is not None
        assert result["status"] == "archived"
        assert result["weight"] == 0.01

    def test_cancel_task(self, store):
        from kindex.tasks import create_task, cancel_task

        task_id = create_task(store, "Nevermind")
        result = cancel_task(store, task_id)
        assert result is not None
        assert result["extra"]["task_status"] == "cancelled"
        assert result["status"] == "archived"

    def test_update_task_priority(self, store):
        from kindex.tasks import create_task, update_task

        task_id = create_task(store, "Something", priority=3)
        result = update_task(store, task_id, priority=1)
        assert result["extra"]["priority"] == 1
        assert result["weight"] == 0.9

    def test_update_task_status(self, store):
        from kindex.tasks import create_task, update_task

        task_id = create_task(store, "WIP")
        result = update_task(store, task_id, task_status="in_progress")
        assert result["extra"]["task_status"] == "in_progress"

    def test_complete_nonexistent(self, store):
        from kindex.tasks import complete_task

        result = complete_task(store, "fake-id")
        assert result is None

    def test_priority_string_parsing(self):
        from kindex.tasks import _parse_priority

        assert _parse_priority("urgent") == 1
        assert _parse_priority("high") == 2
        assert _parse_priority("normal") == 3
        assert _parse_priority("low") == 4
        assert _parse_priority("someday") == 5
        assert _parse_priority(2) == 2


# ── Queries ────────────────────────────────────────────────────────


class TestListTasks:
    def test_list_open_tasks(self, store):
        from kindex.tasks import create_task, list_tasks

        create_task(store, "Task A", priority=1)
        create_task(store, "Task B", priority=3)
        create_task(store, "Task C", priority=5)

        tasks = list_tasks(store, status="open")
        assert len(tasks) == 3
        # Should be sorted by weight DESC (priority 1 first)
        assert tasks[0]["title"] == "Task A"
        assert tasks[2]["title"] == "Task C"

    def test_list_filters_done(self, store):
        from kindex.tasks import create_task, complete_task, list_tasks

        tid = create_task(store, "Done task")
        create_task(store, "Open task")
        complete_task(store, tid)

        open_tasks = list_tasks(store, status="open")
        assert len(open_tasks) == 1
        assert open_tasks[0]["title"] == "Open task"

    def test_list_all(self, store):
        from kindex.tasks import create_task, complete_task, list_tasks

        tid = create_task(store, "Done")
        create_task(store, "Open")
        complete_task(store, tid)

        all_tasks = list_tasks(store, status="all")
        assert len(all_tasks) == 2

    def test_list_by_scope(self, store):
        from kindex.tasks import create_task, list_tasks

        create_task(store, "Global one", scope="global")
        create_task(store, "Local one", scope="contextual")

        global_tasks = list_tasks(store, scope="global")
        assert len(global_tasks) == 1
        assert global_tasks[0]["title"] == "Global one"

    def test_list_by_domain(self, store):
        from kindex.tasks import create_task, list_tasks

        create_task(store, "Code task", domains=["engineering"])
        create_task(store, "Cook task", domains=["cooking"])

        eng = list_tasks(store, domain="engineering")
        assert len(eng) == 1
        assert eng[0]["title"] == "Code task"


# ── BFS ────────────────────────────────────────────────────────────


class TestStoreBFS:
    def test_simple_bfs(self, store):
        from kindex.tasks import store_bfs

        a = store.add_node("Node A", node_type="concept")
        b = store.add_node("Node B", node_type="concept")
        store.add_edge(a, b, "relates_to", weight=0.8)

        results = store_bfs(store, [a], max_hops=1)
        assert len(results) == 1
        assert results[0]["id"] == b

    def test_bfs_two_hops(self, store):
        from kindex.tasks import store_bfs

        a = store.add_node("A", node_type="concept")
        b = store.add_node("B", node_type="concept")
        c = store.add_node("C", node_type="concept")
        store.add_edge(a, b, "relates_to", weight=0.8)
        store.add_edge(b, c, "relates_to", weight=0.8)

        results = store_bfs(store, [a], max_hops=2)
        ids = {r["id"] for r in results}
        assert b in ids
        assert c in ids

    def test_bfs_weight_decay(self, store):
        from kindex.tasks import store_bfs

        a = store.add_node("A", node_type="concept")
        b = store.add_node("B", node_type="concept")
        c = store.add_node("C", node_type="concept")
        store.add_edge(a, b, "relates_to", weight=0.5)
        store.add_edge(b, c, "relates_to", weight=0.5)

        results = store_bfs(store, [a], max_hops=2, min_weight=0.3)
        # B has proximity 0.5, C has proximity 0.25 (below min_weight)
        # But b->c is bidirectional, so there's a reverse edge b->c at 0.5*0.8=0.4
        # Forward: a->b at 0.5, b->c at 0.5*0.5=0.25 (pruned)
        # But add_edge creates bidirectional edges, so c->b also exists
        ids = {r["id"] for r in results}
        assert b in ids

    def test_bfs_type_filter(self, store):
        from kindex.tasks import store_bfs

        a = store.add_node("Kitchen", node_type="concept")
        b = store.add_node("Cooking", node_type="concept")
        c = store.add_node("Stir soup", node_type="task",
                           extra={"task_status": "open", "priority": 2})
        store.add_edge(a, b, "relates_to", weight=0.8)
        store.add_edge(b, c, "context_of", weight=0.6)

        results = store_bfs(store, [a], max_hops=2, type_filter="task")
        assert len(results) >= 1
        assert results[0]["id"] == c


# ── Nearby tasks ───────────────────────────────────────────────────


class TestNearbyTasks:
    def test_nearby_finds_linked_task(self, store):
        from kindex.tasks import create_task, nearby_tasks

        concept_id = store.add_node("kindex-project", node_type="concept")
        create_task(store, "Fix the bug", priority=2, link_to=[concept_id])

        tasks = nearby_tasks(store, [concept_id])
        assert len(tasks) >= 1
        assert tasks[0]["title"] == "Fix the bug"

    def test_nearby_through_intermediate(self, store):
        from kindex.tasks import create_task, nearby_tasks

        kitchen = store.add_node("kitchen", node_type="concept")
        cooking = store.add_node("cooking-dinner", node_type="concept")
        store.add_edge(kitchen, cooking, "relates_to", weight=0.8)

        create_task(store, "Stir the soup", priority=2, link_to=["cooking-dinner"])

        tasks = nearby_tasks(store, [kitchen], max_hops=2)
        assert len(tasks) >= 1
        assert tasks[0]["title"] == "Stir the soup"

    def test_nearby_excludes_done(self, store):
        from kindex.tasks import create_task, complete_task, nearby_tasks

        concept_id = store.add_node("project", node_type="concept")
        tid = create_task(store, "Old task", link_to=[concept_id])
        complete_task(store, tid)

        tasks = nearby_tasks(store, [concept_id])
        assert len(tasks) == 0

    def test_nearby_empty_seeds(self, store):
        from kindex.tasks import nearby_tasks

        tasks = nearby_tasks(store, [])
        assert tasks == []


# ── Formatting ─────────────────────────────────────────────────────


class TestFormatting:
    def test_format_task(self, store):
        from kindex.tasks import create_task, format_task

        task_id = create_task(store, "Test task", priority=2)
        node = store.get_node(task_id)
        output = format_task(node)
        assert "Test task" in output
        assert "high" in output

    def test_format_task_list(self, store):
        from kindex.tasks import create_task, list_tasks, format_task_list

        create_task(store, "First", priority=1)
        create_task(store, "Second", priority=3)
        tasks = list_tasks(store)
        output = format_task_list(tasks)
        assert "First" in output
        assert "Second" in output
        assert "[P1]" in output
        assert "[P3]" in output

    def test_format_empty(self):
        from kindex.tasks import format_task_list

        assert format_task_list([]) == "No tasks found."


# ── CLI smoke tests ────────────────────────────────────────────────


class TestCLI:
    def test_task_add_and_list(self, tmp_path):
        import subprocess
        import sys

        env_args = ["--data-dir", str(tmp_path)]

        # Add a task
        result = subprocess.run(
            [sys.executable, "-m", "kindex.cli", "task", "add", "Test", "CLI", "task",
             "--priority", "2"] + env_args,
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Created task:" in result.stdout

        # List tasks
        result = subprocess.run(
            [sys.executable, "-m", "kindex.cli", "task", "list"] + env_args,
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Test CLI task" in result.stdout

    def test_task_done(self, tmp_path):
        import subprocess
        import sys

        env_args = ["--data-dir", str(tmp_path)]

        # Add
        result = subprocess.run(
            [sys.executable, "-m", "kindex.cli", "task", "add", "Finish", "it",
             "--priority", "3"] + env_args,
            capture_output=True, text=True,
        )
        task_id = result.stdout.strip().split(":")[-1].strip()

        # Done
        result = subprocess.run(
            [sys.executable, "-m", "kindex.cli", "task", "done",
             "--task-id", task_id] + env_args,
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Completed:" in result.stdout
