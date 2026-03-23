"""Tests for optional vector search module."""

from unittest.mock import patch, MagicMock
from kindex.vectors import (
    _check_vec, _resolve_embedding_config, embed_text, is_available,
    PROVIDER_DEFAULTS,
)
from kindex.config import Config, EmbeddingConfig


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
        result = embed_text("test text")
        assert result is None or isinstance(result, list)


class TestProviderConfigResolution:
    def test_none_config_defaults_to_local(self):
        provider, model, dims, key_env = _resolve_embedding_config(None)
        assert provider == "local"
        assert model == "all-MiniLM-L6-v2"
        assert dims == 384
        assert key_env == ""

    def test_default_config_is_local(self):
        config = Config()
        provider, model, dims, key_env = _resolve_embedding_config(config)
        assert provider == "local"
        assert model == "all-MiniLM-L6-v2"
        assert dims == 384

    def test_openai_provider_defaults(self):
        config = Config(embedding=EmbeddingConfig(provider="openai"))
        provider, model, dims, key_env = _resolve_embedding_config(config)
        assert provider == "openai"
        assert model == "text-embedding-3-small"
        assert dims == 1536
        assert key_env == "OPENAI_API_KEY"

    def test_gemini_provider_defaults(self):
        config = Config(embedding=EmbeddingConfig(provider="gemini"))
        provider, model, dims, key_env = _resolve_embedding_config(config)
        assert provider == "gemini"
        assert model == "gemini-embedding-001"
        assert dims == 3072
        assert key_env == "GEMINI_API_KEY"

    def test_custom_model_override(self):
        config = Config(embedding=EmbeddingConfig(
            provider="openai", model="text-embedding-3-large"
        ))
        _, model, _, _ = _resolve_embedding_config(config)
        assert model == "text-embedding-3-large"

    def test_custom_dimensions_override(self):
        config = Config(embedding=EmbeddingConfig(
            provider="openai", dimensions=512
        ))
        _, _, dims, _ = _resolve_embedding_config(config)
        assert dims == 512

    def test_custom_api_key_env_override(self):
        config = Config(embedding=EmbeddingConfig(
            provider="openai", api_key_env="MY_CUSTOM_KEY"
        ))
        _, _, _, key_env = _resolve_embedding_config(config)
        assert key_env == "MY_CUSTOM_KEY"

    def test_unknown_provider_falls_back_to_local_defaults(self):
        config = Config(embedding=EmbeddingConfig(provider="unknown"))
        provider, model, dims, _ = _resolve_embedding_config(config)
        assert provider == "unknown"
        assert model == "all-MiniLM-L6-v2"
        assert dims == 384


class TestEmbedTextDispatch:
    def test_unknown_provider_returns_none(self):
        config = Config(embedding=EmbeddingConfig(provider="nonexistent"))
        result = embed_text("hello", config=config)
        assert result is None

    def test_openai_without_key_returns_none(self):
        config = Config(embedding=EmbeddingConfig(provider="openai"))
        with patch.dict("os.environ", {}, clear=True):
            result = embed_text("hello", config=config)
        assert result is None

    def test_gemini_without_key_returns_none(self):
        config = Config(embedding=EmbeddingConfig(provider="gemini"))
        with patch.dict("os.environ", {}, clear=True):
            result = embed_text("hello", config=config)
        assert result is None

    def test_local_dispatch(self):
        """embed_text with local provider calls _embed_local path."""
        config = Config(embedding=EmbeddingConfig(provider="local"))
        # Will return None if sentence-transformers not installed, which is fine
        result = embed_text("test", config=config)
        assert result is None or isinstance(result, list)


class TestProviderDefaults:
    def test_all_providers_have_required_keys(self):
        for name, defaults in PROVIDER_DEFAULTS.items():
            assert "model" in defaults, f"{name} missing model"
            assert "dimensions" in defaults, f"{name} missing dimensions"
            assert "api_key_env" in defaults, f"{name} missing api_key_env"

    def test_local_needs_no_api_key(self):
        assert PROVIDER_DEFAULTS["local"]["api_key_env"] == ""

    def test_remote_providers_have_api_key_env(self):
        for name in ("openai", "gemini"):
            assert PROVIDER_DEFAULTS[name]["api_key_env"] != "", f"{name} should require an API key"
