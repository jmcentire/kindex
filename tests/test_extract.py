"""Tests for extraction pipeline (keyword fallback — no LLM in tests)."""

from kindex.extract import keyword_extract


class TestKeywordExtract:
    def test_finds_capitalized_phrases(self):
        text = "The Ambient Structure Discovery system uses Stigmergic Mesh architecture."
        result = keyword_extract(text)
        titles = [c["title"] for c in result["concepts"]]
        assert any("Ambient Structure" in t for t in titles)

    def test_finds_quoted_terms(self):
        text = 'The concept of "activation fingerprint" was discussed.'
        result = keyword_extract(text)
        titles = [c["title"] for c in result["concepts"]]
        assert any("activation fingerprint" in t for t in titles)

    def test_detects_connections(self):
        text = "This is similar to stigmergic coordination patterns."
        result = keyword_extract(text)
        assert len(result["connections"]) >= 1

    def test_empty_input(self):
        result = keyword_extract("")
        assert result["concepts"] == []

    def test_returns_all_keys(self):
        result = keyword_extract("Some text about things.")
        assert "concepts" in result
        assert "decisions" in result
        assert "questions" in result
        assert "connections" in result
        assert "bridge_opportunities" in result
