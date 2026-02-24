"""Shared test fixtures."""

from pathlib import Path

import pytest

from kindex.config import Config
from kindex.vault import Vault

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_DATA = FIXTURES / "sample-vault"


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
