"""lyndrix-server-manager — Plugin entrypoint.

Architecture
------------
  examples/hardware.yml     — CPU, RAM, storage, network options + combination rules
  examples/environments.yml — Environments and IT-provider order workflows

  app/model/models.py      — SQLAlchemy models (server_manager_servers,
                             server_manager_maintenance)
  app/model/database.py    — DB session helpers + table bootstrap
  app/model/catalog.py     — YAML/JSON catalog loader + rule evaluator
  app/controller/service.py — CRUD + event emission (singleton: server_manager_service)
  app/controller/maintenance.py — maintenance-window read-model (core API >= 1.13)
  app/ui/overview.py        — Server list with search / filter
  app/controller/configurator/ — 3-step guided add/edit dialog
  app/ui/settings.py        — Plugin settings + catalog/event-bus documentation
  app/ui/widget.py          — Compact dashboard widget
  app/ui/page.py            — Main /server-manager page UI

Events emitted (subscribe from any other plugin)
-------------------------------------------------
  server_manager:server_created   — {server dict}
  server_manager:server_updated   — {server dict, changes: {field: {old, new}}}
  server_manager:server_deleted   — {last known server dict}
  server_manager:hardware_changed — {server_id, server_name, action,
                                     old_profile, new_profile,
                                     environment_id, server_type}
  server_manager:status_changed   — {server_id, server_name, old_status, new_status}
  server_manager:ingest_failed    — {topic, source, external_id, error}
  server_manager:writeback_requested — {action: update|delete, source, external_id,
                                        server, changes}; only for servers whose
                                        source pushed writeback=True. Local bus
                                        only: in cluster mode a source plugin on
                                        another instance does not see it.
  server_manager:catalog_changed  — {catalog_dir, action}; cluster-bridged, peers
                                    re-read the persisted catalog path and reload

Events consumed (push servers in from any other plugin)
-------------------------------------------------------
  server_manager:register_server   — {source, external_id, name, environment_id,
                                      server_type, ...any server field}
                                     idempotent upsert keyed by (source, external_id)
  server_manager:deregister_server — {source, external_id}
  server_manager:writeback_failed  — {source, external_id, error}; the source
                                     could not persist a writeback_requested edit
  Pushes carry writeback: bool — False (default) makes the server read-only
  here, True lets users edit it and routes the edit back to the source.
  The emitting plugin must list these topics in its own permissions.emit.
  REST equivalent: PUT/DELETE /servers/external/{source}/{external_id},
  POST /servers/import (batch) — these need the custom permission
  server_manager:ingest and the source must be named like the calling account
  (any source with server_manager:admin / the system key).

Maintenance windows consumed (core API >= 1.13 contract, read-model only)
-------------------------------------------------------------------------
  maintenance:windows_published    — {source, published_at, replaces, items[]};
                                     replaces=true drops the source's earlier rows
  maintenance:override_started     — {override_id, ci_ids, until, reason, source,
                                     started_at, linger_minutes}
  maintenance:override_ended       — {override_id, source, ended_at, linger_minutes}
  Stored per ci_id in server_manager_maintenance (also for CIs without a server
  record) and evaluated when the list is read: GET /servers carries
  `maintenance` per record with a ci_id, GET /maintenance/{ci_id} the full
  read-model. The core library core.api.maintenance is optional — without it
  `active` is null. This plugin emits no maintenance events.
"""
import asyncio
import time

from nicegui import ui

from core.api import ModuleManifest, PluginHealthStatus, db_instance

# TODO(agent): `main_layout` lives in core's internal `ui.layout`, not in the
# stable `core.api` surface — re-export it from core.api (or drop this NiceGUI
# page in favour of the React UI). The fallback below keeps the page working if
# the internal import ever disappears.
try:
    from ui.layout import main_layout
except ImportError:
    def main_layout(title, **_kwargs):
        def _d(fn):
            return fn
        return _d

from .app.api import PERM_WRITE, ExternalServerPayload, build_plugin_router
from .app.controller.service import server_manager_service as svc
from .app.ui.settings import render_settings_ui as _render_settings_ui
from .app.ui.widget import render_dashboard_widget as _render_widget
from .app.ui.page import render_server_manager_page

# ── Manifest ──────────────────────────────────────────────────────────────────

