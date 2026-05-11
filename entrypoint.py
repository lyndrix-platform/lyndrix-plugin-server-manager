"""lyndrix-server-manager — Plugin entrypoint.

Architecture
------------
  catalog/hardware.yml     — CPU, RAM, storage, network options + combination rules
  catalog/environments.yml — Environments and IT-provider order workflows

  models.py          — SQLAlchemy model (server_manager_servers table)
  database.py        — DB session helpers + table bootstrap
  catalog.py         — YAML/JSON catalog loader + rule evaluator
  service.py         — CRUD + event emission (singleton: server_manager_service)
  ui_overview.py     — Server list with search / filter
  ui_configurator.py — 3-step guided add/edit dialog
  ui_settings.py     — Plugin settings + catalog/event-bus documentation
  ui_widget.py       — Compact dashboard widget

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
from .app.ui.overview import render_overview_ui
from .app.controller.configurator import open_configurator
from .app.ui.settings import render_settings_ui as _render_settings_ui
from .app.ui.widget import render_dashboard_widget as _render_widget

log = get_logger("Plugin:ServerManager")

# ── Manifest ──────────────────────────────────────────────────────────────────

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
    min_core_version="0.0.5",
    auto_enable_on_install=False,
    repo_url="https://github.com/marvin1309/lyndrix-server-manager",
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


# ── Setup ─────────────────────────────────────────────────────────────────────

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
        with ui.column().classes(
            "w-full max-w-[calc(100vw-2.5rem)] 2xl:max-w-[calc(100vw-3rem)] "
            "mx-auto gap-6 px-2"
        ):
            # Header card
            with ui.card().classes(
                "w-full p-0 overflow-hidden bg-gradient-to-br "
                "from-zinc-950 via-zinc-900 to-slate-950 border border-zinc-800"
            ):
                ui.element("div").classes(
                    "h-1 w-full bg-gradient-to-r from-blue-500 via-cyan-400 to-teal-400"
                )
                with ui.column().classes("w-full p-6 gap-2"):
                    ui.label("Server Manager").classes(
                        "text-3xl font-black text-zinc-50"
                    )
                    ui.label(
                        "Manage your server inventory. "
                        "Edit catalog/hardware.yml to add hardware options. "
                        "Events are emitted on every change for downstream plugins."
                    ).classes("text-sm text-zinc-400")

            # Stats row
            if svc.is_ready:
                stats = svc.get_stats()
                with ui.row().classes("w-full gap-4 flex-wrap"):
                    _mini_stat("Total Servers", stats["total"], "dns", "blue")
                    for env_id, count in sorted(stats.get("by_env", {}).items()):
                        env = svc.catalog.environments().get(env_id)
                        label = env["label"] if env else env_id
                        color = (env or {}).get("color", "grey")
                        _mini_stat(label, count, "folder", color)

            ui.separator().classes("border-zinc-700")

            # Server list
            list_container = ui.column().classes("w-full")
            refresh_fn: list = [None]

            def _open_configurator(server):
                def _after_save():
                    if refresh_fn[0]:
                        refresh_fn[0]()
                open_configurator(server, _after_save)

            with list_container:
                fn = render_overview_ui(ctx, _open_configurator)
                refresh_fn[0] = fn

    log.info("Server Manager: setup complete")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mini_stat(label, value, icon, color="blue"):
    with ui.card().classes(
        f"flex-1 min-w-32 p-4 gap-1 bg-zinc-900 border border-{color}-800"
    ):
        with ui.row().classes("items-center gap-2"):
            ui.icon(icon, size="16px").classes(f"text-{color}-400")
            ui.label(label).classes("text-xs text-zinc-400")
        ui.label(str(value)).classes("text-2xl font-bold text-zinc-100")
