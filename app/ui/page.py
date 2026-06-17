"""Server Manager — main page UI."""
from __future__ import annotations

from nicegui import ui

from core.api import UIStyles

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
        ui.separator().classes("border-slate-200 dark:border-white/5")
        _render_list(ctx)


def _render_header() -> None:
    with ui.card().classes(UIStyles.CARD_GLASS + " w-full").style(
        "padding: 0; flex-wrap: nowrap"
    ):
        ui.element("div").classes(UIStyles.GRAD_BAR_ACCENT)
        with ui.column().classes("w-full p-6 gap-2"):
            ui.label("Server Manager").classes(UIStyles.TITLE_H1)
            ui.label(
                "Manage your server inventory. "
                "Edit catalog/hardware.yml to add hardware options. "
                "Events are emitted on every change for downstream plugins."
            ).classes(UIStyles.TEXT_MUTED)


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
    with ui.card().classes(UIStyles.CARD_BASE + " flex-1 min-w-32 p-4 gap-1"):
        with ui.row().classes("items-center gap-2"):
            ui.icon(icon, size="16px").classes(f"text-{color}-400")
            ui.label(label).classes(UIStyles.LABEL_MINI)
        ui.label(str(value)).classes(UIStyles.TITLE_H2)
