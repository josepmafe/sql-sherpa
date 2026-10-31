from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import create_autospec

import pytest
from sherpa_mcp.catalog import MartCatalog, MartCatalogLoader


@pytest.fixture
def empty_catalog_loader() -> MartCatalogLoader:
    """A loader whose catalog has no marts: enough for SQL that reads no table. One per test, since tests set its
    `load` side effect."""
    loader = create_autospec(MartCatalogLoader, instance=True)
    loader.load.return_value = MartCatalog(marts=(), metrics=(), generated_at=datetime(2026, 10, 1, tzinfo=UTC))
    return loader
