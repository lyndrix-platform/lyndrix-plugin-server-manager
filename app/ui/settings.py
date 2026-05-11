"""Server Manager — plugin settings UI."""
from __future__ import annotations

from pathlib import Path

from nicegui import ui

from ..controller.service import server_manager_service as svc


def render_settings_ui(ctx):
    with ui.column().classes("w-full gap-6"):
        # ── Catalog path ──────────────────────────────────────────────────────
        with ui.card().classes("w-full p-5 gap-4 bg-zinc-900 border border-zinc-700"):
            ui.label("Catalog Configuration").classes("text-base font-semibold text-zinc-200")
            ui.label(
                "Point to a custom directory containing hardware.yml and environments.yml. "
                "Leave blank to use the built-in default catalog shipped with the plugin."
            ).classes("text-xs text-zinc-400")

            current_dir = str(svc.catalog.catalog_dir)
            default_dir = str(Path(__file__).parent / "catalog")
            is_default = current_dir == default_dir

            catalog_input = ui.input(
                "Catalog Directory",
                value="" if is_default else current_dir,
                placeholder=f"default: {default_dir}",
            ).props("outlined dark dense clearable").classes("w-full")

            def _save_catalog_path():
                path = (catalog_input.value or "").strip()
                if path and not Path(path).is_dir():
                    ui.notify(f"Directory not found: {path}", type="negative")
                    return
                if path:
                    svc.set_catalog_dir(path)
                    ui.notify(f"Catalog path updated: {path}", type="positive")
                else:
                    svc.set_catalog_dir(str(Path(__file__).parent / "catalog"))
                    ui.notify("Reset to built-in catalog.", type="info")
                _refresh_preview()

            def _reload_catalog():
                svc.reload_catalog()
                ui.notify("Catalog reloaded from disk.", type="positive")
                _refresh_preview()

            with ui.row().classes("gap-2"):
                ui.button("Apply Path", icon="save", on_click=_save_catalog_path) \
                    .props("color=primary dense")
                ui.button("Reload Catalog", icon="refresh", on_click=_reload_catalog) \
                    .props("flat dense")

        # ── Catalog Preview ───────────────────────────────────────────────────
        preview_area = ui.column().classes("w-full gap-4")

        def _refresh_preview():
            preview_area.clear()
            hw = svc.catalog.hardware()
            envs = svc.catalog.environments()
            profiles = svc.catalog.profiles()
            products = svc.catalog.products()

            with preview_area:
                with ui.row().classes("w-full gap-4 flex-wrap"):
                    _stat_card("CPU Options", len(hw.get_cpu_options()), "memory")
                    _stat_card("RAM Modules", len(hw.get_ram_options()), "memory")
                    _stat_card("Storage Options", len(hw.get_storage_options()), "storage")
                    _stat_card("Network Options", len(hw.get_network_options()), "network_node")
                    _stat_card("Profiles", len(profiles.get_all()), "category")
                    _stat_card("Products", len(products.get_all()), "inventory_2")
                    _stat_card("Providers", len(envs.get_providers()), "public")
                    _stat_card("Stages", len(envs.get_all()), "layers")
                    _stat_card("Legacy Rules", len(hw.get_combination_rules()), "rule")

                # Profiles accordion
                with ui.card().classes("w-full p-4 gap-3 bg-zinc-900 border border-zinc-700"):
                    ui.label("Environment Profiles").classes("text-sm font-semibold text-zinc-300")
                    for profile in profiles.get_all():
                        profile_id = profile["id"]
                        color = profile.get("color", "blue")
                        with ui.expansion(
                            f"{profile.get('label', profile_id)}  [{profile_id}]",
                            icon=profile.get("icon", "info"),
                        ).classes("w-full border border-zinc-700 rounded"):
                            ui.label(profile.get("description", "")).classes("text-xs text-zinc-400 mb-2")
                            allowed = ", ".join(profile.get("allowed_server_types", []))
                            ui.label(f"Allowed server types: {allowed}").classes("text-xs text-zinc-500")

                            ui.label("Features:").classes("text-xs font-semibold text-zinc-400 mt-2")
                            for feat in profiles.get_features(profile_id):
                                avail = feat.get("available", True)
                                icon = "check_circle" if avail else "cancel"
                                col = "text-emerald-400" if avail else "text-red-400"
                                with ui.row().classes(f"items-start gap-2 {col} py-0.5"):
                                    ui.icon(icon, size="14px").classes("mt-0.5 shrink-0")
                                    with ui.column().classes("gap-0"):
                                        ui.label(feat.get("label", feat["id"])).classes("text-xs font-medium")
                                        ui.label(feat.get("description", "")).classes("text-xs text-zinc-500")

                            rules = profiles.get_rules(profile_id)
                            if rules:
                                ui.label(f"Rules ({len(rules)}):").classes("text-xs font-semibold text-zinc-400 mt-2")
                                for rule in rules:
                                    sev_color = "text-red-400" if rule.get("severity") == "error" else "text-amber-400"
                                    with ui.row().classes(f"items-start gap-2 {sev_color} py-0.5"):
                                        ui.icon("rule", size="13px").classes("mt-0.5 shrink-0")
                                        ui.label(f"[{rule.get('type', '?')}] {rule.get('description', rule['id'])}") \
                                            .classes("text-xs")

                # Products accordion
                with ui.card().classes("w-full p-4 gap-3 bg-zinc-900 border border-zinc-700"):
                    ui.label("Server Products").classes("text-sm font-semibold text-zinc-300")
                    for prod in products.get_all():
                        compatible = ", ".join(prod.get("compatible_profiles", []))
                        with ui.expansion(
                            f"{prod.get('label', prod['id'])}  [{prod['id']}]",
                            icon=prod.get("icon", "inventory_2"),
                        ).classes("w-full border border-zinc-700 rounded"):
                            ui.label(prod.get("description", "")).classes("text-xs text-zinc-400 mb-1")
                            ui.label(f"Profiles: {compatible}").classes("text-xs text-zinc-500")
                            types = ", ".join(prod.get("compatible_server_types", []))
                            ui.label(f"Server types: {types}").classes("text-xs text-zinc-500")

                            for comp in ("cpu", "ram", "storage", "network"):
                                allowed_ids = prod.get(f"allowed_{comp}_ids")
                                if allowed_ids:
                                    labels = []
                                    for aid in allowed_ids:
                                        item = hw.get_item(aid)
                                        labels.append((item or {}).get("label", aid))
                                    ui.label(f"{comp.upper()} allowed: {', '.join(labels)}") \
                                        .classes("text-xs text-zinc-400 mt-1")

                            rules = prod.get("rules") or []
                            if rules:
                                ui.label(f"Product rules ({len(rules)}):").classes("text-xs font-semibold text-zinc-400 mt-2")
                                for rule in rules:
                                    sev_color = "text-red-400" if rule.get("severity") == "error" else "text-amber-400"
                                    with ui.row().classes(f"items-start gap-2 {sev_color}"):
                                        ui.icon("rule", size="13px").classes("mt-0.5 shrink-0")
                                        ui.label(f"[{rule.get('type', '?')}] {rule.get('description', rule['id'])}") \
                                            .classes("text-xs")

                # Providers & Stages table
                with ui.card().classes("w-full p-4 gap-3 bg-zinc-900 border border-zinc-700"):
                    ui.label("Providers & Stages").classes("text-sm font-semibold text-zinc-300")
                    for provider in envs.get_providers():
                        pid = provider["id"]
                        color = provider.get("color", "grey")
                        placeholder_tag = " [placeholder]" if provider.get("placeholder") else ""
                        with ui.expansion(
                            f"{provider.get('label', pid)}{placeholder_tag}",
                            icon=provider.get("icon", "public"),
                        ).classes("w-full border border-zinc-700 rounded"):
                            with ui.row().classes("items-center gap-2 mb-1"):
                                ui.badge(provider.get("profile_id", "edc"), color=color) \
                                    .classes("text-xs")
                                ui.label(provider.get("display_label", "")) \
                                    .classes("text-xs text-zinc-400")
                            ui.label(provider.get("description", "")).classes("text-xs text-zinc-500 mb-2")
                            for stage in envs.get_stages_for_provider(pid):
                                with ui.row().classes("w-full items-start gap-3 py-1 pl-2 border-l border-zinc-700"):
                                    ui.badge(stage["label"], color=stage.get("color", "grey")) \
                                        .classes("text-xs shrink-0")
                                    with ui.column().classes("gap-0"):
                                        ui.label(f"ID: {stage['id']}").classes("text-xs text-zinc-500")
                                        wf = stage.get("order_workflow") or "none"
                                        ui.label(f"Workflow: {wf}").classes("text-xs text-zinc-400")
                                        ui.label(stage.get("description", "")).classes("text-xs text-zinc-500")

        _refresh_preview()

        # ── Event bus documentation ───────────────────────────────────────────
        with ui.card().classes("w-full p-5 gap-3 bg-zinc-900 border border-zinc-700"):
            ui.label("Event Bus — Hook Points").classes("text-base font-semibold text-zinc-200")
            ui.label(
                "Other plugins can subscribe to these events to react to server changes."
            ).classes("text-xs text-zinc-400")

            events = [
                ("server_manager:server_created",
                 "Full server dict. Fired when a new server is registered."),
                ("server_manager:server_updated",
                 "Full server dict + {changes: {field: {old, new}}}. Fired on any save."),
                ("server_manager:server_deleted",
                 "Last known server dict. Fired on deletion."),
                ("server_manager:hardware_changed",
                 "{server_id, server_name, action, old_profile, new_profile, environment_id, "
                 "server_type}. Fired when hardware config changes — use for order workflows."),
                ("server_manager:status_changed",
                 "{server_id, server_name, old_status, new_status}. Fired on status transitions."),
            ]
            for event, desc in events:
                with ui.row().classes("w-full items-start gap-3 py-1 border-b border-zinc-800"):
                    ui.label(event).classes("font-mono text-xs text-emerald-400 w-72 shrink-0")
                    ui.label(desc).classes("text-xs text-zinc-400")


def _stat_card(label: str, count: int, icon: str) -> None:
    with ui.card().classes("p-3 gap-1 bg-zinc-800 border border-zinc-700 min-w-28"):
        with ui.row().classes("items-center gap-2"):
            ui.icon(icon, size="16px").classes("text-zinc-400")
            ui.label(label).classes("text-xs text-zinc-400")
        ui.label(str(count)).classes("text-2xl font-bold text-zinc-100")
