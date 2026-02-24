"""Tests for Pydantic models."""

from kindex.models import Edge, InboxItem, SkillNode, TopicNode


class TestEdge:
    def test_defaults(self):
        e = Edge(target="foo")
        assert e.target == "foo"
        assert e.weight == 0.5

    def test_full(self):
        e = Edge(target="bar", weight=0.9, reason="strong link")
        assert e.reason == "strong link"


class TestTopicNode:
    def test_extra_fields_preserved(self):
        node = TopicNode(topic="p", slug="p", date_filed="2026-02-12")
        assert node.date_filed == "2026-02-12"
        d = node.frontmatter_dict()
        assert d["date_filed"] == "2026-02-12"

    def test_edge_to(self):
        node = TopicNode(topic="a", slug="a",
                         connects_to=[Edge(target="b", weight=0.9)])
        assert node.edge_to("b").weight == 0.9
        assert node.edge_to("z") is None

    def test_frontmatter_excludes_internals(self):
        node = TopicNode(topic="a", slug="a", body="text", path="/tmp/a.md")
        d = node.frontmatter_dict()
        assert "body" not in d
        assert "path" not in d
        assert "slug" not in d


class TestSkillNode:
    def test_basic(self):
        s = SkillNode(skill="python", slug="python", title="Python", level="expert")
        assert s.level == "expert"
        d = s.frontmatter_dict()
        assert d["level"] == "expert"


class TestInboxItem:
    def test_create(self):
        item = InboxItem(content="found X", source="session-1")
        assert not item.processed
