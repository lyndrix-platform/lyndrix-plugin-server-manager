"""Smoke tests for the server_manager service and catalog loader."""
from __future__ import annotations

from pathlib import Path

import pytest


# ── Service smoke test ────────────────────────────────────────────────────────

def test_service_singleton_exists():
    """server_manager_service is importable and not ready before set_context."""
    from app.controller.service import server_manager_service as svc

    assert svc is not None
    assert svc.is_ready is False


# ── Catalog smoke tests ───────────────────────────────────────────────────────

_EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_catalog_parses_hardware_yml():
    """CatalogLoader can parse examples/hardware.yml without raising."""
    from app.model.catalog import CatalogLoader

    loader = CatalogLoader(catalog_dir=_EXAMPLES_DIR)
    hw = loader.hardware()
    # Should return a HardwareCatalog with at least an empty server_types list
    assert hasattr(hw, "get_server_types")
    assert isinstance(hw.get_server_types(), list)


def test_catalog_parses_environments_yml():
    """CatalogLoader can parse examples/environments.yml without raising."""
    from app.model.catalog import CatalogLoader

    loader = CatalogLoader(catalog_dir=_EXAMPLES_DIR)
    envs = loader.environments()
    assert hasattr(envs, "get_all")
    assert isinstance(envs.get_all(), list)
