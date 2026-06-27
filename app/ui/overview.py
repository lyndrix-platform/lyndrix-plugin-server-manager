"""Server Manager — overview/list UI."""
from __future__ import annotations

from nicegui import ui

from core.api import UIStyles

from ..controller.service import server_manager_service as svc

_TYPE_ICON = {
    "physical": "storage",
    "vm": "cloud",
    "container": "view_in_ar",
}


def render_overview_ui(ctx, open_configurator_fn):  # noqa: C901
    """Render the full server list with search/filter.

    open_configurator_fn(server_dict | None) — called to open the add/edit dialog.
    """
    catalog = svc.catalog
    status_defs = catalog.settings().get_statuses()
    status_labels = {"": "All Statuses"} | {s["id"]: s["label"] for s in status_defs}
    status_colors = {s["id"]: s.get("color", "grey") for s in status_defs}

    # ── State ────────────────────────────────────────────────────────────────
    state = {
        "search": "",
        "env_filter": "",
        "status_filter": "",
        "type_filter": "",
    }

    # Debounced re-render on filter input. `refresh` is defined further down;
    # the closure resolves it at call time, which is always after render.
    _debounce = {"timer": None}

    def _debounced_refresh() -> None:
        if _debounce["timer"] is not None:
            _debounce["timer"].cancel()
        _debounce["timer"] = ui.timer(0.3, refresh, once=True)

    # ── Header bar ───────────────────────────────────────────────────────────
    with ui.row().classes("w-full items-center gap-3 flex-wrap"):
        ui.input(placeholder="Search servers…") \
            .props(f"{UIStyles.INPUT_PROPS} clearable") \
            .classes("flex-1 min-w-48") \
            .bind_value(state, "search") \
            .on_value_change(lambda _=None: _debounced_refresh())

        envs = [{"label": "All Environments", "value": ""}] + [
            {"label": e["label"], "value": e["id"]}
            for e in catalog.environments().get_all()
        ]
        ui.select(
            options={e["value"]: e["label"] for e in envs},
            value="",
        ).props(UIStyles.INPUT_PROPS).classes("w-44") \
            .bind_value(state, "env_filter") \
            .on_value_change(lambda _=None: _debounced_refresh())

        ui.select(
            options=status_labels,
            value="",
        ).props(UIStyles.INPUT_PROPS).classes("w-36") \
            .bind_value(state, "status_filter") \
            .on_value_change(lambda _=None: _debounced_refresh())

        type_labels = {"": "All Types"} | {
            t["id"]: t["label"] for t in catalog.hardware().get_server_types()
        }
        ui.select(
            options=type_labels, value="",
        ).props(UIStyles.INPUT_PROPS).classes("w-36") \
            .bind_value(state, "type_filter") \
            .on_value_change(lambda _=None: _debounced_refresh())

        ui.button(icon="add", text="Add Server",
                  on_click=lambda: open_configurator_fn(None)) \
            .props("color=primary")

    ui.separator().classes("my-3 border-slate-200 dark:border-white/5")

    # ── Server cards list ─────────────────────────────────────────────────────
    list_area = ui.column().classes("w-full gap-3")

    def _hw_summary(profile: dict, server_type: str) -> str:
        hw = catalog.hardware()
        parts: list[str] = []

        if server_type == "physical":
            cpu_id = profile.get("cpu_id")
            cpu_count = profile.get("cpu_count", 1) or 1
            if cpu_id:
                cpu = hw.get_item(cpu_id)
                label = cpu.get("label", cpu_id) if cpu else cpu_id
                parts.append(f"{cpu_count}× {label}")

            ram_modules = profile.get("ram_modules") or []
            ram_total = sum(
                (hw.get_item(m.get("module_id")) or {}).get("size_gb", 0) * m.get("count", 1)
                for m in ram_modules
            )
            if ram_total:
                parts.append(f"{ram_total} GB RAM")

            disks = profile.get("storage_disks") or []
            disk_total = sum(
                int(d.get("size_gb") or 0) or (hw.get_item(d.get("disk_id")) or {}).get("size_gb", 0)
                for d in disks
            )
            if disk_total:
                tb = disk_total / 1024
                parts.append(f"{tb:.1f} TB storage" if tb >= 1 else f"{disk_total} GB storage")

        else:  # vm / container
            vcpu = profile.get("vcpu_count")
            if vcpu:
                parts.append(f"{vcpu} vCPU")
            ram_gb = profile.get("ram_gb")
            if ram_gb:
                parts.append(f"{ram_gb} GB RAM")
            disks = profile.get("storage_disks") or []
            disk_total = sum(int(d.get("size_gb") or 0) for d in disks) \
                or int(profile.get("storage_gb") or 0)
            if disk_total:
                tb = disk_total / 1024
                parts.append(f"{tb:.1f} TB disk" if tb >= 1 else f"{disk_total} GB disk")

        return " · ".join(parts) if parts else "No hardware configured"

    def refresh():
        list_area.clear()
        if not svc.is_ready:
            with list_area:
                with ui.column().classes("w-full items-center py-12 gap-2"):
                    ui.icon("hourglass_empty", size="48px").classes(UIStyles.ICON_MUTED)
                    ui.label("Waiting for database…").classes(UIStyles.TITLE_H3)
                    ui.label("The server list will appear once the DB connection is ready.") \
                        .classes(UIStyles.TEXT_MUTED)
            return
        try:
            servers = svc.get_all_servers()
        except Exception as exc:
            with list_area:
                with ui.column().classes("w-full items-center py-12 gap-2"):
                    ui.icon("error", size="48px").classes("text-red-400")
                    ui.label("Failed to load servers").classes(UIStyles.TITLE_H3)
                    ui.label(str(exc)).classes(UIStyles.STATUS_TEXT_ERROR + " font-mono")
            return
        q = state["search"].lower()

        filtered = [
            s for s in servers
            if (not q or q in s["name"].lower() or q in (s.get("hostname") or "").lower())
            and (not state["env_filter"] or s["environment_id"] == state["env_filter"])
            and (not state["status_filter"] or s["status"] == state["status_filter"])
            and (not state["type_filter"] or s["server_type"] == state["type_filter"])
        ]

        env_map = {e["id"]: e for e in catalog.environments().get_all()}

        with list_area:
            if not filtered:
                with ui.column().classes("w-full items-center py-12 gap-2"):
                    ui.icon("dns", size="48px").classes(UIStyles.ICON_MUTED)
                    ui.label("No servers found").classes(UIStyles.TITLE_H3)
                    if not servers:
                        ui.label("Click 'Add Server' to register your first server.") \
                            .classes(UIStyles.TEXT_MUTED)
                return

            for server in filtered:
                env = env_map.get(server["environment_id"], {})
                env_color = env.get("color", "grey")
                env_label = env.get("label", server["environment_id"])
                status = server["status"]
                stype = server["server_type"]
                hw_summary = _hw_summary(server.get("hardware_profile") or {}, stype)

                with ui.card().classes(
                    UIStyles.CARD_BASE + " w-full p-0 overflow-hidden "
                    "hover:border-slate-300 dark:hover:border-white/10 transition-colors"
                ):
                    # Accent strip (env colour)
                    ui.element("div").classes(f"h-0.5 w-full bg-{env_color}-500")
                    with ui.row().classes("w-full items-center gap-4 px-4 py-3"):
                        ui.icon(
                            _TYPE_ICON.get(stype, "dns"), size="28px"
                        ).classes(UIStyles.ICON_MUTED + " shrink-0")

                        with ui.column().classes("gap-0 flex-1 min-w-0"):
                            ui.label(server["name"]).classes(
                                UIStyles.TITLE_H3 + " truncate"
                            )
                            hostname = server.get("hostname") or "—"
                            ui.label(hostname).classes(
                                UIStyles.TEXT_MUTED + " font-mono truncate"
                            )

                        ui.label(hw_summary).classes(
                            UIStyles.TEXT_MUTED + " hidden md:block flex-1 truncate"
                        )

                        # Environment badge
                        ui.label(env_label).classes(
                            UIStyles.BADGE_NEUTRAL + f" shrink-0 !text-{env_color}-300"
                        )

                        # Status badge
                        status_display = status_labels.get(status, status.capitalize())
                        status_color = status_colors.get(status, "grey")
                        ui.label(status_display).classes(
                            f"text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 "
                            f"rounded-full shrink-0 "
                            f"bg-{status_color}-500/15 text-{status_color}-300 "
                            f"border border-{status_color}-500/30"
                        )

                        sid = server["id"]
                        ui.button(icon="edit", on_click=lambda _, s=server: open_configurator_fn(s)) \
                            .props("flat round dense size=sm color=blue-grey")
                        ui.button(
                            icon="delete",
                            on_click=lambda _, sid=sid, name=server["name"]: _confirm_delete(sid, name, refresh),
                        ).props("flat round dense size=sm color=red-4")

    def _confirm_delete(server_id: int, name: str, after_fn) -> None:
        with ui.dialog() as dlg, ui.card().classes(UIStyles.MODAL_CONTAINER + " p-6 gap-4"):
            ui.label(f"Delete '{name}'?").classes(UIStyles.TITLE_H3)
            ui.label("This will permanently remove the server record and emit a deletion event.") \
                .classes(UIStyles.TEXT_MUTED)
            with ui.row().classes("gap-2 justify-end w-full pt-2"):
                ui.button("Cancel", on_click=dlg.close).props("flat")
                ui.button("Delete", color="negative", on_click=lambda: (
                    svc.delete_server(server_id),
                    dlg.close(),
                    after_fn(),
                    ui.notify(f"'{name}' deleted.", type="positive"),
                ))
        dlg.open()

    refresh()
    return refresh