manifest = ModuleManifest(
    id="lyndrix.plugin.server_manager",
    name="Server Manager",
    version="1.4.0",
    description=(
        "Central server inventory with configurable hardware catalogs, "
        "combination rules, and event-bus hooks for downstream order workflows."
    ),
    author="Lyndrix",
    icon="dns",
    type="PLUGIN",
    min_core_version="0.0.6",
    auto_enable_on_install=False,
    repo_url="https://github.com/lyndrix-platform/lyndrix-plugin-server-manager",
    ui_route="/server-manager",
    react_ui=True,
    # i18next-shaped namespace served to the React UI; core auto-registers
    # locales/server_manager.<locale>.json and adds "server_manager" to the
    # client allowlist.
    i18n_namespace="server_manager",
    react_routes=[
        {
            "path": "/server-manager",
            "label": "Server Manager",
            "icon": "dns",
            "sidebar_visible": True,
        },
        {
            "path": "/server-manager/settings",
            "label": "Server Manager Einstellungen",
            "icon": "settings",
            "sidebar_visible": False,
        },
    ],
    settings_ui_route="/server-manager/settings",
    permissions={
        "subscribe": [
            "db:connected",
            "server_manager:register_server",
            "server_manager:deregister_server",
            "server_manager:writeback_failed",
            # Cluster: peers announce catalog path changes / reloads.
            "server_manager:catalog_changed",
            # Core >= 1.5.1: the operator saved this plugin's settings.
            "plugin:settings_changed",
            # Core API >= 1.13: maintenance-window contract (read-model for the
            # badge). Emitted locally by the orchestrator / monitoring; every
            # instance persists idempotently into the shared table.
            "maintenance:windows_published",
            "maintenance:override_started",
            "maintenance:override_ended",
        ],
        "emit": [
            "server_manager:server_created",
            "server_manager:server_updated",
            "server_manager:server_deleted",
            "server_manager:hardware_changed",
            "server_manager:status_changed",
            "server_manager:ingest_failed",
            "server_manager:writeback_requested",
            "server_manager:catalog_changed",
        ],
    },
    # Identity & Permissions 2.0: roles use fully-qualified per-plugin
    # permissions (plugin:<id>:api:*) rather than the bare api:read/write,
    # so granting a role here can never leak into a global cross-plugin grant.
    # Operator-level actions. The core's baseline grants hand api:write to every
    # INT_USER, so these are separate permissions that only the admin role (and
    # roles an operator assigns deliberately) carry.
    custom_permissions=[
        {"id": "server_manager:admin", "label": "Server Manager Administration",
         "description": "Re-home and reload the catalog; ingest servers on behalf of any source",
         "icon": "admin_panel_settings"},
        {"id": "server_manager:ingest", "label": "Server Manager Ingest",
         "description": "Push servers in over the REST ingest routes as the source "
                        "named like the calling account",
         "icon": "input"},
    ],
    roles=[
        {"id": "admin", "label": "Server Manager Administrator",
         "permissions": ["plugin:lyndrix.plugin.server_manager:api:read",
                          "plugin:lyndrix.plugin.server_manager:api:write",
                          "plugin:lyndrix.plugin.server_manager:server_manager:admin",
                          "plugin:lyndrix.plugin.server_manager:server_manager:ingest"],
         "auto_map_groups": ["INT_ADMIN"],
         "description": "Full control over server inventory and catalog configuration."},
        {"id": "source", "label": "Server Manager Source System",
         "permissions": ["plugin:lyndrix.plugin.server_manager:api:read",
                          "plugin:lyndrix.plugin.server_manager:server_manager:ingest"],
         "auto_map_groups": [],
         "description": "Service account of a system that pushes its servers in "
                        "(REST ingest as the source named like the account)."},
        {"id": "operator", "label": "Server Manager Operator",
         "permissions": ["plugin:lyndrix.plugin.server_manager:api:read",
                          "plugin:lyndrix.plugin.server_manager:api:write"],
         "auto_map_groups": [],
         "description": "Manage servers, no administrative extras."},
        {"id": "viewer", "label": "Server Manager Viewer",
         "permissions": ["plugin:lyndrix.plugin.server_manager:api:read"],
         "auto_map_groups": [],
         "description": "Read-only access to the server inventory."},
    ],
)

# ── Public plugin API (called by lyndrix-core) ────────────────────────────────

def render_settings_ui(ctx):
    _render_settings_ui(ctx)


def render_dashboard_widget(ctx):
    _render_widget(ctx)


# ── Health ────────────────────────────────────────────────────────────────────

