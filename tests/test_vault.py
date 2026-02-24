"""Tests for vault loading, parsing, and writing."""

from kindex.models import Edge, SkillNode, TopicNode
from kindex.vault import Vault, parse_frontmatter


class TestParseFrontmatter:
    def test_normal(self, tmp_path):
        f = tmp_path / "t.md"
        f.write_text("---\ntopic: foo\n---\nBody.")
        meta, body = parse_frontmatter(f)
        assert meta["topic"] == "foo"
        assert body == "Body."

    def test_no_frontmatter(self, tmp_path):
        f = tmp_path / "t.md"
        f.write_text("# Title\nBody.")
        meta, body = parse_frontmatter(f)
        assert meta == {}

    def test_crlf(self, tmp_path):
        f = tmp_path / "t.md"
        f.write_bytes(b"---\r\ntopic: bar\r\n---\r\nBody\r\n")
        meta, body = parse_frontmatter(f)
        assert meta["topic"] == "bar"
        assert "\r" not in body


class TestVaultLoad:
    def test_loads_topics(self, sample_vault):
        assert len(sample_vault.topics) == 4
        assert "alpha" in sample_vault.topics

    def test_loads_skills(self, sample_vault):
        assert "python" in sample_vault.skills
        assert sample_vault.skills["python"].level == "expert"

    def test_all_slugs(self, sample_vault):
        slugs = sample_vault.all_slugs()
        assert "alpha" in slugs
        assert "python" in slugs

    def test_forward_index(self, sample_vault):
        edges = sample_vault.edges_from("alpha")
        targets = {e.target for e in edges}
        assert "beta" in targets

    def test_reverse_index(self, sample_vault):
        # python skill connects to alpha
        incoming = sample_vault.edges_to("alpha")
        sources = {s for s, _ in incoming}
        assert "python" in sources

    def test_no_frontmatter_topic(self, sample_vault):
        delta = sample_vault.get("delta")
        assert not delta.has_frontmatter

    def test_extra_fields(self, sample_vault):
        beta = sample_vault.get("beta")
        assert beta.custom_field == "preserved value"


class TestVaultWrite:
    def test_save_topic(self, tmp_vault):
        from kindex.config import Config
        node = TopicNode(topic="test", slug="test", title="Test", weight=0.5,
                         body="# Test\n\nContent.")
        tmp_vault.save_topic(node)
        tmp_vault.topics["test"] = node

        reloaded = Vault(tmp_vault.config).load()
        assert reloaded.get("test").title == "Test"

    def test_save_skill(self, tmp_vault):
        node = SkillNode(skill="go", slug="go", title="Go Development",
                         level="proficient", body="# Go\n\nContent.")
        tmp_vault.save_skill(node)
        tmp_vault.skills["go"] = node

        reloaded = Vault(tmp_vault.config).load()
        assert "go" in reloaded.skills
        assert reloaded.skills["go"].level == "proficient"

    def test_add_edge(self, tmp_vault):
        a = TopicNode(topic="a", slug="a", title="A", body="# A\n")
        b = TopicNode(topic="b", slug="b", title="B", body="# B\n")
        tmp_vault.save_topic(a)
        tmp_vault.save_topic(b)
        tmp_vault.topics["a"] = a
        tmp_vault.topics["b"] = b

        tmp_vault.add_edge("a", "b", 0.8, "test")

        reloaded = Vault(tmp_vault.config).load()
        edges = reloaded.edges_from("a")
        assert any(e.target == "b" for e in edges)
