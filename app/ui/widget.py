"""Server Manager — compact dashboard widget."""
from __future__ import annotations

from nicegui import ui

from ..controller.service import server_manager_service as svc

_STATUS_COLOR = {
    "active": "text-emerald-400",
    "ordered": "text-amber-400",
    "provisioning": "text-blue-400",
    "decommissioned": "text-zinc-500",
}


def render_dashboard_widget(ctx):
    with ui.card().classes(
        "w-full p-4 gap-3 bg-zinc-900 border border-zinc-700"
    ):
        with ui.row().classes("w-full items-center justify-between"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("dns", size="20px").classes("text-primary")
                ui.label("Server Manager").classes("text-sm font-bold text-zinc-200")
            ui.button(
                icon="open_in_new",
                on_click=lambda: ui.navigate.to("/server-manager"),
            ).props("flat round dense size=xs color=blue-grey")

        stats_label = ui.label("Loading…").classes("text-xs text-zinc-400")
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
                        color = _STATUS_COLOR.get(status, "text-zinc-400")
                        with ui.row().classes(f"items-center gap-1 {color}"):
                            ui.label(f"{count}").classes("text-xs font-bold font-mono")
                            ui.label(status.capitalize()).classes("text-xs")
            except Exception:
                stats_label.set_text("Unavailable")

        refresh()
        ui.timer(30, refresh)
