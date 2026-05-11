"""Catalog loader — reads hardware / environments / profiles / products YAML files
and evaluates combination rules via a typed constraint engine.

Admin workflow
  1. Edit any catalog/*.yml file.
  2. Click "Reload Catalog" in the plugin settings page.
  3. No application restart needed.

Catalog files
-------------
    hardware.yml     – CPU / RAM / storage / network hardware items
    environments.yml – providers (legacy, cloud, …) and their stages (prod, dev, …)
    profiles.yml     – EDC / FCE profiles with rules and UI config
    products.yml     – product tiers with vCPU-RAM matrices and component restrictions
    settings.yml     – global values (server statuses, OS types)

Constraint rule types
---------------------
  Hardware catalog (combination_rules / legacy):
    required_fields        – field must be non-empty
    rule (expression)      – arbitrary boolean expression (restricted eval)

  Profile rules (profiles.yml):
    cores_ram_ratio        – physical total cores → RAM GB range (tiers)
    ram_module_parity      – total RAM module count divisible by `modulus`
    storage_count_limit    – max N storage entries
    vcpu_ram_ratio         – vCPU count → RAM GB range (tiers)
    os_storage_limit       – OS-specific storage constraints

  Product rules (products.yml):
    ram_total_minimum      – minimum total RAM GB
    ram_total_maximum      – maximum total RAM GB
    cpu_count_minimum      – minimum CPU socket / vCPU count
    cpu_count_maximum      – maximum CPU socket / vCPU count
    storage_total_minimum  – minimum total storage GB
    storage_total_maximum  – maximum total storage GB
    storage_size_minimum   – minimum single-disk size GB
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from core.logger import get_logger

log = get_logger("Plugin:ServerManager:Catalog")

_DEFAULT_CATALOG_DIR = Path(__file__).parent.parent.parent / "catalog"


# ── Hardware Catalog ─────────────────────────────────────────────────────────

class HardwareCatalog:
    """Parsed hardware catalog with lookup helpers."""

    def __init__(self, data: dict) -> None:
        self._data = data
        self._index: dict[str, dict] = {}
        for section in ("cpu_options", "ram_options", "storage_options", "network_options"):
            for item in data.get(section, []):
                self._index[item["id"]] = item

    def get_server_types(self) -> list[dict]:
        return list(self._data.get("server_types", []))

    def get_cpu_options(self, server_type: str | None = None) -> list[dict]:
        return self._filtered("cpu_options", server_type)

    def get_ram_options(self, server_type: str | None = None) -> list[dict]:
        return self._filtered("ram_options", server_type)

    def get_storage_options(self, server_type: str | None = None) -> list[dict]:
        return self._filtered("storage_options", server_type)

    def get_network_options(self, server_type: str | None = None) -> list[dict]:
        return self._filtered("network_options", server_type)

    def get_combination_rules(self) -> list[dict]:
        return list(self._data.get("combination_rules", []))

    def get_item(self, item_id: str) -> dict | None:
        return self._index.get(item_id)

    # ── Product-filtered accessors ────────────────────────────────────────────

    def get_cpu_options_for_product(
        self, server_type: str | None, allowed_ids: list[str] | None
    ) -> list[dict]:
        return self._filtered_by_product("cpu_options", server_type, allowed_ids)

    def get_ram_options_for_product(
        self, server_type: str | None, allowed_ids: list[str] | None
    ) -> list[dict]:
        return self._filtered_by_product("ram_options", server_type, allowed_ids)

    def get_storage_options_for_product(
        self, server_type: str | None, allowed_ids: list[str] | None
    ) -> list[dict]:
        return self._filtered_by_product("storage_options", server_type, allowed_ids)

    def get_network_options_for_product(
        self, server_type: str | None, allowed_ids: list[str] | None
    ) -> list[dict]:
        return self._filtered_by_product("network_options", server_type, allowed_ids)

    # ── Legacy rule evaluation (expression-based) ─────────────────────────────

    def validate_hardware(self, server_type: str, profile: dict) -> list[dict]:
        """Evaluate legacy combination_rules.  Returns [{rule_id, severity, message}]."""
        issues: list[dict] = []
        for rule in self.get_combination_rules():
            applies = rule.get("applies_to_types")
            if applies and server_type not in applies:
                continue
            for field in rule.get("required_fields", []):
                val = profile.get(field)
                if val is None or val == "" or val == [] or val == 0:
                    issues.append({
                        "rule_id": rule["id"],
                        "severity": rule.get("severity", "error"),
                        "message": rule.get("message", f"Required field '{field}' is missing."),
                    })
            expr = rule.get("rule")
            if expr and self._eval_expr(expr, profile):
                issues.append({
                    "rule_id": rule["id"],
                    "severity": rule.get("severity", "warning"),
                    "message": rule.get("message", f"Rule '{rule['id']}' violated."),
                })
        return issues

    # ── Private ───────────────────────────────────────────────────────────────

    def _filtered(self, section: str, server_type: str | None) -> list[dict]:
        opts = self._data.get(section, [])
        if server_type:
            opts = [o for o in opts if server_type in o.get("compatible_types", [])]
        return list(opts)

    def _filtered_by_product(
        self, section: str, server_type: str | None, allowed_ids: list[str] | None
    ) -> list[dict]:
        opts = self._filtered(section, server_type)
        if allowed_ids:
            opts = [o for o in opts if o["id"] in allowed_ids]
        return opts

    def _eval_expr(self, expr: str, profile: dict) -> bool:
        ctx: dict[str, Any] = dict(profile)
        cpu_id = profile.get("cpu_id")
        ctx["cpu"] = self.get_item(cpu_id) if cpu_id else {}
        ram_modules: list[dict] = profile.get("ram_modules") or []
        ctx["ram_count"] = sum(m.get("count", 1) for m in ram_modules)
        # Prefer direct ram_gb (new format), fall back to module sum (legacy)
        ctx["ram_total_gb"] = int(profile.get("ram_gb") or 0) or sum(
            (self.get_item(m.get("module_id")) or {}).get("size_gb", 0) * m.get("count", 1)
            for m in ram_modules
        )
        storage_disks: list[dict] = profile.get("storage_disks") or []
        ctx["storage_count"] = len(storage_disks)
        ctx["storage_total_gb"] = sum(
            (int(d.get("size_gb") or 0) or (self.get_item(d.get("disk_id") or "") or {}).get("size_gb", 0))
            * int(d.get("count", 1))
            for d in storage_disks
        ) or int(profile.get("storage_gb") or 0)
        try:
            return bool(eval(expr, {"__builtins__": {}}, ctx))  # noqa: S307
        except Exception as exc:
            log.debug(f"Catalog: expr eval error '{expr}': {exc}")
            return False


# ── Profile Catalog ───────────────────────────────────────────────────────────

class ProfileCatalog:
    """Parsed profiles.yml — EDC, FCE, and any custom profiles."""

    def __init__(self, data: dict) -> None:
        self._profiles: list[dict] = data.get("profiles", [])
        self._by_id: dict[str, dict] = {p["id"]: p for p in self._profiles}

    def get_all(self) -> list[dict]:
        return self._profiles

    def get(self, profile_id: str) -> dict | None:
        return self._by_id.get(profile_id)

    def get_features(self, profile_id: str) -> list[dict]:
        return list((self._by_id.get(profile_id) or {}).get("features", []))

    def get_rules(self, profile_id: str) -> list[dict]:
        return list((self._by_id.get(profile_id) or {}).get("rules", []))

    def allowed_server_types(self, profile_id: str) -> list[str]:
        profile = self._by_id.get(profile_id) or {}
        return profile.get("allowed_server_types", ["physical", "vm", "container"])

    def get_ram_steps(self, profile_id: str) -> list[int]:
        """Total-RAM step values (GB) for tile selection, from profiles.yml."""
        return list((self._by_id.get(profile_id) or {}).get("ram_steps_gb") or [32, 64, 128, 256, 512, 1024])

    def get_vcpu_tiles(self, profile_id: str) -> list[int]:
        """vCPU tile values for tile selection, from profiles.yml."""
        return list((self._by_id.get(profile_id) or {}).get("vcpu_tiles") or [1, 2, 4, 8, 16, 32, 64])

    def get_vcpu_ram_range(self, profile_id: str, vcpu_count: int) -> tuple[int, int]:
        """Return (min_ram_gb, max_ram_gb) for the given vCPU count from vcpu_ram_ratio rules.
        Returns (0, 999999) if no ratio rule found."""
        for rule in self.get_rules(profile_id):
            if rule.get("type") != "vcpu_ram_ratio":
                continue
            for tier in rule.get("ratios", []):
                if vcpu_count <= tier["vcpu_max"]:
                    return int(tier.get("ram_gb_min", 0)), int(tier.get("ram_gb_max", 999999))
        return 0, 999999

    def get_socket_options(self, profile_id: str) -> list[int]:
        """Socket count tile options for physical servers, from profiles.yml."""
        return list((self._by_id.get(profile_id) or {}).get("socket_options") or [1, 2, 4])

    def multi_storage_allowed(self, profile_id: str) -> bool:
        """True if the profile allows multiple different storage products per server."""
        for feat in (self._by_id.get(profile_id) or {}).get("features", []):
            if feat.get("id") == "multi_storage":
                return bool(feat.get("available", True))
        return True

    def get_ram_manual_max(self, profile_id: str) -> int:
        """Maximum value (GB) accepted by the free-text RAM input."""
        return int((self._by_id.get(profile_id) or {}).get("ram_manual_max_gb") or 8192)

    def get_disk_count_options(self, profile_id: str) -> list[int]:
        """Disk-count tile values for each storage line item."""
        return list((self._by_id.get(profile_id) or {}).get("disk_count_options") or [1, 2, 4, 6, 8])

    def get_vm_disk_size_steps(self, profile_id: str) -> list[int]:
        """Volume size preset tiles (GB) for virtual storage volumes."""
        return list((self._by_id.get(profile_id) or {}).get("vm_disk_size_steps_gb") or [10, 20, 50, 100, 200, 500, 1000, 2000])

    def get_storage_manual_max(self, profile_id: str) -> int:
        """Maximum value (GB) accepted by the free-text storage size input."""
        return int((self._by_id.get(profile_id) or {}).get("storage_manual_max_gb") or 131072)


# ── Product Catalog ───────────────────────────────────────────────────────────
class ProductCatalog:
    """Parsed products.yml — server product tiers with component constraints."""

    def __init__(self, data: dict) -> None:
        self._products: list[dict] = data.get("server_products", [])
        self._by_id: dict[str, dict] = {p["id"]: p for p in self._products}

    def get_all(self) -> list[dict]:
        return self._products

    def get(self, product_id: str) -> dict | None:
        return self._by_id.get(product_id)

    def get_for_profile(
        self, profile_id: str, server_type: str | None = None
    ) -> list[dict]:
        """Products compatible with a specific profile (and optionally server_type)."""
        result = []
        for p in self._products:
            if profile_id not in p.get("compatible_profiles", []):
                continue
            if server_type and server_type not in p.get("compatible_server_types", []):
                continue
            result.append(p)
        return result

    def get_allowed_ids(self, product_id: str, component: str) -> list[str] | None:
        """Allowed hardware IDs for a component.  None = no restriction (all allowed)."""
        product = self._by_id.get(product_id)
        if not product:
            return None
        key = f"allowed_{component}_ids"
        val = product.get(key)
        return val if val else None  # empty list treated as None (no restriction)

    def get_product_rules(self, product_id: str) -> list[dict]:
        return list((self._by_id.get(product_id) or {}).get("rules", []))

    # ── Schema-v2 accessors (service classes, OS variants, resources) ─────────

    def get_service_classes(self, product_id: str) -> list[dict]:
        """Selectable SK tiers for the product, in declared order. Empty if none."""
        return list((self._by_id.get(product_id) or {}).get("service_classes") or [])

    def get_service_class(self, product_id: str, sc_id: str) -> dict | None:
        for sc in self.get_service_classes(product_id):
            if sc.get("id") == sc_id:
                return sc
        return None

    def get_os_variants(self, product_id: str) -> list[dict]:
        """OS family variants. Empty list = use the legacy generic OS dropdown."""
        return list((self._by_id.get(product_id) or {}).get("os_variants") or [])

    def get_os_variant(self, product_id: str, family_id: str) -> dict | None:
        for v in self.get_os_variants(product_id):
            if v.get("id") == family_id:
                return v
        return None

    def get_os_version(
        self, product_id: str, family_id: str, version_id: str
    ) -> dict | None:
        v = self.get_os_variant(product_id, family_id)
        if not v:
            return None
        for ver in v.get("os_versions") or []:
            if ver.get("id") == version_id:
                return ver
        return None

    def get_resources(self, product_id: str) -> dict:
        return dict((self._by_id.get(product_id) or {}).get("resources") or {})

    def get_form_factor(self, product_id: str) -> str | None:
        return self.get_resources(product_id).get("form_factor")

    def get_ram_step(self, product_id: str) -> int:
        return int(self.get_resources(product_id).get("ram_step_gb") or 1)

    def get_ram_special_max(self, product_id: str) -> int | None:
        v = self.get_resources(product_id).get("ram_special_max_gb")
        return int(v) if v is not None else None

    def get_vcpu_ram_matrix(self, product_id: str) -> list[dict]:
        return list(self.get_resources(product_id).get("vcpu_ram_matrix") or [])

    def get_overcommit(self, product_id: str) -> dict:
        return dict((self._by_id.get(product_id) or {}).get("overcommit") or {})

    def get_hypervisor(self, product_id: str) -> dict:
        return dict((self._by_id.get(product_id) or {}).get("hypervisor") or {})

    # ── Matrix evaluation helper (used by UI for live tile greying) ───────────

    def matrix_row_for_vcpu(self, product_id: str, vcpu: int) -> dict | None:
        """Return the matrix tier that contains the given vCPU count, or None."""
        if vcpu <= 0:
            return None
        for row in self.get_vcpu_ram_matrix(product_id):
            if "vcpu" in row and int(row["vcpu"]) == vcpu:
                return row
            lo = row.get("vcpu_min")
            hi = row.get("vcpu_max")
            if lo is not None and hi is not None and lo <= vcpu <= hi:
                return row
        return None

    def allowed_vcpu_values(self, product_id: str) -> set[int]:
        """All vCPU values declared anywhere in the matrix (expanded for ranges)."""
        out: set[int] = set()
        for row in self.get_vcpu_ram_matrix(product_id):
            if "vcpu" in row:
                out.add(int(row["vcpu"]))
            else:
                lo = row.get("vcpu_min")
                hi = row.get("vcpu_max")
                if lo is not None and hi is not None:
                    out.update(range(int(lo), int(hi) + 1))
        return out

    def vcpu_allowed_for_os(self, product_id: str, vcpu: int, os_family: str) -> bool:
        """True if (vcpu, os_family) appears in the matrix."""
        row = self.matrix_row_for_vcpu(product_id, vcpu)
        if not row:
            return False
        per_os = row.get("per_os") or {}
        return os_family in per_os if os_family else bool(per_os)

    def ram_range_for(
        self, product_id: str, vcpu: int, os_family: str
    ) -> tuple[int, int] | None:
        """Return (ram_min, ram_max) for (vcpu, os_family), or None if combo invalid."""
        row = self.matrix_row_for_vcpu(product_id, vcpu)
        if not row:
            return None
        cfg = (row.get("per_os") or {}).get(os_family)
        if not cfg:
            return None
        return int(cfg.get("ram_min", 0)), int(cfg.get("ram_max", 0))

    def evaluate_combo(
        self,
        product_id: str,
        os_family: str | None,
        vcpu: int,
        ram_gb: int,
    ) -> dict:
        """Evaluate a (vCPU, OS, RAM) combination against the product's matrix.

        Returns dict with: status ('ok'|'special'|'invalid'|'unknown'),
        message, ram_min, ram_max, generation, alt_platform (bool).
        """
        result: dict = {
            "status": "unknown", "message": "",
            "ram_min": None, "ram_max": None,
            "generation": None, "alt_platform": False,
        }
        if not self.get_vcpu_ram_matrix(product_id):
            return result  # product has no matrix — caller should not call greying
        row = self.matrix_row_for_vcpu(product_id, vcpu)
        if not row:
            result.update(status="invalid", message=f"vCPU {vcpu} not allowed for this product.")
            return result
        result["generation"] = row.get("generation")
        result["alt_platform"] = row.get("generation") == "alt_platform"
        if not os_family:
            return result  # need OS to evaluate RAM
        cfg = (row.get("per_os") or {}).get(os_family)
        if not cfg:
            result.update(
                status="invalid",
                message=f"OS family '{os_family}' not allowed at {vcpu} vCPU.",
            )
            return result
        rmin = int(cfg.get("ram_min", 0))
        rmax = int(cfg.get("ram_max", 0))
        result["ram_min"] = rmin
        result["ram_max"] = rmax
        if ram_gb <= 0:
            return result
        special = self.get_ram_special_max(product_id)
        if ram_gb < rmin or ram_gb > rmax:
            if special is not None and rmax < ram_gb <= special:
                result.update(
                    status="special",
                    message=f"RAM {ram_gb} GB exceeds standard max ({rmax} GB) — "
                            f"special sizing approval required (max {special} GB).",
                )
                return result
            result.update(
                status="invalid",
                message=f"RAM {ram_gb} GB outside allowed range ({rmin}–{rmax} GB) "
                        f"for {os_family} at {vcpu} vCPU.",
            )
            return result
        result["status"] = "ok"
        return result


# ── Settings Catalog ─────────────────────────────────────────────────────────

class SettingsCatalog:
    """Parsed settings.yml — global values shared across all profiles and products."""

    def __init__(self, data: dict) -> None:
        self._statuses: list[dict] = data.get("server_statuses", [])
        self._os_types: list[dict] = data.get("os_types", [])

    def get_status_ids(self) -> list[str]:
        """Ordered list of status id strings (e.g. ['active', 'ordered', ...])."""
        return [s["id"] for s in self._statuses]

    def get_statuses(self) -> list[dict]:
        """Full status dicts with id, label, color, terminal."""
        return list(self._statuses)

    def get_os_types(self) -> list[dict]:
        """Full OS type dicts with id, label, icon."""
        return list(self._os_types)

    def get_os_type_map(self) -> dict[str, str]:
        """Dict of {id: label} for use in ui.select options."""
        return {o["id"]: o["label"] for o in self._os_types}


# ── Constraint Engine ─────────────────────────────────────────────────────────

class ConstraintEngine:
    """Evaluates profile rules + product rules against a server's hardware profile.

    All rule types are handled as named methods; adding a new rule type requires
    only adding a new `_rule_<type>` method and updating RULE_HANDLERS.
    """

    def __init__(self, hw: HardwareCatalog) -> None:
        self._hw = hw

    def validate(
        self,
        *,
        server_type: str,
        profile_id: str,
        product_id: str | None,
        hardware_profile: dict,
        os_type: str | None,
        profile_catalog: ProfileCatalog,
        product_catalog: ProductCatalog,
    ) -> list[dict]:
        """Return a list of constraint violations: [{rule_id, severity, message}]."""
        issues: list[dict] = []

        # Make product context available to v2 rule handlers (vcpu_ram_matrix, ...)
        self._ctx = {
            "product_id": product_id,
            "product_catalog": product_catalog,
            "os_family": (hardware_profile or {}).get("os_family_id"),
        }

        # 1. Legacy hardware combination_rules (backward compat)
        issues.extend(self._hw.validate_hardware(server_type, hardware_profile))

        # 2. Profile rules
        for rule in profile_catalog.get_rules(profile_id):
            applies_to_types = rule.get("applies_to_types")
            if applies_to_types and server_type not in applies_to_types:
                continue
            applies_to_os = rule.get("applies_to_os")
            if applies_to_os and os_type and os_type not in applies_to_os:
                continue
            issues.extend(self._eval(rule, server_type, hardware_profile, os_type))

        # 3. Product rules
        if product_id:
            for rule in product_catalog.get_product_rules(product_id):
                applies_to_types = rule.get("applies_to_types")
                if applies_to_types and server_type not in applies_to_types:
                    continue
                issues.extend(self._eval(rule, server_type, hardware_profile, os_type))

        return issues

    # ── Rule dispatch ─────────────────────────────────────────────────────────

    def _eval(
        self,
        rule: dict,
        server_type: str,
        profile: dict,
        os_type: str | None,
    ) -> list[dict]:
        rule_type = rule.get("type", "")
        handler = {
            "cores_ram_ratio":       self._rule_cores_ram_ratio,
            "ram_module_parity":     self._rule_ram_module_parity,
            "storage_count_limit":   self._rule_storage_count_limit,
            "vcpu_ram_ratio":        self._rule_vcpu_ram_ratio,
            "os_storage_limit":      self._rule_os_storage_limit,
            "ram_total_minimum":     self._rule_ram_total_minimum,
            "ram_total_maximum":     self._rule_ram_total_maximum,
            "cpu_count_minimum":     self._rule_cpu_count_minimum,
            "cpu_count_maximum":     self._rule_cpu_count_maximum,
            "storage_total_minimum": self._rule_storage_total_minimum,
            "storage_total_maximum": self._rule_storage_total_maximum,
            "storage_size_minimum":  self._rule_storage_size_minimum,
            "vcpu_ram_matrix":       self._rule_vcpu_ram_matrix,
            "ram_step_check":        self._rule_ram_step_check,
            "ram_special_max":       self._rule_ram_special_max,
        }.get(rule_type)
        if handler:
            return handler(rule, server_type, profile, os_type)
        return []

    def _issue(self, rule: dict, message: str) -> dict:
        return {
            "rule_id": rule["id"],
            "severity": rule.get("severity", "error"),
            "message": message,
        }

    # ── Profile rule handlers ─────────────────────────────────────────────────

    def _rule_cores_ram_ratio(
        self, rule: dict, server_type: str, profile: dict, _os: str | None
    ) -> list[dict]:
        """Physical: total cores constrains RAM range."""
        if server_type != "physical":
            return []
        cpu_id = profile.get("cpu_id")
        cpu_item = self._hw.get_item(cpu_id) if cpu_id else None
        cores_per_socket = (cpu_item or {}).get("cores") or 0
        socket_count = int(profile.get("cpu_count") or 1)
        total_cores = cores_per_socket * socket_count
        if total_cores == 0:
            return []  # can't evaluate without CPU

        ram_total_gb = self._ram_total_gb(profile)
        if ram_total_gb == 0:
            return []  # will be caught by required_fields rule

        for tier in rule.get("tiers", []):
            if total_cores <= tier["cores_max"]:
                min_gb = tier["ram_gb_min"]
                max_gb = tier["ram_gb_max"]
                if ram_total_gb < min_gb:
                    return [self._issue(rule,
                        f"With {total_cores} cores you need at least {min_gb} GB RAM "
                        f"(currently {ram_total_gb} GB).")]
                if ram_total_gb > max_gb:
                    return [self._issue(rule,
                        f"With {total_cores} cores you cannot exceed {max_gb} GB RAM "
                        f"(currently {ram_total_gb} GB).")]
                return []
        return []

    def _rule_ram_module_parity(
        self, rule: dict, server_type: str, profile: dict, _os: str | None
    ) -> list[dict]:
        modulus = int(rule.get("modulus", 2))
        total_modules = sum(m.get("count", 1) for m in (profile.get("ram_modules") or []))
        if total_modules > 0 and total_modules % modulus != 0:
            return [self._issue(rule,
                f"RAM module count ({total_modules}) should be a multiple of {modulus} "
                f"for optimal dual-channel performance.")]
        return []

    def _rule_storage_count_limit(
        self, rule: dict, server_type: str, profile: dict, _os: str | None
    ) -> list[dict]:
        max_count = int(rule.get("max_count", 8))
        if server_type == "physical":
            entries = len(profile.get("storage_disks") or [])
        else:
            disks = profile.get("storage_disks") or []
            if disks:
                # Count distinct storage product *types* (FCE allows only 1 type)
                entries = len({d.get("disk_id") for d in disks if d.get("disk_id")})
            else:
                entries = 1 if profile.get("storage_gb") else 0
        if entries > max_count:
            return [self._issue(rule,
                f"Maximum {max_count} storage product type(s) allowed; {entries} in use.")]
        return []

    def _rule_vcpu_ram_ratio(
        self, rule: dict, server_type: str, profile: dict, _os: str | None
    ) -> list[dict]:
        vcpu_count = profile.get("vcpu_count") or 0
        ram_gb = profile.get("ram_gb") or 0
        if not vcpu_count or not ram_gb:
            return []
        for tier in rule.get("ratios", []):
            if vcpu_count <= tier["vcpu_max"]:
                min_gb = tier["ram_gb_min"]
                max_gb = tier["ram_gb_max"]
                if ram_gb < min_gb:
                    return [self._issue(rule,
                        f"With {vcpu_count} vCPU(s) you need at least {min_gb} GB RAM "
                        f"(currently {ram_gb} GB).")]
                if ram_gb > max_gb:
                    return [self._issue(rule,
                        f"With {vcpu_count} vCPU(s) you cannot exceed {max_gb} GB RAM "
                        f"(currently {ram_gb} GB).")]
                return []
        return []

    def _rule_os_storage_limit(
        self, rule: dict, server_type: str, profile: dict, os_type: str | None
    ) -> list[dict]:
        applies_to_os = rule.get("applies_to_os", [])
        if applies_to_os and os_type not in applies_to_os:
            return []
        max_total = rule.get("max_total_gb")
        issues: list[dict] = []
        total_gb = self._storage_total_gb(profile)

        if max_total and total_gb > max_total:
            tb = max_total / 1024
            issues.append(self._issue(rule,
                f"Total storage ({total_gb} GB) exceeds the {tb:.0f} TB OS limit for "
                f"{os_type or 'this OS'}."))

        if not rule.get("partitions_unlimited"):
            max_p = rule.get("max_partitions")
            max_gb_at_max = rule.get("max_gb_per_partition_at_max_partitions")
            # Partition count from storage_disks total-count for physical,
            # not directly modeled for VMs — surfaced as advisory info only.
            if max_p and server_type == "physical":
                total_disks = sum(d.get("count", 1) for d in (profile.get("storage_disks") or []))
                if total_disks > max_p:
                    issues.append(self._issue(rule,
                        f"OS allows max {max_p} partitions; {total_disks} disk(s) configured."))
                elif total_disks == max_p and max_gb_at_max:
                    # Each disk must stay ≤ max_gb_at_max
                    for entry in (profile.get("storage_disks") or []):
                        item = self._hw.get_item(entry.get("disk_id") or "")
                        sz = (item or {}).get("size_gb") or 0
                        if sz > max_gb_at_max:
                            issues.append(self._issue(rule,
                                f"With {max_p} partitions, each disk may not exceed "
                                f"{max_gb_at_max} GB (found {sz} GB disk)."))
        return issues

    # ── Product rule handlers ─────────────────────────────────────────────────

    def _rule_ram_total_minimum(
        self, rule: dict, server_type: str, profile: dict, _os: str | None
    ) -> list[dict]:
        min_gb = rule.get("ram_gb_min", 0)
        actual = self._ram_total_gb(profile) if server_type == "physical" else (profile.get("ram_gb") or 0)
        if actual and actual < min_gb:
            return [self._issue(rule,
                f"This product requires at least {min_gb} GB RAM (currently {actual} GB).")]
        return []

    def _rule_ram_total_maximum(
        self, rule: dict, server_type: str, profile: dict, _os: str | None
    ) -> list[dict]:
        max_gb = rule.get("ram_gb_max", 0)
        actual = self._ram_total_gb(profile) if server_type == "physical" else (profile.get("ram_gb") or 0)
        if actual and max_gb and actual > max_gb:
            return [self._issue(rule,
                f"This product allows a maximum of {max_gb} GB RAM (currently {actual} GB).")]
        return []

    def _rule_cpu_count_minimum(
        self, rule: dict, server_type: str, profile: dict, _os: str | None
    ) -> list[dict]:
        min_count = rule.get("count_min", 1)
        actual = int(profile.get("cpu_count") or 1) if server_type == "physical" else int(profile.get("vcpu_count") or 0)
        if actual and actual < min_count:
            noun = "sockets" if server_type == "physical" else "vCPUs"
            return [self._issue(rule,
                f"This product requires at least {min_count} CPU {noun} (currently {actual}).")]
        return []

    def _rule_cpu_count_maximum(
        self, rule: dict, server_type: str, profile: dict, _os: str | None
    ) -> list[dict]:
        max_count = rule.get("count_max", 9999)
        actual = int(profile.get("cpu_count") or 1) if server_type == "physical" else int(profile.get("vcpu_count") or 0)
        if actual and actual > max_count:
            noun = "sockets" if server_type == "physical" else "vCPUs"
            return [self._issue(rule,
                f"This product allows a maximum of {max_count} CPU {noun} (currently {actual}).")]
        return []

    def _rule_storage_total_minimum(
        self, rule: dict, server_type: str, profile: dict, _os: str | None
    ) -> list[dict]:
        min_gb = rule.get("storage_gb_min", 0)
        actual = self._storage_total_gb(profile)
        if actual and actual < min_gb:
            tb = min_gb / 1024
            return [self._issue(rule,
                f"This product requires at least {tb:.1f} TB storage (currently {actual} GB).")]
        return []

    def _rule_storage_total_maximum(
        self, rule: dict, server_type: str, profile: dict, _os: str | None
    ) -> list[dict]:
        max_gb = rule.get("storage_gb_max", 0)
        actual = self._storage_total_gb(profile)
        if actual and max_gb and actual > max_gb:
            tb = max_gb / 1024
            return [self._issue(rule,
                f"This product allows a maximum of {tb:.1f} TB storage (currently {actual} GB).")]
        return []

    def _rule_storage_size_minimum(
        self, rule: dict, server_type: str, profile: dict, _os: str | None
    ) -> list[dict]:
        min_gb = rule.get("size_gb_min", 0)
        if server_type != "physical":
            return []
        for entry in (profile.get("storage_disks") or []):
            item = self._hw.get_item(entry.get("disk_id") or "")
            sz = (item or {}).get("size_gb") or 0
            if sz and sz < min_gb:
                label = (item or {}).get("label", entry.get("disk_id"))
                return [self._issue(rule,
                    f"This product requires disks of at least {min_gb} GB; "
                    f"'{label}' is {sz} GB.")]
        return []

    # ── v2 product-matrix rule handlers ───────────────────────────────────────

    def _rule_vcpu_ram_matrix(
        self, rule: dict, server_type: str, profile: dict, _os: str | None
    ) -> list[dict]:
        """Validate (vCPU, OS-family, RAM) against the product's vcpu_ram_matrix."""
        ctx = getattr(self, "_ctx", {}) or {}
        product_id = ctx.get("product_id")
        pcat: ProductCatalog | None = ctx.get("product_catalog")
        os_family = ctx.get("os_family")
        if not (product_id and pcat):
            return []
        if not pcat.get_vcpu_ram_matrix(product_id):
            return []
        vcpu = int(profile.get("vcpu_count") or profile.get("cpu_count") or 0)
        ram = self._ram_total_gb(profile)
        if vcpu == 0 or ram == 0:
            return []
        verdict = pcat.evaluate_combo(product_id, os_family, vcpu, ram)
        if verdict["status"] == "invalid":
            return [self._issue(rule, verdict["message"])]
        if verdict["status"] == "special":
            issue = self._issue(rule, verdict["message"])
            issue["severity"] = rule.get("severity", "warning")
            return [issue]
        return []

    def _rule_ram_step_check(
        self, rule: dict, server_type: str, profile: dict, _os: str | None
    ) -> list[dict]:
        ctx = getattr(self, "_ctx", {}) or {}
        product_id = ctx.get("product_id")
        pcat: ProductCatalog | None = ctx.get("product_catalog")
        if not (product_id and pcat):
            return []
        step = pcat.get_ram_step(product_id)
        if step <= 1:
            return []
        ram = self._ram_total_gb(profile)
        if ram > 0 and ram % step != 0:
            return [self._issue(rule,
                f"RAM ({ram} GB) must be a multiple of {step} GB for this product.")]
        return []

    def _rule_ram_special_max(
        self, rule: dict, server_type: str, profile: dict, _os: str | None
    ) -> list[dict]:
        ctx = getattr(self, "_ctx", {}) or {}
        product_id = ctx.get("product_id")
        pcat: ProductCatalog | None = ctx.get("product_catalog")
        if not (product_id and pcat):
            return []
        special = pcat.get_ram_special_max(product_id)
        if special is None:
            return []
        vcpu = int(profile.get("vcpu_count") or profile.get("cpu_count") or 0)
        os_family = ctx.get("os_family")
        ram = self._ram_total_gb(profile)
        if vcpu == 0 or ram == 0 or not os_family:
            return []
        rng = pcat.ram_range_for(product_id, vcpu, os_family)
        if not rng:
            return []
        std_max = rng[1]
        if std_max < ram <= special:
            issue = self._issue(rule,
                f"RAM {ram} GB exceeds standard max ({std_max} GB) — "
                f"special sizing approval required (max {special} GB).")
            issue["severity"] = rule.get("severity", "warning")
            return [issue]
        return []

    # ── Aggregation helpers ───────────────────────────────────────────────────

    def _ram_total_gb(self, profile: dict) -> int:
        # New format: ram_gb stored directly
        if profile.get("ram_gb"):
            return int(profile["ram_gb"])
        # Legacy: sum from ram_modules
        return sum(
            (self._hw.get_item(m.get("module_id")) or {}).get("size_gb", 0) * m.get("count", 1)
            for m in (profile.get("ram_modules") or [])
        )

    def _storage_total_gb(self, profile: dict) -> int:
        disks = profile.get("storage_disks") or []
        if disks:
            return sum(
                (int(d.get("size_gb") or 0) or (self._hw.get_item(d.get("disk_id") or "") or {}).get("size_gb", 0))
                * int(d.get("count", 1))
                for d in disks
            )
        # Legacy single-value fallback
        return int(profile.get("storage_gb") or 0)


