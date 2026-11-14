"""REST API for Server Manager.

A single router is mounted by core under
``/api/plugins/lyndrix.plugin.server_manager/`` via ``ctx.register_routes()``.
The registry enforces authentication for every route automatically; we
additionally require ``api:read`` on reads and ``api:write`` on the manual CRUD.
The ingest routes (acting as a source system) and the catalog controls need the
custom ``server_manager:ingest`` / ``server_manager:admin`` permissions, because
the core hands ``api:write`` to every ordinary user.

The React CRUD UI and the NiceGUI configurator both call the same
``ServerManagerService`` singleton — this router is the HTTP surface used by the
React bundle. ``ServerRecord.to_dict()`` is the JSON shape returned for servers;
``hardware_profile`` is passed through verbatim because its keys vary by
``server_type`` (physical sockets vs. vCPU, etc.).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam
from pydantic import BaseModel, ConfigDict, Field

from core.api import ApiIdentity, require_permission

from .controller.service import ServerManagerService, ServerReadOnlyError
from .model.catalog import _DEFAULT_CATALOG_DIR

# Plugin-scoped permission ids. Gating routes on the fully-qualified
# plugin:<id>:api:* (rather than bare api:read/api:write) makes the manifest's
# scoped viewer/operator roles actually unlock routes, while global api:read/
# api:write holders keep working via the core's one-directional fallback.
PLUGIN_ID = "lyndrix.plugin.server_manager"
PERM_READ = f"plugin:{PLUGIN_ID}:api:read"
PERM_WRITE = f"plugin:{PLUGIN_ID}:api:write"
# The core grants every plugin's api:read to INT_VIEWER and api:write to
# INT_USER with no opt-out, so "api:write" means "every ordinary user".
# Operator-level actions are therefore gated by custom permissions (declared in
# the manifest, mapped to INT_ADMIN) whose names do not end in :api:read/:api:write
# and are thus matched exactly — never satisfied by a baseline or global grant.
#   ingest — push/remove servers as a source system (REST ingest routes)
#   admin  — re-home / reload the cluster-wide catalog, ingest as ANY source
PERM_INGEST = f"plugin:{PLUGIN_ID}:server_manager:ingest"
PERM_ADMIN = f"plugin:{PLUGIN_ID}:server_manager:admin"

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
    ci_id: Optional[str] = Field(default=None, min_length=36, max_length=36)


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
    ci_id: Optional[str] = Field(default=None, min_length=36, max_length=36)


class ExternalServerBody(ServerUpdatePayload):
    """Server fields a source system may push (key comes from elsewhere).

    ``writeback`` tells the Server Manager whether the source can persist
    edits made here (True → editable, changes go out as
    server_manager:writeback_requested; False/unset on create → read-only).
    """

    writeback: Optional[bool] = None


class ExternalServerPayload(ExternalServerBody):
    """A server pushed in by another system (IaC SSoT, CMDB, ...).

    Upsert semantics keyed by (source, external_id): name/environment_id/
    server_type are required only when the server does not exist yet; on
    later pushes only the supplied fields are applied.
    """

    source: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=255)


class ImportPayload(BaseModel):
    servers: List[ExternalServerPayload] = Field(max_length=1000)


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
    is_default = Path(current_dir).resolve() == _DEFAULT_CATALOG_DIR.resolve()

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
        # True while the configured directory holds no catalog files and the
        # bundled examples/ catalog is served instead (additive field).
        "bundled_fallback": cat.using_bundled_fallback,
        "effective_dir": str(cat.effective_dir),
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


# ── Ingest ownership ───────────────────────────────────────────────────────────

def source_denied_reason(identity: ApiIdentity, source: str) -> Optional[str]:
    """Why ``identity`` may not act as ingest ``source`` — ``None`` when it may.

    A record's ``source`` is its owner: only the owner may update or delete it
    and decide about ``writeback`` (read-only vs. editable here). On the bus
    the emitting plugin states its source; over REST the source is derived from
    the caller instead of being freely choosable: a holder of the ingest
    permission can only act as the source named like its own account (a sync
    job runs under a service account / API key of that name). The master
    system key and holders of the admin permission may act for any source
    (migrations, cleaning up after a retired source).
    """
    if getattr(identity, "is_system", False) or identity.allows(PERM_ADMIN):
        return None
    own = (getattr(identity, "username", "") or "").strip()
    if own and own == (source or "").strip():
        return None
    return (
        f"'{own or 'this account'}' may only ingest as source '{own}' — records of "
        f"source '{source}' belong to that source (acting for another source "
        f"requires {PERM_ADMIN})"
    )


def _require_source(identity: ApiIdentity, source: str) -> None:
    reason = source_denied_reason(identity, source)
    if reason:
        raise HTTPException(status_code=403, detail=reason)


# ── Router ──────────────────────────────────────────────────────────────────────

def build_plugin_router(service: ServerManagerService) -> APIRouter:
    """The single Server Manager router — core mounts it at /api/plugins/<id>/.

    Every handler performs blocking work (synchronous SQLAlchemy sessions and
    YAML catalog file reads) off the event loop via ``asyncio.to_thread`` so the
    shared NiceGUI/FastAPI loop is never stalled under concurrent load.
    """
    router = APIRouter(tags=["Server Manager"])

    def _ensure_ready() -> None:
        """Short-circuit DB-backed routes to a retryable 503 during boot/reconnect."""
        if not service.is_ready:
            raise HTTPException(
                status_code=503,
                detail="Server Manager database not ready — retry shortly.",
            )

    # ── Servers (CRUD) ─────────────────────────────────────────────────────────
    @router.get("/servers")
    async def list_servers(
        in_maintenance: Optional[bool] = None,
        identity: ApiIdentity = Depends(require_permission(PERM_READ)),
    ):
        """Every server carries ``maintenance`` (read-model for records with a
        ``ci_id``, ``null`` otherwise); ``?in_maintenance=true`` keeps only the
        servers whose maintenance state is active right now."""
        _ensure_ready()
        servers = await asyncio.to_thread(
            service.get_all_servers, in_maintenance=in_maintenance
        )
        return {"servers": servers}

    @router.get("/servers/{server_id}")
    async def get_server(
        server_id: int,
        identity: ApiIdentity = Depends(require_permission(PERM_READ)),
    ):
        _ensure_ready()
        server = await asyncio.to_thread(service.get_server, server_id)
        if server is None:
            raise HTTPException(status_code=404, detail=f"Unknown server id: {server_id}")
        return {"server": server}

    @router.post("/servers")
    async def create_server(
        payload: ServerCreatePayload,
        identity: ApiIdentity = Depends(require_permission(PERM_WRITE)),
    ):
        _ensure_ready()
        try:
            server = await asyncio.to_thread(service.create_server, payload.model_dump())
            return {"server": server}
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=f"Missing field: {exc}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch("/servers/{server_id}")
    async def update_server(
        server_id: int,
        payload: ServerUpdatePayload,
        identity: ApiIdentity = Depends(require_permission(PERM_WRITE)),
    ):
        _ensure_ready()
        data = payload.model_dump(exclude_unset=True)
        try:
            server = await asyncio.to_thread(service.update_server, server_id, data)
        except ServerReadOnlyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if server is None:
            raise HTTPException(status_code=404, detail=f"Unknown server id: {server_id}")
        return {"server": server}

    @router.delete("/servers/{server_id}")
    async def delete_server(
        server_id: int,
        identity: ApiIdentity = Depends(require_permission(PERM_WRITE)),
    ):
        _ensure_ready()
        try:
            deleted = await asyncio.to_thread(service.delete_server, server_id)
        except ServerReadOnlyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Unknown server id: {server_id}")
        return {"ok": True}

    # ── Ingest (external systems) ──────────────────────────────────────────────
    # Idempotent push API for other systems; keyed by (source, external_id) so
    # a sync job can re-send its whole inventory every run without duplicates.
    # Gated by the custom ingest permission (not api:write — see PERM_INGEST)
    # and bound to the caller's own source (source_denied_reason): an ordinary
    # user can neither push as "iac-orchestrator" to make its read-only records
    # editable, nor re-point their ci_id, nor delete them.
    async def _upsert(data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            server, created = await asyncio.to_thread(service.upsert_external_server, data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"server": server, "created": created}

    @router.put("/servers/external/{source}/{external_id}")
    async def upsert_external_server(
        source: str,
        external_id: str,
        payload: ExternalServerBody,  # source/external_id come from the path
        identity: ApiIdentity = Depends(require_permission(PERM_INGEST)),
    ):
        _require_source(identity, source)
        _ensure_ready()
        data = payload.model_dump(exclude_unset=True)
        return await _upsert({**data, "source": source, "external_id": external_id})

    @router.delete("/servers/external/{source}/{external_id}")
    async def delete_external_server(
        source: str,
        external_id: str,
        identity: ApiIdentity = Depends(require_permission(PERM_INGEST)),
    ):
        _require_source(identity, source)
        _ensure_ready()
        if not await asyncio.to_thread(service.remove_external_server, source, external_id):
            raise HTTPException(
                status_code=404, detail=f"Unknown external server: {source}/{external_id}"
            )
        return {"ok": True}

    @router.post("/servers/import")
    async def import_servers(
        payload: ImportPayload,
        identity: ApiIdentity = Depends(require_permission(PERM_INGEST)),
    ):
        """Batch upsert. Each item is applied independently; failures are
        reported per item instead of aborting the whole import — including
        items whose ``source`` the caller may not act for."""
        _ensure_ready()
        results: List[Dict[str, Any]] = []
        for item in payload.servers:
            data = item.model_dump(exclude_unset=True)
            key = {"source": item.source, "external_id": item.external_id}
            denied = source_denied_reason(identity, item.source)
            if denied:
                results.append({**key, "ok": False, "error": denied})
                continue
            try:
                server, created = await asyncio.to_thread(service.upsert_external_server, data)
                results.append({**key, "ok": True, "created": created, "id": server["id"]})
            except (ValueError, LookupError) as exc:
                results.append({**key, "ok": False, "error": str(exc)})
        return {
            "results": results,
            "created": sum(1 for r in results if r.get("created")),
            "updated": sum(1 for r in results if r["ok"] and not r.get("created")),
            "failed": sum(1 for r in results if not r["ok"]),
        }

    # ── Maintenance windows (core API >= 1.13, read-model) ─────────────────────
    @router.get("/maintenance/{ci_id}")
    async def get_maintenance(
        ci_id: str = PathParam(min_length=1, max_length=36),
        identity: ApiIdentity = Depends(require_permission(PERM_READ)),
    ):
        """Full maintenance read-model of one configuration item: effective state
        (``active``, ``until``, ``source``, ``next_start``, ``note``), the window
        sets per emitting source and the live overrides, plus ``server_ids`` of
        the records carrying this ``ci_id``. A CI nobody published anything for
        is simply "not in maintenance" (200, empty lists)."""
        _ensure_ready()
        return await asyncio.to_thread(service.get_maintenance, ci_id)

    # ── Statuses / Stats ───────────────────────────────────────────────────────
    @router.get("/statuses")
    async def list_statuses(identity: ApiIdentity = Depends(require_permission(PERM_READ))):
        return {"statuses": await asyncio.to_thread(service.get_all_statuses)}

    @router.get("/stats")
    async def stats(identity: ApiIdentity = Depends(require_permission(PERM_READ))):
        _ensure_ready()
        return await asyncio.to_thread(service.get_stats)

    # ── Validation ─────────────────────────────────────────────────────────────
    @router.post("/validate")
    async def validate(
        payload: ValidatePayload,
        identity: ApiIdentity = Depends(require_permission(PERM_READ)),
    ):
        issues = await asyncio.to_thread(
            service.validate_server,
            payload.server_type,
            payload.profile,
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
        identity: ApiIdentity = Depends(require_permission(PERM_READ)),
    ):
        """Wrap ProductCatalog.evaluate_combo — the VM vCPU/RAM matrix verdict.

        Returns {status: ok|special|invalid|unknown, message, ram_min, ram_max,
        generation, alt_platform}. The React step-2 tiles call this (debounced)
        instead of re-deriving the matrix client-side.
        """
        def _run() -> dict:
            return service.catalog.products().evaluate_combo(
                payload.product_id,
                payload.os_family_id,
                payload.vcpu,
                payload.ram_gb,
            )

        return await asyncio.to_thread(_run)

    # ── Catalog ────────────────────────────────────────────────────────────────
    # ``can_manage`` tells the UI whether to offer the catalog controls; the
    # routes below enforce it — the flag is a hint, not the gate.
    @router.get("/catalog")
    async def get_catalog(identity: ApiIdentity = Depends(require_permission(PERM_READ))):
        payload = await asyncio.to_thread(_catalog_payload, service)
        return {**payload, "can_manage": identity.allows(PERM_ADMIN)}

    # Reload and re-homing change the catalog of EVERY instance and user
    # (persisted in Vault, broadcast cluster-wide) → admin permission.
    @router.post("/catalog/reload")
    async def reload_catalog(identity: ApiIdentity = Depends(require_permission(PERM_ADMIN))):
        def _run() -> dict:
            service.reload_catalog()
            return {**_catalog_payload(service), "can_manage": True}

        return await asyncio.to_thread(_run)

    @router.post("/catalog/path")
    async def set_catalog_path(
        payload: CatalogPathPayload,
        identity: ApiIdentity = Depends(require_permission(PERM_ADMIN)),
    ):
        # Validated, persisted in Vault and announced to every instance
        # (server_manager:catalog_changed, cluster=True) by the service.
        def _run() -> dict:
            service.apply_catalog_dir(payload.path)
            return {**_catalog_payload(service), "can_manage": True}

        try:
            return await asyncio.to_thread(_run)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
