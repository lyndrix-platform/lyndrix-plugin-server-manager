"""Server Manager — main page UI (moved from entrypoint.py)."""
from __future__ import annotations

from nicegui import ui

from ..controller.service import server_manager_service as svc
from ..controller.configurator import open_configurator
from .overview import render_overview_ui


def render_server_manager_page(ctx) -> None:
    """Render the full /server-manager page body."""
    with ui.column().classes(
        "w-full max-w-[calc(100vw-2.5rem)] 2xl:max-w-[calc(100vw-3rem)] "
        "mx-auto gap-6 px-2"
    ):
        _render_header()
        _render_stats_row()
        ui.separator().classes("border-zinc-700")
        _render_list(ctx)


def _render_header() -> None:
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


def _render_stats_row() -> None:
    if svc.is_ready:
        stats = svc.get_stats()
        with ui.row().classes("w-full gap-4 flex-wrap"):
            _mini_stat("Total Servers", stats["total"], "dns", "blue")
            for env_id, count in sorted(stats.get("by_env", {}).items()):
                env = svc.catalog.environments().get(env_id)
                label = env["label"] if env else env_id
                color = (env or {}).get("color", "grey")
                _mini_stat(label, count, "folder", color)


def _render_list(ctx) -> None:
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


def _mini_stat(label: str, value, icon: str, color: str = "blue") -> None:
    with ui.card().classes(
        f"flex-1 min-w-32 p-4 gap-1 bg-zinc-900 border border-{color}-800"
    ):
        with ui.row().classes("items-center gap-2"):
            ui.icon(icon, size="16px").classes(f"text-{color}-400")
            ui.label(label).classes("text-xs text-zinc-400")
        ui.label(str(value)).classes("text-2xl font-bold text-zinc-100")
