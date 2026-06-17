"""Server Manager — compact dashboard widget."""
from __future__ import annotations

from nicegui import ui

from core.api import UIStyles

from ..controller.service import server_manager_service as svc

_STATUS_STYLE = {
    "active":        UIStyles.STATUS_TEXT_SUCCESS,
    "ordered":       UIStyles.STATUS_TEXT_WARNING,
    "provisioning":  "text-xs text-cyan-400",
    "decommissioned": UIStyles.STATUS_TEXT_NEUTRAL,
}


def render_dashboard_widget(ctx):
    with ui.card().classes(UIStyles.CARD_BASE + " w-full p-4 gap-3"):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("dns", size="20px").classes(UIStyles.ICON_PRIMARY)
                ui.label("Server Manager").classes(UIStyles.TITLE_H3)
            ui.button(
                icon="open_in_new",
                on_click=lambda: ui.navigate.to("/server-manager"),
            ).props("flat round dense size=xs color=blue-grey")

        stats_label = ui.label("Loading…").classes(UIStyles.TEXT_MUTED)
        status_row = ui.row().classes("w-full gap-2 flex-wrap")

        def refresh():
            if not svc.is_ready:
                stats_label.set_text("DB not ready")
                return
            try:
                stats = svc.get_stats()
                total = stats["total"]
                stats_label.set_text(f"{total} server{'s' if total != 1 else ''} registered")
                status_row.clear()
                with status_row:
                    for status, count in sorted(stats.get("by_status", {}).items()):
                        cls = _STATUS_STYLE.get(status, UIStyles.STATUS_TEXT_NEUTRAL)
                        with ui.row().classes(f"items-center gap-1 {cls}"):
                            ui.label(f"{count}").classes("text-xs font-bold font-mono")
                            ui.label(status.capitalize()).classes("text-xs")
            except Exception:
                stats_label.set_text("Unavailable")

        refresh()
        ui.timer(30, refresh)