# ── Environment Catalog ───────────────────────────────────────────────────────

class EnvironmentCatalog:
    """Parsed environment catalog.

    Supports the v2 provider→stage hierarchy (``providers`` key in YAML).
    The compound ``environment_id`` stored on server records is
    ``"{provider_id}:{stage_id}"``, e.g. ``"legacy:prod"``.

    Falls back to the legacy flat ``environments`` list for backward compat.
    """

    def __init__(self, data: dict) -> None:
        self._providers: dict[str, dict] = {}
        # flat map: compound_id → merged stage dict
        self._flat: dict[str, dict] = {}

        if "providers" in data:
            for provider in data.get("providers", []):
                pid = provider["id"]
                self._providers[pid] = provider
                for stage in provider.get("stages", []):
                    sid = stage["id"]
                    compound = f"{pid}:{sid}"
                    self._flat[compound] = {
                        **stage,
                        "id": compound,
                        "provider_id": pid,
                        "provider_label": provider.get("display_label") or provider.get("label", pid),
                        "profile_id": provider.get("profile_id", "edc"),
                        "placeholder": provider.get("placeholder", False),
                    }
        else:
            # legacy flat format
            for env in data.get("environments", []):
                self._flat[env["id"]] = env

    # ── Provider API ──────────────────────────────────────────────────────────

    def get_providers(self) -> list[dict]:
        """Return all providers (top-level entries)."""
        return list(self._providers.values())

    def get_provider(self, provider_id: str) -> dict | None:
        return self._providers.get(provider_id)

    def get_stages_for_provider(self, provider_id: str) -> list[dict]:
        """Return flattened stage dicts for a given provider."""
        prefix = f"{provider_id}:"
        return [v for k, v in self._flat.items() if k.startswith(prefix)]

    # ── Flat API (used by rest of codebase) ───────────────────────────────────

    def get_all(self) -> list[dict]:
        return list(self._flat.values())

    def get(self, env_id: str) -> dict | None:
        return self._flat.get(str(env_id)) if env_id else None

    def get_profile_id(self, env_id: str) -> str:
        """Return the profile_id for an environment ('edc', 'fce', ...)."""
        env = self._flat.get(str(env_id)) if env_id else None
        return (env or {}).get("profile_id", "edc")


