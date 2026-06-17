"""Step 1 — Basic Info & Product selection."""
from __future__ import annotations

from nicegui import ui
from core.api import UIStyles

from ..service import server_manager_service as svc


def _tile_cls(*, selected: bool, disabled: bool = False) -> str:
    """Themed selectable-tile classes for the given state (border + bg + text)."""
    if disabled:
        state = UIStyles.TILE_DISABLED
    elif selected:
        state = UIStyles.TILE_SELECTED
    else:
        state = UIStyles.TILE_DEFAULT
    return f"{UIStyles.TILE_BASE} {state}"


def _tile_icon_cls(*, selected: bool, disabled: bool = False, entity_color: str | None = None) -> str:
    """Icon colour for a tile: primary when selected, muted when disabled, else the
    catalog-provided per-entity accent colour."""
    if disabled:
        return UIStyles.ICON_MUTED
    if selected:
        return UIStyles.ICON_PRIMARY
    return f"text-{entity_color}-400" if entity_color else UIStyles.ICON_MUTED


def render_step1(form: dict, on_features_refresh) -> None:  # noqa: C901
    """Render step 1: name/hostname · provider/env/type · OS · product · status/tags/notes."""
    ui.label("Basic Information & Product").classes(UIStyles.TITLE_H3)

    with ui.row().classes("w-full gap-4"):
        ui.input("Server Name *").props(UIStyles.INPUT_PROPS).classes("flex-1") \
            .bind_value(form, "name")
        ui.input("Hostname").props(UIStyles.INPUT_PROPS).classes("flex-1") \
            .bind_value(form, "hostname")

    envs = svc.catalog.environments()
    status_defs = svc.catalog.settings().get_statuses()

    # Mutable area references — recreated on each full provider section redraw
    areas: dict = {"stage": None, "type": None, "os": None, "product": None, "extras": None}

    # ── Callbacks ──────────────────────────────────────────────────────────
    def _on_provider_change(pid: str) -> None:
        form["provider_id"] = pid
        form["environment_id"] = ""
        form["server_type"] = ""
        form["product_id"] = ""
        form["service_class_id"] = ""
        form["os_family_id"] = ""
        form["os_version_id"] = ""
        form["os_type"] = ""
        _render_provider_section()
        on_features_refresh()

    def _on_env_change(env_id: str) -> None:
        form["environment_id"] = env_id
        form["server_type"] = ""
        form["product_id"] = ""
        form["service_class_id"] = ""
        form["os_family_id"] = ""
        form["os_version_id"] = ""
        form["os_type"] = ""
        _render_stage_area()
        _render_type_area()
        _render_os_area()
        _render_product_area()
        _render_product_extras_area()
        on_features_refresh()

    def _on_type_change(type_id: str) -> None:
        form["server_type"] = type_id
        form["product_id"] = ""
        form["service_class_id"] = ""
        form["os_family_id"] = ""
        form["os_version_id"] = ""
        form["os_type"] = ""
        _render_type_area()
        _render_os_area()
        _render_product_area()
        _render_product_extras_area()
        on_features_refresh()

    def _on_product_change(pid: str) -> None:
        form["product_id"] = pid
        products_cat = svc.catalog.products()
        # Auto-select first SK if product has service classes
        scs = products_cat.get_service_classes(pid)
        form["service_class_id"] = scs[0]["id"] if scs else ""
        # Auto-select preferred version for the already-selected OS family
        fam_id = form.get("os_family_id") or ""
        if fam_id:
            versions = next(
                (f.get("os_versions") or []
                 for f in products_cat.get_os_variants(pid) if f["id"] == fam_id),
                []
            )
            pref = next((v for v in versions if v.get("lifecycle") == "preferred"), None)
            form["os_version_id"] = (pref or (versions[0] if versions else {})).get("id", "")
        _render_product_area()
        _render_product_extras_area()
        on_features_refresh()

    def _on_os_change(fam_id: str) -> None:
        form["os_family_id"] = fam_id
        form["os_version_id"] = ""
        form["os_type"] = svc.catalog.products().os_type_for_family(fam_id) or ""
        form["product_id"] = ""
        form["service_class_id"] = ""
        _render_os_area()
        _render_product_area()
        _render_product_extras_area()
        on_features_refresh()

    # ── Tile builders ──────────────────────────────────────────────────────
    def _render_provider_tiles() -> None:
        ui.label("Provider *").classes(UIStyles.LABEL_FIELD + " mt-1")
        with ui.row().classes("w-full gap-3 flex-wrap"):
            for p in envs.get_providers():
                pid = p["id"]
                is_ph = p.get("placeholder", False)
                is_sel = form.get("provider_id") == pid
                tile = ui.element("div").classes(
                    f"flex flex-col items-center justify-center gap-1 p-3 "
                    f"{_tile_cls(selected=is_sel, disabled=is_ph)}"
                ).style("min-width:100px; min-height:80px")
                with tile:
                    ui.icon(p.get("icon", "dns"), size="26px").classes(
                        _tile_icon_cls(selected=is_sel, disabled=is_ph,
                                       entity_color=p.get("color"))
                    )
                    ui.label(p["label"]).classes(
                        "text-xs font-bold text-center leading-tight"
                    )
                    if is_ph:
                        ui.label("soon").classes(UIStyles.TEXT_HINT + " italic")
                if not is_ph:
                    tile.on("click", lambda _, _pid=pid: _on_provider_change(_pid))

    def _render_stage_area() -> None:
        sa = areas.get("stage")
        if sa is None:
            return
        sa.clear()
        pid = form.get("provider_id") or ""
        if not pid:
            return
        stages = envs.get_stages_for_provider(pid)
        if not stages:
            return
        current_env = form.get("environment_id") or ""
        provider = envs.get_provider(pid) or {}
        profile_id = provider.get("profile_id", "")
        with sa:
            if profile_id:
                profile = svc.catalog.profiles().get(profile_id) or {}
                color = profile.get("color", "blue")
                with ui.row().classes("items-center gap-2"):
                    ui.icon(profile.get("icon", "info"), size="14px").classes(f"text-{color}-400")
                    ui.label(profile.get("label", profile_id)).classes(
                        f"text-xs font-semibold text-{color}-300"
                    )
                    allowed_types = profile.get("allowed_server_types", [])
                    if allowed_types:
                        ui.label("(" + " / ".join(allowed_types) + ")").classes(
                            UIStyles.TEXT_MUTED
                        )
            ui.label("Stage *").classes(UIStyles.LABEL_FIELD)
            with ui.row().classes("w-full gap-3 flex-wrap"):
                for s in stages:
                    compound = s["id"]  # "provider:stage"
                    is_sel = compound == current_env
                    tile = ui.element("div").classes(
                        f"flex flex-col items-center justify-center gap-1 p-3 "
                        f"{_tile_cls(selected=is_sel)}"
                    ).style("min-width:90px; min-height:72px")
                    with tile:
                        ui.icon(s.get("icon", "layers"), size="22px").classes(
                            _tile_icon_cls(selected=is_sel, entity_color=s.get("color"))
                        )
                        ui.label(s["label"]).classes(
                            "text-xs font-semibold text-center leading-tight"
                        )
                    tile.on("click", lambda _, _cid=compound: _on_env_change(_cid))

    def _render_type_area() -> None:
        ta = areas.get("type")
        if ta is None:
            return
        ta.clear()
        env_id = form.get("environment_id") or ""
        if not env_id:
            return
        profile_id = envs.get_profile_id(env_id)
        all_types = svc.catalog.hardware().get_server_types()
        if profile_id:
            allowed = set(svc.catalog.profiles().allowed_server_types(profile_id))
        else:
            allowed = {t["id"] for t in all_types}
        if not form.get("server_type") and len(allowed) == 1:
            form["server_type"] = next(iter(allowed))
        with ta:
            ui.label("Server Type *").classes(UIStyles.LABEL_FIELD)
            with ui.row().classes("w-full gap-3 flex-wrap"):
                for t in all_types:
                    tid = t["id"]
                    is_dis = tid not in allowed
                    is_sel = form.get("server_type") == tid
                    tile = ui.element("div").classes(
                        f"flex flex-col items-center justify-center gap-1 p-3 "
                        f"{_tile_cls(selected=is_sel, disabled=is_dis)}"
                    ).style("min-width:100px; min-height:80px")
                    with tile:
                        ui.icon(t.get("icon", "memory"), size="26px").classes(
                            _tile_icon_cls(selected=is_sel, disabled=is_dis)
                        )
                        ui.label(t.get("label", tid)).classes(
                            "text-xs font-bold text-center leading-tight"
                        )
                    if not is_dis:
                        tile.on("click", lambda _, _tid=tid: _on_type_change(_tid))

    def _render_os_area() -> None:
        oa = areas.get("os")
        if oa is None:
            return
        oa.clear()
        env_id = form.get("environment_id") or ""
        stype = form.get("server_type") or ""
        if not env_id or not stype:
            return
        profile_id = envs.get_profile_id(env_id)
        all_products = svc.catalog.products().get_for_profile(profile_id, stype)
        seen: dict = {}
        for prod in all_products:
            for fam in svc.catalog.products().get_os_variants(prod["id"]):
                if fam["id"] not in seen:
                    seen[fam["id"]] = fam
        if not seen:
            return
        with oa:
            ui.label("Operating System *").classes(UIStyles.LABEL_FIELD)
            with ui.row().classes("w-full gap-3 flex-wrap"):
                for fam in seen.values():
                    fid = fam["id"]
                    is_sel = form.get("os_family_id") == fid
                    tile = ui.element("div").classes(
                        f"flex flex-col items-center justify-center gap-1 p-3 "
                        f"{_tile_cls(selected=is_sel)}"
                    ).style("min-width:100px; min-height:72px")
                    with tile:
                        ui.icon(fam.get("icon", "computer"), size="22px").classes(
                            _tile_icon_cls(selected=is_sel)
                        )
                        ui.label(fam.get("label", fid)).classes(
                            "text-xs font-bold leading-tight"
                        )
                    tile.on("click", lambda _, _fid=fid: _on_os_change(_fid))

    def _render_product_area() -> None:
        pa = areas.get("product")
        if pa is None:
            return
        pa.clear()
        env_id = form.get("environment_id") or ""
        stype = form.get("server_type") or ""
        if not env_id or not stype:
            return
        profile_id = envs.get_profile_id(env_id)
        products = svc.catalog.products().get_for_profile(profile_id, stype)
        os_fam = form.get("os_family_id") or ""
        if os_fam:
            pc = svc.catalog.products()
            products = [
                p for p in products
                if not pc.get_os_variants(p["id"])
                or any(f["id"] == os_fam for f in pc.get_os_variants(p["id"]))
            ]
        if not products:
            with pa:
                ui.label(
                    "No products available for this environment / type combination."
                ).classes(UIStyles.TEXT_HINT + " italic")
            return
        prod_ids = [p["id"] for p in products]
        current = form.get("product_id") or ""
        if current not in prod_ids:
            form["product_id"] = prod_ids[0] if len(prod_ids) == 1 else ""
        with pa:
            ui.label("Server Product *").classes(UIStyles.LABEL_FIELD)
            with ui.row().classes("w-full gap-3 flex-wrap"):
                for p in products:
                    pid = p["id"]
                    is_sel = form.get("product_id") == pid
                    tile = ui.element("div").classes(
                        f"flex flex-col items-start gap-1 p-3 {_tile_cls(selected=is_sel)}"
                    ).style("min-width:140px; max-width:220px")
                    with tile:
                        with ui.row().classes("items-center gap-2"):
                            ui.icon(p.get("icon", "inventory_2"), size="18px").classes(
                                _tile_icon_cls(selected=is_sel)
                            )
                            ui.label(p.get("label", pid)).classes("text-xs font-bold")
                        desc = (p.get("description") or "").strip().split("\n")[0][:80]
                        if desc:
                            ui.label(desc).classes("text-[10px] opacity-70 leading-tight")
                    tile.on("click", lambda _, _pid=pid: _on_product_change(_pid))

    def _render_product_extras_area() -> None:
        """Service-class sub-tiles + OS version tiles for the selected product."""
        ea = areas.get("extras")
        if ea is None:
            return
        ea.clear()
        pid = form.get("product_id") or ""
        if not pid:
            return
        products_cat = svc.catalog.products()
        scs = products_cat.get_service_classes(pid)
        os_vars = products_cat.get_os_variants(pid)
        if not scs and not os_vars:
            return

        with ea:
            if scs:
                ui.label("Service Class *").classes(UIStyles.LABEL_FIELD + " mt-2")
                with ui.row().classes("w-full gap-2 flex-wrap"):
                    for sc in scs:
                        sid = sc["id"]
                        is_sel = form.get("service_class_id") == sid
                        tile = ui.element("div").classes(
                            f"flex flex-col items-center justify-center gap-0.5 px-3 py-2 "
                            f"{_tile_cls(selected=is_sel)}"
                        ).style("min-width:96px").tooltip(sc.get("description", ""))
                        with tile:
                            with ui.row().classes("items-center gap-1"):
                                ui.icon(sc.get("icon", "verified"), size="16px").classes(
                                    _tile_icon_cls(selected=is_sel, entity_color=sc.get("color"))
                                )
                                ui.label(sc.get("label", sid)).classes(
                                    "text-xs font-bold leading-tight"
                                )
                            if sc.get("sub_label"):
                                ui.label(sc["sub_label"]).classes(
                                    "text-[10px] opacity-70 leading-tight"
                                )
                        if not is_sel:
                            tile.on("click", lambda _, _sid=sid: (
                                form.update({"service_class_id": _sid}),
                                _render_product_extras_area(),
                            ))

            if os_vars:
                cur_fam = next((f for f in os_vars if f["id"] == form.get("os_family_id")), None)
                if cur_fam and cur_fam.get("os_versions"):
                    ui.label("OS Version *").classes(UIStyles.LABEL_FIELD + " mt-2")
                    with ui.row().classes("gap-2 flex-wrap"):
                        for ver in cur_fam["os_versions"]:
                            vid = ver["id"]
                            is_sel = form.get("os_version_id") == vid
                            lc = ver.get("lifecycle", "supported")
                            lc_color = {
                                "preferred": "emerald",
                                "supported": "blue",
                                "exceptional_until": "amber",
                                "supported_for_legacy": "zinc",
                            }.get(lc, "zinc")
                            tip = lc.replace("_", " ")
                            if ver.get("end_of_support"):
                                tip += f" until {ver['end_of_support']}"
                            tile = ui.element("div").classes(
                                f"flex items-center gap-2 px-3 py-1.5 {_tile_cls(selected=is_sel)}"
                            ).tooltip(tip)
                            with tile:
                                ui.label(ver.get("label", vid)).classes("text-xs font-semibold")
                                ui.element("div").classes(
                                    f"w-1.5 h-1.5 rounded-full bg-{lc_color}-400"
                                )
                            if not is_sel:
                                tile.on("click", lambda _, _vid=vid: (
                                    form.update({"os_version_id": _vid}),
                                    _render_product_extras_area(),
                                ))

    # ── Provider section (re-renderable on provider change) ────────────────
    provider_section = ui.column().classes("w-full gap-3")

    def _render_provider_section() -> None:
        provider_section.clear()
        with provider_section:
            _render_provider_tiles()
            areas["stage"] = ui.column().classes("w-full gap-2")
            areas["type"] = ui.column().classes("w-full gap-2")
            areas["os"] = ui.column().classes("w-full gap-2")
            areas["product"] = ui.column().classes("w-full gap-2")
            areas["extras"] = ui.column().classes("w-full gap-1")
        _render_stage_area()
        _render_type_area()
        _render_os_area()
        _render_product_area()
        _render_product_extras_area()

    # Initial build
    with provider_section:
        _render_provider_tiles()
        areas["stage"] = ui.column().classes("w-full gap-2")
        areas["type"] = ui.column().classes("w-full gap-2")
        areas["os"] = ui.column().classes("w-full gap-2")
        areas["product"] = ui.column().classes("w-full gap-2")
        areas["extras"] = ui.column().classes("w-full gap-1")

    _render_stage_area()
    _render_type_area()
    _render_os_area()
    _render_product_area()
    _render_product_extras_area()

    ui.separator().classes("bg-slate-200 dark:bg-white/10")

    with ui.row().classes("w-full gap-4"):
        ui.select(
            label="Status",
            options={"": "All Statuses"} | {s["id"]: s["label"] for s in status_defs},
            value=form["status"],
        ).props(UIStyles.INPUT_PROPS).classes("flex-1") \
            .bind_value(form, "status")
        ui.input("Tags (comma-separated)").props(UIStyles.INPUT_PROPS) \
            .classes("flex-1").bind_value(form, "tags_raw")

    ui.textarea("Notes").props(f"{UIStyles.INPUT_PROPS} rows=2").classes("w-full") \
        .bind_value(form, "notes")
