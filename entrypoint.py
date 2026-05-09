from threading import Lock

from nicegui import ui
from ui.layout import main_layout
from core.api import ModuleManifest

manifest = ModuleManifest(
    id="lyndrix.plugin.server_manager",
    name="Server Manager",
    version="0.1.0",
    description="Grundbasis für zentrale Server-Verwaltung in Lyndrix.",
    author="Lyndrix",
    icon="dns",
    type="PLUGIN",
    min_core_version="0.0.1",
    auto_enable_on_install=False,
    repo_url="https://github.com/marvin1309/lyndrix-server-manager",
    ui_route="/server-manager",
    permissions={
        "subscribe": ["system:boot_complete", "vault:ready_for_data"],
        "emit": ["server_manager:status", "user:notify"],
    },
)

plugin_state = {
    "status": "idle",
    "last_action": "not_started",
}
state_lock = Lock()


def _set_state(**updates):
    with state_lock:
        plugin_state.update(updates)


def _get_state(key):
    with state_lock:
        return plugin_state.get(key, "")


def render_settings_ui(ctx):
    with ui.column().classes("gap-3 w-full"):
        ui.label("Server Manager Einstellungen").classes("text-base font-semibold")
        ui.label("Die Grundkonfiguration ist aktiv.").classes("text-sm text-slate-400")


def render_dashboard_widget(ctx):
    with ui.column().classes("gap-2 w-full"):
        ui.label("Server Manager").classes("text-base font-bold")
        with ui.row().classes("w-full justify-between"):
            ui.label("Status").classes("text-xs text-slate-400")
            status_label = ui.label().classes("text-xs font-mono")
        with ui.row().classes("w-full justify-between"):
            ui.label("Last Action").classes("text-xs text-slate-400")
            last_action_label = ui.label().classes("text-xs font-mono")

        def refresh_widget():
            status_label.set_text(_get_state("status"))
            last_action_label.set_text(_get_state("last_action"))

        refresh_widget()
        ui.timer(1.0, refresh_widget)


def setup(ctx):
    ctx.log.info("Server Manager: setup started")

    @ctx.subscribe("system:boot_complete")
    async def _on_boot_complete(payload):
        _set_state(status="ready", last_action="boot_complete")
        ctx.emit("server_manager:status", {"status": "ready"})

    @ui.page("/server-manager")
    @main_layout("Server Manager")
    async def server_manager_page():
        with ui.column().classes("gap-4"):
            ui.label("Server Manager").classes("text-2xl font-bold")
            ui.label("Plugin-Basis ist initialisiert.").classes("text-sm text-slate-400")

            def mark_checked():
                _set_state(status="healthy", last_action="manual_health_check")
                ctx.emit("server_manager:status", {"status": "healthy"})
                ui.notify("Server-Status auf healthy gesetzt", type="positive")

            ui.button("Status prüfen", on_click=mark_checked).props("icon=check_circle")

    ctx.log.info("Server Manager: setup complete")
