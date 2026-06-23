"""REST API for Server Manager.

A single router is mounted by core under
``/api/plugins/lyndrix.plugin.server_manager/`` via ``ctx.register_routes()``.
The registry enforces authentication for every route automatically; we
additionally require ``api:read`` on reads and ``api:write`` on mutations so a
merely-authenticated user cannot create/edit/delete servers or reconfigure the
catalog without the write permission.

The React CRUD UI and the NiceGUI configurator both call the same
``ServerManagerService`` singleton — this router is the HTTP surface used by the
React bundle. ``ServerRecord.to_dict()`` is the JSON shape returned for servers;
``hardware_profile`` is passed through verbatim because its keys vary by
``server_type`` (physical sockets vs. vCPU, etc.).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from core.api import ApiIdentity, require_permission

from .controller.service import ServerManagerService
from .model.catalog import _DEFAULT_CATALOG_DIR


# ── Payloads ───────────────────────────────────────────────────────────────────

class ServerCreatePayload(BaseModel):
    name: str
    hostname: Optional[str] = None
    environment_id: str
    server_type: str
    product_id: Optional[str] = None
    service_class_id: Optional[str] = None
    os_type: Optional[str] = None
    os_family_id: Optional[str] = None
    os_version_id: Optional[str] = None
    status: Optional[str] = None
    hardware_profile: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class ServerUpdatePayload(BaseModel):
    # Every field optional — only fields actually supplied are applied (PATCH).
    model_config = ConfigDict(extra="ignore")

    name: Optional[str] = None
    hostname: Optional[str] = None
    environment_id: Optional[str] = None
    server_type: Optional[str] = None
    product_id: Optional[str] = None
    service_class_id: Optional[str] = None
    os_type: Optional[str] = None
    os_family_id: Optional[str] = None
    os_version_id: Optional[str] = None
    status: Optional[str] = None
    hardware_profile: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    notes: Optional[str] = None


class ValidatePayload(BaseModel):
    server_type: str
    profile: Dict[str, Any] = Field(default_factory=dict)
    profile_id: Optional[str] = None
    product_id: Optional[str] = None
    environment_id: Optional[str] = None
    os_type: Optional[str] = None
    os_family_id: Optional[str] = None


class CatalogPathPayload(BaseModel):
    # Empty / null path resets to the built-in catalog shipped with the plugin.
    path: Optional[str] = None


class EvaluateComboPayload(BaseModel):
    # Authoritative (vCPU, OS-family, RAM) verdict for the step-2 VM matrix tiles.
    product_id: str
    os_family_id: Optional[str] = None
    vcpu: int
    ram_gb: int


# ── Catalog projection ──────────────────────────────────────────────────────────

def _catalog_payload(service: ServerManagerService) -> dict:
    """Everything the React forms + settings view need, in one shot.

    The wizard drives the full 3-step configurator from this single projection:
    providers→stages cascade, profile tile/limit config + features, product
    service-classes/os-variants/resources, and the raw hardware option lists.
    All values come straight from the catalog accessors so the React layer never
    re-implements the matrix/validation logic (that stays in the Python service).
    """
    cat = service.catalog
    hw = cat.hardware()
    envs = cat.environments()
    profiles = cat.profiles()
    products = cat.products()
    settings = cat.settings()

    current_dir = str(cat.catalog_dir)
    is_default = Path(current_dir) == _DEFAULT_CATALOG_DIR

    environments = [
        {
            "id": e.get("id"),
            "label": e.get("label", e.get("id")),
            "provider_id": e.get("provider_id"),
            "provider_label": e.get("provider_label"),
            "profile_id": e.get("profile_id", "edc"),
        }
        for e in envs.get_all()
    ]

    # Provider → stage cascade (step 1). Stages carry the compound environment_id.
    providers = []
    for p in envs.get_providers():
        pid = p["id"]
        providers.append({
            "id": pid,
            "label": p.get("display_label") or p.get("label", pid),
            "profile_id": p.get("profile_id", "edc"),
            "icon": p.get("icon"),
            "color": p.get("color"),
            "placeholder": p.get("placeholder", False),
            "description": p.get("description"),
            "stages": [
                {
                    "id": s["id"],  # "provider:stage"
                    "label": s.get("label", s["id"]),
                    "icon": s.get("icon"),
                    "color": s.get("color"),
                    "order_workflow": s.get("order_workflow"),
                    "description": s.get("description"),
                }
                for s in envs.get_stages_for_provider(pid)
            ],
        })

    profiles_out = []
    for p in profiles.get_all():
        pid = p.get("id")
        profiles_out.append({
            "id": pid,
            "label": p.get("label", pid),
            "icon": p.get("icon"),
            "color": p.get("color"),
            "allowed_server_types": profiles.allowed_server_types(pid),
            "features": profiles.get_features(pid),
            "ram_steps_gb": profiles.get_ram_steps(pid),
            "vcpu_tiles": profiles.get_vcpu_tiles(pid),
            "socket_options": profiles.get_socket_options(pid),
            "ram_manual_max_gb": profiles.get_ram_manual_max(pid),
            "storage_manual_max_gb": profiles.get_storage_manual_max(pid),
            "multi_storage": profiles.multi_storage_allowed(pid),
        })

    products_out = []
    for p in products.get_all():
        pid = p.get("id")
        products_out.append({
            "id": pid,
            "label": p.get("label", pid),
            "icon": p.get("icon"),
            "description": p.get("description"),
            "compatible_profiles": p.get("compatible_profiles", []),
            "compatible_server_types": p.get("compatible_server_types", []),
            "service_classes": products.get_service_classes(pid),
            "os_variants": products.get_os_variants(pid),
            "allowed_cpu_ids": products.get_allowed_ids(pid, "cpu"),
            "allowed_storage_ids": products.get_allowed_ids(pid, "storage"),
            "resources": products.get_resources(pid),
        })

    hardware = {
        "cpu_options": [
            {
                "id": c.get("id"),
                "label": c.get("label", c.get("id")),
                "cores": c.get("cores"),
                "threads": c.get("threads"),
                "tdp_w": c.get("tdp_w"),
                "base_ghz": c.get("base_ghz"),
                "server_model": c.get("server_model"),
                "compatible_types": c.get("compatible_types", []),
            }
            for c in hw.get_cpu_options()
        ],
        "storage_options": [
            {
                "id": s.get("id"),
                "label": s.get("label", s.get("id")),
                "type": s.get("type"),
                "description": s.get("description"),
                "compatible_types": s.get("compatible_types", []),
            }
            for s in hw.get_storage_options()
        ],
        "ram_options": [
            {
                "id": r.get("id"),
                "label": r.get("label", r.get("id")),
                "size_gb": r.get("size_gb"),
                "compatible_types": r.get("compatible_types", []),
            }
            for r in hw.get_ram_options()
        ],
    }

    return {
        "catalog_dir": current_dir,
        "is_default": is_default,
        "environments": environments,
        "providers": providers,
        "server_types": hw.get_server_types(),
        "statuses": settings.get_statuses(),
        "os_types": settings.get_os_types(),
        "profiles": profiles_out,
        "products": products_out,
        "hardware": hardware,
        "stats": {
            "cpu_options": len(hw.get_cpu_options()),
            "ram_options": len(hw.get_ram_options()),
            "storage_options": len(hw.get_storage_options()),
            "network_options": len(hw.get_network_options()),
            "profiles": len(profiles.get_all()),
            "products": len(products.get_all()),
            "providers": len(envs.get_providers()),
            "stages": len(envs.get_all()),
            "legacy_rules": len(hw.get_combination_rules()),
        },
    }


# ── Router ──────────────────────────────────────────────────────────────────────

def build_plugin_router(service: ServerManagerService) -> APIRouter:
    """The single Server Manager router — core mounts it at /api/plugins/<id>/."""
    router = APIRouter(tags=["Server Manager"])

    # ── Servers (CRUD) ─────────────────────────────────────────────────────────
    @router.get("/servers")
    async def list_servers(identity: ApiIdentity = Depends(require_permission("api:read"))):
        return {"servers": service.get_all_servers()}

    @router.get("/servers/{server_id}")
    async def get_server(
        server_id: int,
        identity: ApiIdentity = Depends(require_permission("api:read")),
    ):
        server = service.get_server(server_id)
        if server is None:
            raise HTTPException(status_code=404, detail=f"Unknown server id: {server_id}")
        return {"server": server}

    @router.post("/servers")
    async def create_server(
        payload: ServerCreatePayload,
        identity: ApiIdentity = Depends(require_permission("api:write")),
    ):
        try:
            server = service.create_server(payload.model_dump())
            return {"server": server}
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=f"Missing field: {exc}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch("/servers/{server_id}")
    async def update_server(
        server_id: int,
        payload: ServerUpdatePayload,
        identity: ApiIdentity = Depends(require_permission("api:write")),
    ):
        data = payload.model_dump(exclude_unset=True)
        server = service.update_server(server_id, data)
        if server is None:
            raise HTTPException(status_code=404, detail=f"Unknown server id: {server_id}")
        return {"server": server}

    @router.delete("/servers/{server_id}")
    async def delete_server(
        server_id: int,
        identity: ApiIdentity = Depends(require_permission("api:write")),
    ):
        if not service.delete_server(server_id):
            raise HTTPException(status_code=404, detail=f"Unknown server id: {server_id}")
        return {"ok": True}

    # ── Statuses / Stats ───────────────────────────────────────────────────────
    @router.get("/statuses")
    async def list_statuses(identity: ApiIdentity = Depends(require_permission("api:read"))):
        return {"statuses": service.get_all_statuses()}

    @router.get("/stats")
    async def stats(identity: ApiIdentity = Depends(require_permission("api:read"))):
        return service.get_stats()

    # ── Validation ─────────────────────────────────────────────────────────────
    @router.post("/validate")
    async def validate(
        payload: ValidatePayload,
        identity: ApiIdentity = Depends(require_permission("api:read")),
    ):
        issues = service.validate_server(
            server_type=payload.server_type,
            profile=payload.profile,
            profile_id=payload.profile_id,
            product_id=payload.product_id,
            environment_id=payload.environment_id,
            os_type=payload.os_type,
            os_family_id=payload.os_family_id,
        )
        return {"issues": issues}

    @router.post("/catalog/evaluate-combo")
    async def evaluate_combo(
        payload: EvaluateComboPayload,
        identity: ApiIdentity = Depends(require_permission("api:read")),
    ):
        """Wrap ProductCatalog.evaluate_combo — the VM vCPU/RAM matrix verdict.

        Returns {status: ok|special|invalid|unknown, message, ram_min, ram_max,
        generation, alt_platform}. The React step-2 tiles call this (debounced)
        instead of re-deriving the matrix client-side.
        """
        return service.catalog.products().evaluate_combo(
            payload.product_id,
            payload.os_family_id,
            payload.vcpu,
            payload.ram_gb,
        )

    # ── Catalog ────────────────────────────────────────────────────────────────
    @router.get("/catalog")
    async def get_catalog(identity: ApiIdentity = Depends(require_permission("api:read"))):
        return _catalog_payload(service)

    @router.post("/catalog/reload")
    async def reload_catalog(identity: ApiIdentity = Depends(require_permission("api:write"))):
        service.reload_catalog()
        return _catalog_payload(service)

    @router.post("/catalog/path")
    async def set_catalog_path(
        payload: CatalogPathPayload,
        identity: ApiIdentity = Depends(require_permission("api:write")),
    ):
        path = (payload.path or "").strip()
        if path:
            if not Path(path).is_dir():
                raise HTTPException(status_code=400, detail=f"Directory not found: {path}")
            service.set_catalog_dir(path)
        else:
            service.set_catalog_dir(str(_DEFAULT_CATALOG_DIR))
        service.reload_catalog()
        return _catalog_payload(service)

    return router
