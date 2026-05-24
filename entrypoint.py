"""lyndrix-server-manager — Plugin entrypoint.

Architecture
------------
  catalog/hardware.yml     — CPU, RAM, storage, network options + combination rules
  catalog/environments.yml — Environments and IT-provider order workflows

  app/model/         — SQLAlchemy model, DB session helpers, catalog loader
  app/controller/    — CRUD, event emission, configurator logic
  app/ui/            — NiceGUI pages, widgets, dialogs

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
from nicegui import ui

from core.api import ModuleManifest
from core.logger import get_logger

try:
    from ui.layout import main_layout
except ImportError:
    def main_layout(title):
        def _d(fn):
            return fn
        return _d

from .app.controller.service import server_manager_service as svc
from .app.controller.configurator import open_configurator
from .app.ui.overview import render_page
from .app.ui.settings import render_settings_ui as _render_settings_ui
from .app.ui.widget import render_dashboard_widget as _render_widget

log = get_logger("Plugin:ServerManager")

# ── Manifest ───────────────────────────────────────────────────────────

manifest = ModuleManifest(
    id="lyndrix.plugin.server_manager",
    name="Server Manager",
    version="0.0.2",
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


# ── Setup ────────────────────────────────────────────────────────────

def setup(ctx):
    log.info("Server Manager: setup started")
    svc.set_context(ctx)

    # db:connected fires BEFORE setup() is called (it's what triggers plugin activation).
    # Call on_db_connected() immediately if the DB is already ready, then subscribe for
    # future reconnections (e.g. after a transient connection loss).
    from core.api import db_instance

    if db_instance.is_connected:
        try:
            svc.on_db_connected()
            log.info("Server Manager: tables bootstrapped (DB was already connected)")
        except Exception as exc:
            log.error(f"Server Manager: DB init failed — {exc}")

    @ctx.subscribe("db:connected")
    async def _on_db_connected(payload):
        try:
            svc.on_db_connected()
            log.info("Server Manager: database ready")
        except Exception as exc:
            log.error(f"Server Manager: DB init failed — {exc}")

    @ui.page("/server-manager")
    @main_layout("Server Manager")
    async def server_manager_page():
        render_page(ctx, svc, open_configurator)

    log.info("Server Manager: setup complete")