async def health(ctx) -> PluginHealthStatus:
    """Functional health probe.

    This plugin is a DB-backed inventory, so "healthy" means the persistence
    layer is genuinely usable — not merely that ``setup()`` ran. We confirm the
    DB is connected, that the tables were bootstrapped (``svc.is_ready``), and
    that a real query against ``server_manager_servers`` succeeds. The catalog
    (YAML hardware/rules) must also be loaded for add/validate flows to work.
    The sync ORM call is offloaded so the probe never blocks the event loop.
    """
    start = time.perf_counter()

    if not db_instance.is_connected:
        return PluginHealthStatus(
            status="error",
            details={"reason": "db_unavailable", "db_connected": False},
        )

    if not svc.is_ready:
        return PluginHealthStatus(
            status="error",
            details={"reason": "tables_not_initialized", "db_connected": True, "ready": False},
        )

    try:
        servers = await asyncio.to_thread(svc.count_servers)
    except Exception as exc:  # tables present but the query path is broken
        return PluginHealthStatus(
            status="error",
            details={"reason": "db_query_failed", "error": str(exc)},
            latency_ms=round((time.perf_counter() - start) * 1000, 1),
        )

    latency = round((time.perf_counter() - start) * 1000, 1)
    try:
        catalog = await asyncio.to_thread(svc.catalog_status)
    except Exception as exc:  # unreadable catalog must not turn the probe into an error
        catalog = {"loaded": False, "dir": None, "bundled_fallback": False, "error": str(exc)}
    details = {
        "db_connected": True,
        "ready": True,
        "servers": servers,
        "catalog_loaded": catalog["loaded"],
        "catalog_dir": catalog["dir"],
        "catalog_bundled_fallback": catalog["bundled_fallback"],
    }
    # DB works but no usable hardware catalog → CRUD reads work, but the guided
    # add/validate workflows are broken. Usable-but-impaired = degraded.
    if not catalog["loaded"]:
        return PluginHealthStatus(
            status="degraded",
            details={
                **details,
                "reason": "catalog_not_loaded",
                "message": (
                    "No usable catalog (server types / environments) in "
                    f"{catalog['dir'] or 'the catalog directory'} — add the catalog "
                    "files or reset the catalog path in the plugin settings."
                ),
                **({"error": catalog["error"]} if catalog.get("error") else {}),
            },
            latency_ms=latency,
        )
    return PluginHealthStatus(status="ok", details=details, latency_ms=latency)


# ── Setup ─────────────────────────────────────────────────────────────────────

def _spawn(ctx, coro) -> None:
    """Start background work via ctx.create_task (older cores: the running loop)."""
    create_task = getattr(ctx, "create_task", None)
    if callable(create_task):
        create_task(coro, name="server_manager:bootstrap")
    else:
        asyncio.get_running_loop().create_task(coro)


