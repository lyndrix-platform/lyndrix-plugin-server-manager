"""Server Manager — 3-step guided server add/edit configurator.

Step 1  Basic Info    name · hostname · environment (→ profile resolved) ·
                      server type · product (filtered by profile+type) ·
                      OS family · status · tags · notes
Step 2  Hardware      CPU/RAM/Storage/Network — filtered to product's allowed IDs
                      and validated live against profile+product rules
Step 3  Review        Summary table + full validation panel
        ↓ always-visible features panel (profile features + limitations)
"""
from __future__ import annotations

from nicegui import ui

from ..service import server_manager_service as svc
from .helpers import _unpack_profile, _build_profile, _collect_form
from .step1 import render_step1
from .step2 import render_step2
from .step3 import render_step3

_STEP_LABELS = ["Basic Info & Product", "Hardware Profile", "Review & Save"]


def open_configurator(existing_server: dict | None, on_saved_fn) -> None:  # noqa: C901
    is_edit = existing_server is not None
    title = f"Edit: {existing_server['name']}" if is_edit else "Add New Server"

    # ── Form state ─────────────────────────────────────────────────────────
    _s = existing_server or {}
    _env_id = _s.get("environment_id") or ""
    _provider_id = _env_id.split(":")[0] if ":" in _env_id else ""
    form: dict = {
        "name": _s.get("name") or "" if is_edit else "",
        "hostname": _s.get("hostname") or "",
        "provider_id": _provider_id,
        "environment_id": _env_id,
        "server_type": _s.get("server_type") or "physical",
        "product_id": _s.get("product_id") or "",
        "service_class_id": _s.get("service_class_id") or "",
        "os_family_id": _s.get("os_family_id") or "",
        "os_version_id": _s.get("os_version_id") or "",
        "os_type": _s.get("os_type") or "",
        "status": _s.get("status") or "active",
        "tags_raw": ", ".join(_s.get("tags") or []) if is_edit else "",
        "notes": _s.get("notes") or "",
        **_unpack_profile(_s.get("hardware_profile") or {}),
    }
    current_step = {"value": 0}

    with ui.dialog().props("maximized persistent") as dlg, \
         ui.card().classes(
             "m-2 bg-zinc-950 border border-zinc-700 overflow-hidden rounded-lg"
         ).style(
             "max-width: none; max-height: none; "
             "width: calc(100% - 16px); height: calc(100% - 16px); "
             "display: flex; flex-direction: column;"
         ):

        # ── Header (compact) ──────────────────────────────────────────────
        with ui.row().classes(
            "w-full items-center justify-between px-4 py-1.5 shrink-0 "
            "border-b border-zinc-800 bg-zinc-900"
        ):
            with ui.row().classes("items-center gap-2"):
                ui.icon("dns", size="16px").classes("text-primary")
                ui.label(title).classes("text-sm font-semibold text-zinc-100")
            ui.button(icon="close", on_click=dlg.close).props("flat round dense size=xs")

        # ── Step bar (compact) ────────────────────────────────────────────
        steps_row = ui.row().classes(
            "w-full px-4 py-1 gap-0 shrink-0 items-center border-b border-zinc-800"
        )

        def _render_step_bar(active: int) -> None:
            steps_row.clear()
            with steps_row:
                for i, label in enumerate(_STEP_LABELS):
                    done = i < active
                    curr = i == active
                    col = "text-primary" if curr else ("text-emerald-400" if done else "text-zinc-600")
                    icon = "check_circle" if done else ("radio_button_checked" if curr else "radio_button_unchecked")
                    with ui.row().classes("items-center gap-1"):
                        ui.icon(icon, size="16px").classes(col)
                        ui.label(label).classes(f"text-xs font-medium {col}")
                    if i < len(_STEP_LABELS) - 1:
                        ui.element("div").classes("flex-1 h-px bg-zinc-700 mx-2")

        _render_step_bar(0)

        # ── Main area: left configurator + right side panel ───────────────
        with ui.element("div").style(
            "flex: 1 1 0; overflow: hidden; display: flex; flex-direction: row; min-height: 0;"
        ):
            with ui.element("div").style("flex: 1 1 0; overflow-y: auto; min-height: 0;"):
                content = ui.column().classes("px-5 py-3 gap-4 w-full")

            ui.element("div").classes("w-px bg-zinc-800 shrink-0").style("align-self: stretch")

            with ui.element("div").style(
                "width: 256px; flex-shrink: 0; display: flex; flex-direction: column; "
                "overflow: hidden; min-height: 0; background: rgba(24,24,27,0.3);"
            ):
                with ui.element("div").classes("px-3 pt-3 pb-2 gap-1.5").style(
                    "flex-shrink: 0; display: flex; flex-direction: column;"
                ):
                    ui.label("Profile").classes(
                        "text-[10px] font-semibold text-zinc-500 uppercase tracking-wider"
                    )
                    features_area = ui.column().classes("gap-1")
                    with features_area:
                        ui.label("Select an environment.") \
                            .classes("text-xs text-zinc-600 italic")

                ui.element("div").classes("w-full h-px bg-zinc-800").style("flex-shrink: 0;")

                with ui.element("div").classes("px-3 pt-3 pb-2 gap-1.5").style(
                    "flex: 1 1 0; overflow-y: auto; display: flex; flex-direction: column; min-height: 0;"
                ):
                    ui.label("Rules").classes(
                        "text-[10px] font-semibold text-zinc-500 uppercase tracking-wider"
                    )
                    validation_panel = ui.column().classes("gap-1")
                    with validation_panel:
                        ui.label("—").classes("text-xs text-zinc-600")

        # ── Features + Validation ─────────────────────────────────────────
        def _render_validation() -> list[dict]:
            issues = svc.validate_server(
                form["server_type"],
                _build_profile(form),
                environment_id=form.get("environment_id"),
                product_id=form.get("product_id") or None,
                os_type=form.get("os_type") or None,
                os_family_id=form.get("os_family_id") or None,
            )
            validation_panel.clear()
            with validation_panel:
                if not issues:
                    with ui.row().classes("items-center gap-1.5 text-emerald-400"):
                        ui.icon("check_circle", size="13px")
                        ui.label("All rules pass.").classes("text-xs")
                else:
                    for issue in issues:
                        color = "text-red-400" if issue["severity"] == "error" else "text-amber-400"
                        icon = "error" if issue["severity"] == "error" else "warning"
                        with ui.row().classes(f"items-start gap-1 {color}"):
                            ui.icon(icon, size="12px").classes("mt-0.5 shrink-0")
                            ui.label(issue["message"]).classes("text-[11px] leading-tight")
            return issues

        def _render_features() -> None:
            features_area.clear()
            env_id = form.get("environment_id") or ""
            if not env_id:
                with features_area:
                    ui.label("Select an environment.") \
                        .classes("text-xs text-zinc-600 italic")
                return
            profile_id = svc.catalog.environments().get_profile_id(env_id)
            profile_meta = svc.catalog.profiles().get(profile_id) or {}
            features = svc.catalog.profiles().get_features(profile_id)
            with features_area:
                ui.label(profile_meta.get("label", profile_id)) \
                    .classes("text-[11px] font-semibold text-zinc-400")
                for feat in features:
                    avail = feat.get("available", True)
                    icon = "check_circle" if avail else "cancel"
                    col = "text-emerald-400" if avail else "text-red-400"
                    bg = "bg-emerald-900/30" if avail else "bg-red-900/20"
                    with ui.element("div").classes(
                        f"flex items-center gap-1.5 px-2 py-1 rounded {bg} cursor-default"
                    ).tooltip(feat.get("description", "")):
                        ui.icon(feat.get("icon", icon), size="13px").classes(col)
                        ui.label(feat.get("label", feat["id"])).classes(f"text-[11px] {col}")

        _render_features()
        _render_validation()

        # ── Navigation bar (compact) ──────────────────────────────────────
        nav_row = ui.row().classes(
            "w-full justify-between items-center px-4 py-2 shrink-0 "
            "border-t border-zinc-800 bg-zinc-900"
        )

        nonlocal_refs: dict = {}

        def go_to(step: int) -> None:
            current_step["value"] = step
            _render_step_bar(step)
            _render_step(step)
            nonlocal_refs["prev"].set_visibility(step > 0)
            nonlocal_refs["next"].set_visibility(step < len(_STEP_LABELS) - 1)
            nonlocal_refs["save"].set_visibility(step == len(_STEP_LABELS) - 1)
            _render_validation()

        def _save():
            issues = _render_validation()
            if any(i["severity"] == "error" for i in issues):
                ui.notify("Please fix all errors before saving.", type="negative")
                return
            data = _collect_form(form)
            try:
                if is_edit:
                    svc.update_server(existing_server["id"], data)
                    ui.notify(f"'{data['name']}' updated.", type="positive")
                else:
                    svc.create_server(data)
                    ui.notify(f"'{data['name']}' created.", type="positive")
                on_saved_fn()
                dlg.close()
            except Exception as exc:
                ui.notify(f"Save failed: {exc}", type="negative")

        with nav_row:
            btn_prev = ui.button("← Back", on_click=lambda: go_to(current_step["value"] - 1)) \
                .props("flat size=sm")
            btn_prev.set_visibility(False)
            ui.element("div").classes("flex-1")
            btn_next = ui.button("Next →", on_click=lambda: go_to(current_step["value"] + 1)) \
                .props("color=primary size=sm")
            btn_save = ui.button(
                "Save" if is_edit else "Create",
                on_click=_save,
            ).props("color=positive icon=save size=sm")
            btn_save.set_visibility(False)

        nonlocal_refs.update({"prev": btn_prev, "next": btn_next, "save": btn_save})

        # ── Step renderer ─────────────────────────────────────────────────
        def _render_step(step: int) -> None:
            content.clear()
            with content:
                if step == 0:
                    render_step1(form, _render_features)
                elif step == 1:
                    render_step2(form)
                else:
                    render_step3(form)

        # Initial render
        _render_step(0)

    dlg.open()
