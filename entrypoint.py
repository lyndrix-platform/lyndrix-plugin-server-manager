"""lyndrix-server-manager — Plugin entrypoint.

Architecture
------------
  examples/hardware.yml     — CPU, RAM, storage, network options + combination rules
  examples/environments.yml — Environments and IT-provider order workflows

  app/model/models.py      — SQLAlchemy model (server_manager_servers table)
  app/model/database.py    — DB session helpers + table bootstrap
  app/model/catalog.py     — YAML/JSON catalog loader + rule evaluator
  app/controller/service.py — CRUD + event emission (singleton: server_manager_service)
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
    def main_layout(title):
        def _d(fn):
            return fn
        return _d

from .app.api import build_plugin_router
from .app.controller.service import server_manager_service as svc
from .app.ui.settings import render_settings_ui as _render_settings_ui
from .app.ui.widget import render_dashboard_widget as _render_widget
from .app.ui.page import render_server_manager_page

# ── Manifest ──────────────────────────────────────────────────────────────────

manifest = ModuleManifest(
    id="lyndrix.plugin.server_manager",
    name="Server Manager",
    version="1.0.0",
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
        "subscribe": ["db:connected"],
        "emit": [
            "server_manager:server_created",
            "server_manager:server_updated",
            "server_manager:server_deleted",
            "server_manager:hardware_changed",
            "server_manager:status_changed",
        ],
    },
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
        servers = await asyncio.to_thread(svc.get_all_servers)
    except Exception as exc:  # tables present but the query path is broken
        return PluginHealthStatus(
            status="error",
            details={"reason": "db_query_failed", "error": str(exc)},
            latency_ms=round((time.perf_counter() - start) * 1000, 1),
        )

    latency = round((time.perf_counter() - start) * 1000, 1)
    catalog_loaded = bool(getattr(svc, "catalog", None))
    details = {
        "db_connected": True,
        "ready": True,
        "servers": len(servers),
        "catalog_loaded": catalog_loaded,
    }
    # DB works but the hardware catalog never loaded → CRUD reads work, but the
    # guided add/validate workflows are broken. Usable-but-impaired = degraded.
    if not catalog_loaded:
        return PluginHealthStatus(
            status="degraded",
            details={**details, "reason": "catalog_not_loaded"},
            latency_ms=latency,
        )
    return PluginHealthStatus(status="ok", details=details, latency_ms=latency)


# ── Setup ─────────────────────────────────────────────────────────────────────

def setup(ctx):
    ctx.log.info("Server Manager: setup started")
    svc.set_context(ctx)

    # Single auth'd router, mounted via the registry at
    # /api/plugins/lyndrix.plugin.server_manager/ (registry enforces
    # authentication; routes add api:read / api:write for authorization).
    ctx.register_routes(build_plugin_router(svc))

    # db:connected fires BEFORE setup() is called (it's what triggers plugin activation).
    # Call on_db_connected() immediately if the DB is already ready, then subscribe for
    # future reconnections (e.g. after a transient connection loss).
    if db_instance.is_connected:
        try:
            svc.on_db_connected()
            ctx.log.info("Server Manager: tables bootstrapped (DB was already connected)")
        except Exception as exc:
            ctx.log.error(f"Server Manager: DB init failed — {exc}")

    @ctx.subscribe("db:connected")
    async def _on_db_connected(payload):
        try:
            svc.on_db_connected()
            ctx.log.info("Server Manager: database ready")
        except Exception as exc:
            ctx.log.error(f"Server Manager: DB init failed — {exc}")

    @ui.page("/server-manager")
    @main_layout("Server Manager")
    async def _server_manager_page():
        render_server_manager_page(ctx)

    ctx.log.info("Server Manager: setup complete")


