"""Server Manager — overview/list UI."""
from __future__ import annotations

from nicegui import ui

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

    # ── Header bar ───────────────────────────────────────────────────────────
    with ui.row().classes("w-full items-center gap-3 flex-wrap"):
        ui.input(placeholder="Search servers…") \
            .props("dense outlined dark clearable") \
            .classes("flex-1 min-w-48") \
            .bind_value(state, "search")

        envs = [{"label": "All Environments", "value": ""}] + [
            {"label": e["label"], "value": e["id"]}
            for e in catalog.environments().get_all()
        ]
        ui.select(
            options={e["value"]: e["label"] for e in envs},
            value="",
        ).props("dense outlined dark").classes("w-44") \
            .bind_value(state, "env_filter")

        ui.select(
            options=status_labels,
            value="",
        ).props("dense outlined dark").classes("w-36") \
            .bind_value(state, "status_filter")

        types = [""] + [t["id"] for t in catalog.hardware().get_server_types()]
        type_labels = {"": "All Types"} | {
            t["id"]: t["label"] for t in catalog.hardware().get_server_types()
        }
        ui.select(
            options=type_labels, value="",
        ).props("dense outlined dark").classes("w-36") \
            .bind_value(state, "type_filter")

        ui.button(icon="add", text="Add Server",
                  on_click=lambda: open_configurator_fn(None)) \
            .props("color=primary")

    ui.separator().classes("my-3 border-zinc-700")

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
                (hw.get_item(d.get("disk_id")) or {}).get("size_gb", 0) * d.get("count", 1)
                for d in disks
            )
            if disk_total:
                tb = disk_total / 1024
                parts.append(f"{tb:.1f} TB storage" if tb >= 1 else f"{disk_total} GB storage")

            net_id = profile.get("network_id")
            if net_id:
                net = hw.get_item(net_id)
                if net:
                    parts.append(net.get("label", net_id))

        else:  # vm / container
            vcpu = profile.get("vcpu_count")
            if vcpu:
                parts.append(f"{vcpu} vCPU")
            ram_gb = profile.get("ram_gb")
            if ram_gb:
                parts.append(f"{ram_gb} GB RAM")
            disk_gb = profile.get("storage_gb")
            if disk_gb:
                parts.append(f"{disk_gb} GB disk")

        return " · ".join(parts) if parts else "No hardware configured"

    def refresh():
        list_area.clear()
        if not svc.is_ready:
            with list_area:
                with ui.column().classes("w-full items-center py-12 gap-2 text-zinc-500"):
                    ui.icon("hourglass_empty", size="48px")
                    ui.label("Waiting for database…").classes("text-lg")
                    ui.label("The server list will appear once the DB connection is ready.") \
                        .classes("text-sm")
            return
        try:
            servers = svc.get_all_servers()
        except Exception as exc:
            with list_area:
                with ui.column().classes("w-full items-center py-12 gap-2 text-red-400"):
                    ui.icon("error", size="48px")
                    ui.label("Failed to load servers").classes("text-lg")
                    ui.label(str(exc)).classes("text-xs font-mono")
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
                with ui.column().classes("w-full items-center py-12 gap-2 text-zinc-500"):
                    ui.icon("dns", size="48px")
                    ui.label("No servers found").classes("text-lg")
                    if not servers:
                        ui.label("Click 'Add Server' to register your first server.") \
                            .classes("text-sm")
                return

            for server in filtered:
                env = env_map.get(server["environment_id"], {})
                env_color = env.get("color", "grey")
                env_label = env.get("label", server["environment_id"])
                status = server["status"]
                stype = server["server_type"]
                hw_summary = _hw_summary(server.get("hardware_profile") or {}, stype)

                with ui.card().classes(
                    "w-full p-0 overflow-hidden bg-zinc-900 border border-zinc-700 "
                    "hover:border-zinc-500 transition-colors"
                ):
                    # Accent strip
                    ui.element("div").classes(
                        f"h-0.5 w-full bg-{env_color}-500"
                    )
                    with ui.row().classes("w-full items-center gap-4 px-4 py-3"):
                        # Type icon
                        ui.icon(
                            _TYPE_ICON.get(stype, "dns"), size="28px"
                        ).classes("text-slate-400 shrink-0")

                        # Name / hostname
                        with ui.column().classes("gap-0 flex-1 min-w-0"):
                            ui.label(server["name"]).classes(
                                "text-sm font-semibold text-zinc-100 truncate"
                            )
                            hostname = server.get("hostname") or "—"
                            ui.label(hostname).classes(
                                "text-xs text-zinc-400 font-mono truncate"
                            )

                        # HW summary
                        ui.label(hw_summary).classes(
                            "text-xs text-zinc-400 hidden md:block flex-1 truncate"
                        )

                        # Environment badge
                        ui.badge(env_label, color=env_color).classes("text-xs shrink-0")

                        # Status badge
                        ui.badge(
                            status_labels.get(status, status.capitalize()),
                            color=status_colors.get(status, "grey"),
                        ).classes("text-xs shrink-0")

                        # Actions
                        sid = server["id"]
                        ui.button(icon="edit", on_click=lambda _, s=server: open_configurator_fn(s)) \
                            .props("flat round dense size=sm color=blue-grey")
                        ui.button(
                            icon="delete",
                            on_click=lambda _, sid=sid, name=server["name"]: _confirm_delete(sid, name, refresh),
                        ).props("flat round dense size=sm color=red-4")

    def _confirm_delete(server_id: int, name: str, after_fn) -> None:
        with ui.dialog() as dlg, ui.card().classes("p-6 gap-4 bg-zinc-900 border border-zinc-700"):
            ui.label(f"Delete '{name}'?").classes("text-base font-semibold text-zinc-100")
            ui.label("This will permanently remove the server record and emit a deletion event.")  \
                .classes("text-sm text-zinc-400")
            with ui.row().classes("gap-2 justify-end w-full pt-2"):
                ui.button("Cancel", on_click=dlg.close).props("flat")
                ui.button("Delete", color="negative", on_click=lambda: (
                    svc.delete_server(server_id),
                    dlg.close(),
                    after_fn(),
                    ui.notify(f"'{name}' deleted.", type="positive"),
                ))
        dlg.open()

    # Wire up reactive refresh on filter changes
    for key in ("search", "env_filter", "status_filter", "type_filter"):
        ui.timer(0.4, refresh, once=True)

    state_ref = state

    def _on_change(*_):
        refresh()

    # Initial render
    refresh()

    # Expose refresh for external callers (e.g. after save in configurator)
    return refresh