def setup(ctx):
    ctx.log.info("Server Manager: setup started")
    svc.set_context(ctx)

    # Single auth'd router, mounted via the registry at
    # /api/plugins/lyndrix.plugin.server_manager/ (registry enforces
    # authentication; routes add api:read / api:write for authorization).
    ctx.register_routes(build_plugin_router(svc))

    # db:connected fires BEFORE setup() is called (it's what triggers plugin
    # activation). Bootstrap right away in the background if the DB is already
    # ready (setup() must not block; the DDL runs in a thread under the core's
    # bootstrap lock), then re-run on every reconnection. The persisted catalog
    # path is applied in the same background task.
    async def _initial_bootstrap():
        if db_instance.is_connected and await svc.bootstrap():
            ctx.log.info("Server Manager: tables bootstrapped (DB was already connected)")
        await asyncio.to_thread(svc.load_catalog_setting)

    _spawn(ctx, _initial_bootstrap())

    @ctx.subscribe("db:connected")
    async def _on_db_connected(payload):
        if await svc.bootstrap():
            ctx.log.info("Server Manager: database ready")

    # Cluster: a peer changed the catalog path or reloaded the catalog.
    @ctx.subscribe("server_manager:catalog_changed")
    async def _on_catalog_changed(payload):
        if isinstance(payload, dict) and payload.get("_origin"):
            await asyncio.to_thread(svc.load_catalog_setting)

    # Core >= 1.5.1 (bridged): the operator saved this plugin's settings.
    @ctx.subscribe("plugin:settings_changed")
    async def _on_settings_changed(payload):
        if isinstance(payload, dict) and payload.get("plugin_id") == manifest.id:
            await asyncio.to_thread(svc.load_catalog_setting)

    # Inbound servers from other plugins. Payloads are untrusted input; any
    # failure is reported back on server_manager:ingest_failed because the
    # bus gives the sender no return value.
    def _ingest_failed(topic: str, payload, error: str) -> None:
        data = payload if isinstance(payload, dict) else {}
        ctx.log.warning(f"Server Manager: {topic} rejected — {error}")
        ctx.emit("server_manager:ingest_failed", {
            "topic": topic,
            "source": data.get("source"),
            "external_id": data.get("external_id"),
            "error": error,
        })

    @ctx.subscribe("server_manager:register_server")
    async def _on_register_server(payload):
        topic = "server_manager:register_server"
        if not isinstance(payload, dict):
            _ingest_failed(topic, payload, "payload must be an object")
            return
        # The schema bootstrap runs in the background after setup(); give an
        # early push a moment instead of rejecting it outright.
        if not await svc.wait_ready():
            _ingest_failed(topic, payload, "database not ready")
            return
        try:
            data = ExternalServerPayload.model_validate(payload).model_dump(exclude_unset=True)
            await asyncio.to_thread(svc.upsert_external_server, data)
        except Exception as exc:
            _ingest_failed(topic, payload, str(exc))

    @ctx.subscribe("server_manager:deregister_server")
    async def _on_deregister_server(payload):
        topic = "server_manager:deregister_server"
        data = payload if isinstance(payload, dict) else {}
        source, external_id = data.get("source"), data.get("external_id")
        if not source or not external_id:
            _ingest_failed(topic, payload, "source and external_id are required")
            return
        # The schema bootstrap runs in the background after setup(); give an
        # early push a moment instead of rejecting it outright.
        if not await svc.wait_ready():
            _ingest_failed(topic, payload, "database not ready")
            return
        try:
            if not await asyncio.to_thread(
                svc.remove_external_server, str(source), str(external_id)
            ):
                _ingest_failed(topic, payload, "unknown server")
        except Exception as exc:
            _ingest_failed(topic, payload, str(exc))

    @ctx.subscribe("server_manager:writeback_failed")
    async def _on_writeback_failed(payload):
        data = payload if isinstance(payload, dict) else {}
        source, external_id = data.get("source"), data.get("external_id")
        if not source or not external_id or not await svc.wait_ready():
            ctx.log.warning(f"Server Manager: ignoring malformed writeback_failed {data!r}")
            return
        try:
            await asyncio.to_thread(
                svc.mark_writeback_failed, str(source), str(external_id), str(data.get("error") or "")
            )
        except Exception as exc:
            ctx.log.error(f"Server Manager: could not record write-back failure — {exc}")

    # Maintenance windows (core API >= 1.13): read-model for the badge. Handlers
    # run on every cluster instance and write idempotently into the shared
    # table, so payload["_origin"] is deliberately NOT filtered. Failures are
    # only logged — ingest_failed is shaped for (source, external_id) pushes.
    async def _apply_maintenance(topic: str, payload, apply) -> None:
        if not isinstance(payload, dict):
            ctx.log.warning(f"Server Manager: {topic} ignored — payload must be an object")
            return
        if not await svc.wait_ready():
            ctx.log.warning(f"Server Manager: {topic} dropped — database not ready")
            return
        try:
            await asyncio.to_thread(apply, payload)
        except Exception as exc:
            ctx.log.warning(f"Server Manager: {topic} rejected — {exc}")

    @ctx.subscribe("maintenance:windows_published")
    async def _on_windows_published(payload):
        await _apply_maintenance(
            "maintenance:windows_published", payload, svc.apply_windows_published
        )

    @ctx.subscribe("maintenance:override_started")
    async def _on_override_started(payload):
        await _apply_maintenance(
            "maintenance:override_started", payload, svc.apply_override_started
        )

    @ctx.subscribe("maintenance:override_ended")
    async def _on_override_ended(payload):
        await _apply_maintenance(
            "maintenance:override_ended", payload, svc.apply_override_ended
        )

    # SM-02: this page lists AND edits/deletes servers and the UI code has no
    # permission checks of its own (core.api offers plugins no session
    # permission helper), so the page needs api:write — the lowest permission
    # that may use every action on it. Read-only users use the React UI.
    # TODO(agent): gate per action and open the page read-only to viewers once
    # core.api exposes a session permission check (CORE).
    try:
        page_layout = main_layout("Server Manager", permission=PERM_WRITE)
    except TypeError:  # older core: main_layout has no ``permission`` argument
        page_layout = main_layout("Server Manager")

    @ui.page("/server-manager")
    @page_layout
    async def _server_manager_page():
        render_server_manager_page(ctx)

    ctx.log.info("Server Manager: setup complete")