# ── Loader ────────────────────────────────────────────────────────────────────

class CatalogLoader:
    """Loads and caches all four catalog files from a configurable directory.

    Call reload() to force a re-read from disk (no restart needed).
    """

    def __init__(self, catalog_dir: Path | None = None) -> None:
        self._dir = Path(catalog_dir) if catalog_dir else _DEFAULT_CATALOG_DIR
        self._hardware: HardwareCatalog | None = None
        self._environments: EnvironmentCatalog | None = None
        self._profiles: ProfileCatalog | None = None
        self._products: ProductCatalog | None = None
        self._engine: ConstraintEngine | None = None
        self._settings: SettingsCatalog | None = None

    @property
    def catalog_dir(self) -> Path:
        return self._dir

    @catalog_dir.setter
    def catalog_dir(self, path: Path | str) -> None:
        self._dir = Path(path)
        self.reload()

    def hardware(self) -> HardwareCatalog:
        if self._hardware is None:
            self._hardware = HardwareCatalog(self._load("hardware.yml"))
            self._engine = None  # invalidate
        return self._hardware

    def environments(self) -> EnvironmentCatalog:
        if self._environments is None:
            self._environments = EnvironmentCatalog(self._load("environments.yml"))
        return self._environments

    def profiles(self) -> ProfileCatalog:
        if self._profiles is None:
            self._profiles = ProfileCatalog(self._load("profiles.yml"))
        return self._profiles

    def products(self) -> ProductCatalog:
        if self._products is None:
            self._products = ProductCatalog(self._load("products.yml"))
        return self._products

    def engine(self) -> ConstraintEngine:
        if self._engine is None:
            self._engine = ConstraintEngine(self.hardware())
        return self._engine

    def settings(self) -> SettingsCatalog:
        if self._settings is None:
            self._settings = SettingsCatalog(self._load("settings.yml"))
        return self._settings

    def reload(self) -> None:
        """Force re-read of all catalog files from disk."""
        self._hardware = None
        self._environments = None
        self._profiles = None
        self._products = None
        self._engine = None
        self._settings = None
        log.info(f"Catalog: reloaded from {self._dir}")

    # ── Private ───────────────────────────────────────────────────────────────

    def _load(self, filename: str) -> dict:
        for suffix in (".yml", ".yaml", ".json"):
            path = self._dir / Path(filename).with_suffix(suffix)
            if path.exists():
                try:
                    text = path.read_text(encoding="utf-8")
                    if suffix == ".json":
                        return json.loads(text)
                    return yaml.safe_load(text) or {}
                except Exception as exc:
                    log.error(f"Catalog: failed to load {path}: {exc}")
                    return {}
        log.warning(f"Catalog: file not found: {self._dir / filename}")
        return {}
