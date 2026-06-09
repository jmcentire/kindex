"""Shared test fixtures."""

from pathlib import Path

import pytest

from kindex.config import Config
from kindex.vault import Vault

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_DATA = FIXTURES / "sample-vault"

# Provider API keys that, if inherited from the developer's real environment,
# would make extraction/summarization hit live APIs — turning deterministic
# keyword-fallback tests into non-deterministic ones (and spending money).
_PROVIDER_KEY_ENVS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)


@pytest.fixture(autouse=True)
def hermetic_provider_env(monkeypatch):
    """Keep the test suite hermetic.

    Strips ambient LLM provider keys so code paths that consult the environment
    (e.g. ``extract()`` -> ``llm_extract`` -> ``_get_client``) fall back to the
    deterministic keyword extractor instead of calling a live API. Tests that
    exercise the LLM path set their own (fake) key and mock the client, which
    runs after this fixture and therefore overrides it.
    """
    for var in _PROVIDER_KEY_ENVS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def sample_config():
    """Config pointing at the sample fixture data."""
    return Config(data_dir=str(SAMPLE_DATA))


@pytest.fixture
def sample_vault(sample_config):
    """Loaded vault from fixture data."""
    return Vault(sample_config).load()


@pytest.fixture
def tmp_vault(tmp_path):
    """Empty vault in a temp directory for write tests."""
    cfg = Config(data_dir=str(tmp_path))
    v = Vault(cfg)
    v.ensure_dirs()
    return v.load()
