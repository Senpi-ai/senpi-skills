import pytest


@pytest.fixture(autouse=True)
def _own_catalog_cache(tmp_path_factory, monkeypatch):
    """_fetch keeps the catalog listing in ~/.cache/senpi by default: each test gets its own."""
    monkeypatch.setenv("SENPI_CATALOG_CACHE_DIR", str(tmp_path_factory.mktemp("catalog-cache")))
