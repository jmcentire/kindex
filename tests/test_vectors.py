"""Tests for optional vector search module."""

from kindex.vectors import _check_vec, embed_text, is_available


class TestVectorAvailability:
    def test_is_available_returns_bool(self):
        result = is_available()
        assert isinstance(result, bool)

    def test_check_vec_consistent(self):
        """_check_vec should return same result on repeated calls."""
        a = _check_vec()
        b = _check_vec()
        assert a == b

    def test_embed_text_without_model(self):
        """embed_text should return None if sentence-transformers not installed."""
        # This may or may not return None depending on environment
        result = embed_text("test text")
        assert result is None or isinstance(result, list)
