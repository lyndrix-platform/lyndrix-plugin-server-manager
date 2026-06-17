"""Smoke tests for the server_manager service and catalog loader."""
from __future__ import annotations

from pathlib import Path


# ── Service smoke test ────────────────────────────────────────────────────────

def test_service_singleton_exists():
    """server_manager_service is importable and not ready before set_context."""
    from app.controller.service import server_manager_service as svc

    assert svc is not None
    assert svc.is_ready is False


# ── Catalog smoke tests ───────────────────────────────────────────────────────

_EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
_CATALOG_DIR = Path(__file__).parent.parent / "catalog"


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


# ── os_type derivation + rule application (regression for B1) ─────────────────

def test_os_type_for_family_resolves_tokens():
    """OS family ids resolve to their global os_type token from products.yml."""
    from app.model.catalog import CatalogLoader

    products = CatalogLoader(catalog_dir=_CATALOG_DIR).products()
    assert products.os_type_for_family("WIN") == "windows"
    assert products.os_type_for_family("RHEL") == "linux"
    assert products.os_type_for_family("CRHEL") == "linux"
    assert products.os_type_for_family("SOL") == "unix"
    assert products.os_type_for_family("DOES_NOT_EXIST") is None
    assert products.os_type_for_family("") is None


def test_fce_windows_storage_rule_requires_os_type():
    """The FCE Windows os_storage_limit rule only fires once os_type is set.

    Proves B1: previously os_type was never populated, so applies_to_os rules
    were silently skipped. With os_type='windows' a >8 TB VM must be flagged.
    """
    from app.model.catalog import CatalogLoader

    loader = CatalogLoader(catalog_dir=_CATALOG_DIR)
    engine = loader.engine()
    profile = {
        "vcpu_count": 4,
        "ram_gb": 16,
        "os_family_id": "WIN",
        "storage_disks": [{"mount": "C:\\", "disk_id": "vdisk", "size_gb": 9000}],
    }
    kw = dict(
        server_type="vm",
        profile_id="fce",
        product_id=None,
        hardware_profile=profile,
        profile_catalog=loader.profiles(),
        product_catalog=loader.products(),
    )

    fired = [i for i in engine.validate(os_type="windows", **kw)
             if i["rule_id"] == "fce_windows_storage"]
    assert fired, "fce_windows_storage should fire for a >8 TB Windows VM"

    silent = [i for i in engine.validate(os_type=None, **kw)
              if i["rule_id"] == "fce_windows_storage"]
    assert not silent, "rule must stay inert when os_type is unknown"


# ── Storage-product rework + network removal ──────────────────────────────────

def test_storage_is_amount_based_and_network_removed():
    """Storage products carry no fixed size; network options are gone."""
    from app.model.catalog import CatalogLoader

    hw = CatalogLoader(catalog_dir=_CATALOG_DIR).hardware()
    storage_ids = [s["id"] for s in hw.get_storage_options()]
    assert "edc-block-ssd" in storage_ids
    assert "480gb-sata-ssd" not in storage_ids  # fixed-size SKUs removed
    assert all(s.get("size_gb") is None for s in hw.get_storage_options())
    assert hw.get_network_options() == []
    assert any(c.get("server_model") for c in hw.get_cpu_options())  # ProLiant Gen11


def test_edc_has_no_hardware_constraints():
    """EDC: no CPU↔RAM binding and no storage limit, regardless of amounts."""
    from app.model.catalog import CatalogLoader

    loader = CatalogLoader(catalog_dir=_CATALOG_DIR)
    profile = {
        "cpu_id": "intel-xeon-silver-4410y", "cpu_count": 1, "ram_gb": 4096,
        "storage_disks": [
            {"disk_id": "edc-block-ssd", "mount": "/d1", "size_gb": 50000},
            {"disk_id": "edc-block-nvme", "mount": "/d2", "size_gb": 50000},
            {"disk_id": "edc-capacity-hdd", "mount": "/d3", "size_gb": 99999},
        ],
    }
    rule_ids = [i["rule_id"] for i in loader.engine().validate(
        server_type="physical", profile_id="edc", product_id=None,
        hardware_profile=profile, os_type=None,
        profile_catalog=loader.profiles(), product_catalog=loader.products(),
    )]
    assert "edc_cores_ram_ratio" not in rule_ids
    assert "edc_max_storage_lines" not in rule_ids


def test_fce_windows_partition_and_total_limits():
    """FCE Windows: >4 partitions or >8 TB total both flag fce_windows_storage."""
    from app.model.catalog import CatalogLoader

    loader = CatalogLoader(catalog_dir=_CATALOG_DIR)

    def check(disks):
        return [i["rule_id"] for i in loader.engine().validate(
            server_type="vm", profile_id="fce", product_id=None,
            hardware_profile={"vcpu_count": 4, "ram_gb": 16, "os_family_id": "WIN",
                              "storage_disks": disks},
            os_type="windows",
            profile_catalog=loader.profiles(), product_catalog=loader.products(),
        )]

    five = [{"disk_id": "cloud-storage-hp", "mount": f"{c}:", "size_gb": 1000}
            for c in "CDEFG"]
    assert "fce_windows_storage" in check(five)  # 5 partitions

    within = [{"disk_id": "cloud-storage-hp", "mount": "C:", "size_gb": 2000},
              {"disk_id": "cloud-storage-hp", "mount": "D:", "size_gb": 2000}]
    assert "fce_windows_storage" not in check(within)  # 2 partitions / 4 TB
