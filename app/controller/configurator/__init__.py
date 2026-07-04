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
from core.api import UIStyles

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
             UIStyles.MODAL_CONTAINER + " m-2 overflow-hidden"
         ).style(
             "max-width: none; max-height: none; padding: 0; "
             "width: calc(100% - 16px); height: calc(100% - 16px); "
             "display: flex; flex-direction: column;"
         ):

        # Modern accent bar across the top of the dialog.
        ui.element("div").classes(UIStyles.GRAD_BAR_ACCENT + " shrink-0")

        # ── Header (compact) ──────────────────────────────────────────────
        with ui.row().classes(
            "w-full items-center justify-between px-4 py-1.5 shrink-0 "
            "border-b border-[var(--lx-border-soft)]"
        ):
            with ui.row().classes("items-center gap-2"):
                ui.icon("dns", size="16px").classes(UIStyles.ICON_PRIMARY)
                ui.label(title).classes(UIStyles.LABEL_HEADING)
            ui.button(icon="close", on_click=dlg.close).props("flat round dense size=xs")

        # ── Step bar (compact) ──────────────────────────────────────────────
        # Shared `.lx-stepper` component (Theming v2 T0b) — the exact same
        # classes ServerWizard.tsx's step bar uses, so the two GUIs stop
        # drifting on step-indicator styling.
        steps_row = ui.row().classes(
            f"w-full px-4 py-1.5 shrink-0 border-b border-[var(--lx-border-soft)] {UIStyles.STEPPER}"
        )

        def _render_step_bar(active: int) -> None:
            steps_row.clear()
            with steps_row:
                for i, label in enumerate(_STEP_LABELS):
                    done = i < active
                    curr = i == active
                    step_state_cls = (" lx-stepper__step--active" if curr
                                       else " lx-stepper__step--done" if done else "")
                    label_cls = ("text-[var(--lx-accent)]" if curr
                                 else "text-[var(--lx-state-success)]" if done
                                 else "text-[var(--lx-text-muted)]")
                    with ui.row().classes("items-center gap-1.5"):
                        with ui.element("div").classes(f"lx-stepper__step{step_state_cls}"):
                            if done:
                                ui.icon("check", size="14px")
                            else:
                                ui.label(str(i + 1)).classes("text-[10px] font-bold")
                        ui.label(label).classes(f"text-xs font-medium {label_cls}")
                    if i < len(_STEP_LABELS) - 1:
                        ui.element("div").classes("lx-stepper__bar")

        _render_step_bar(0)

        # ── Main area: left configurator + right info panel ───────────────
        # Tailwind utilities (proven to work in this stack incl. dialogs): the
        # info panel is docked to the right edge as a slim fixed column on md+
        # and stacks below the configurator on narrow screens. Collapse is driven
        # by an inline `display` toggle so it works on desktop, not only mobile.
        panel_state = {"collapsed": False}

        def _toggle_panel() -> None:
            panel_state["collapsed"] = not panel_state["collapsed"]
            if panel_state["collapsed"]:
                side_panel.style("display:none")
                reopen_handle.style("display:flex")
            else:
                side_panel.style("display:flex")
                reopen_handle.style("display:none")

        # Scroll model: mobile = the whole body scrolls as one (overflow-y-auto,
        # children flow at natural height); desktop = a fixed-height row where each
        # column scrolls internally (overflow-hidden + md:-guarded inner scrollers).
        # NOTE: use `grow basis-0` (flex-basis: 0px), NOT `flex-1` (flex-basis: 0%).
        # A 0% basis only resolves against a parent with a *definite* height; a
        # stretched flex item's height is not definite, so `flex-1` scrollers never
        # get a height and the content grows out of the layout instead of scrolling.
        with ui.element("div").classes(
            "w-full grow basis-0 min-h-0 overflow-y-auto md:overflow-hidden flex flex-col md:flex-row"
        ):
            # Left configurator. Desktop: a height-bounded flex-col whose inner div
            # is the scroller. Mobile: plain blocks, content flows.
            with ui.element("div").classes(
                "w-full md:grow md:basis-0 md:min-h-0 md:flex md:flex-col md:overflow-hidden"
            ):
                with ui.element("div").classes("md:grow md:basis-0 md:min-h-0 md:overflow-y-auto"):
                    content = ui.column().classes("px-5 py-3 gap-4 w-full")

            side_panel = ui.element("div").classes(
                UIStyles.PANEL_SUBTLE
                + " w-full md:w-[248px] md:shrink-0 flex flex-col md:min-h-0 md:overflow-hidden "
                "border-t md:border-t-0 md:border-l"
            )
            with side_panel:
                with ui.row().classes("w-full items-center justify-between px-3 pt-3 pb-2 shrink-0"):
                    ui.label("Profile").classes(UIStyles.LABEL_MINI)
                    # Collapse control — desktop only (panel just stacks on mobile).
                    ui.button(icon="chevron_right", on_click=lambda: _toggle_panel()) \
                        .props("flat dense size=xs") \
                        .classes(UIStyles.ICON_MUTED + " hidden md:flex") \
                        .tooltip("Collapse panel")
                with ui.column().classes("px-3 pb-2 gap-1 shrink-0"):
                    features_area = ui.column().classes("gap-1")
                    with features_area:
                        ui.label("Select an environment.") \
                            .classes(UIStyles.TEXT_HINT + " italic")

                ui.element("div").classes("w-full h-px bg-[var(--lx-border-soft)] shrink-0")

                with ui.column().classes(
                    "px-3 pt-3 pb-2 gap-1 md:grow md:basis-0 md:min-h-0 md:overflow-y-auto"
                ):
                    ui.label("Rules").classes(UIStyles.LABEL_MINI)
                    validation_panel = ui.column().classes("gap-1")
                    with validation_panel:
                        ui.label("—").classes(UIStyles.TEXT_HINT)

            # Thin re-open handle docked to the right edge while collapsed (desktop).
            reopen_handle = ui.element("div").classes(
                "flex-col items-center shrink-0 px-1 py-2 cursor-pointer "
                "border-l border-[var(--lx-border-soft)] hover:bg-[var(--lx-elevated)]"
            ).style("display:none").tooltip("Show Profile & Rules")
            with reopen_handle:
                ui.icon("chevron_left", size="18px").classes(UIStyles.ICON_MUTED)
            reopen_handle.on("click", lambda: _toggle_panel())

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
                    with ui.row().classes("items-center gap-1.5"):
                        ui.icon("check_circle", size="13px").classes(UIStyles.ICON_SUCCESS)
                        ui.label("All rules pass.").classes(UIStyles.STATUS_TEXT_SUCCESS)
                else:
                    for issue in issues:
                        is_err = issue["severity"] == "error"
                        color = UIStyles.STATUS_TEXT_ERROR if is_err else UIStyles.STATUS_TEXT_WARNING
                        icon = "error" if is_err else "warning"
                        with ui.row().classes("items-start gap-1"):
                            ui.icon(icon, size="12px").classes(
                                (UIStyles.ICON_WARNING if not is_err else UIStyles.STATUS_TEXT_ERROR)
                                + " mt-0.5 shrink-0"
                            )
                            ui.label(issue["message"]).classes(color + " leading-tight")
            return issues

        def _render_features() -> None:
            features_area.clear()
            env_id = form.get("environment_id") or ""
            if not env_id:
                with features_area:
                    ui.label("Select an environment.") \
                        .classes(UIStyles.TEXT_HINT + " italic")
                return
            profile_id = svc.catalog.environments().get_profile_id(env_id)
            profile_meta = svc.catalog.profiles().get(profile_id) or {}
            features = svc.catalog.profiles().get_features(profile_id)
            with features_area:
                ui.label(profile_meta.get("label", profile_id)).classes(UIStyles.LABEL_FIELD)
                for feat in features:
                    avail = feat.get("available", True)
                    icon = "check_circle" if avail else "cancel"
                    col = UIStyles.ICON_SUCCESS if avail else UIStyles.STATUS_TEXT_ERROR
                    bg = (
                        "bg-[color-mix(in_srgb,var(--lx-state-up)_10%,transparent)]" if avail
                        else "bg-[color-mix(in_srgb,var(--lx-state-down)_10%,transparent)]"
                    )
                    with ui.element("div").classes(
                        f"flex items-center gap-1.5 px-2 py-1 rounded-[var(--lx-radius-lg)] {bg} cursor-default"
                    ).tooltip(feat.get("description", "")):
                        ui.icon(feat.get("icon", icon), size="13px").classes(col)
                        ui.label(feat.get("label", feat["id"])).classes(f"text-[11px] {col}")

        def _refresh_side() -> None:
            """Refresh both side-panel sections (profile features + live rule
            validation). Passed into every step so any field change re-evaluates
            the constraint engine immediately, not just on step navigation."""
            _render_features()
            _render_validation()

        _refresh_side()

        # ── Navigation bar (compact) ──────────────────────────────────────
        nav_row = ui.row().classes(
            "w-full justify-between items-center px-4 py-2 shrink-0 "
            "border-t border-[var(--lx-border-soft)]"
        )

        nonlocal_refs: dict = {}

        def go_to(step: int) -> None:
            current_step["value"] = step
            _render_step_bar(step)
            _render_step(step)
            nonlocal_refs["prev"].set_visibility(step > 0)
            nonlocal_refs["next"].set_visibility(step < len(_STEP_LABELS) - 1)
            nonlocal_refs["save"].set_visibility(step == len(_STEP_LABELS) - 1)
            _refresh_side()

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
                    render_step1(form, _refresh_side)
                elif step == 1:
                    render_step2(form, _refresh_side)
                else:
                    render_step3(form)

        # Initial render
        _render_step(0)

    dlg.open()
