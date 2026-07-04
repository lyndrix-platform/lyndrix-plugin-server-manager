"""Step 2 — Hardware Profile configuration (virtual & physical)."""
from __future__ import annotations

from nicegui import ui
from core.api import UIStyles

from ..service import server_manager_service as svc


# ── Shared tile helper ────────────────────────────────────────────────────────

def _num_tile(val, current_val, on_select, suffix="") -> None:
    """Compact number tile used for sockets, RAM steps, vCPU, sizes."""
    is_sel = (int(current_val or 0) == int(val))
    state = UIStyles.TILE_SELECTED if is_sel else UIStyles.TILE_DEFAULT
    tile = ui.element("div").classes(
        f"flex items-center justify-center gap-0.5 px-3 py-2 text-xs font-semibold "
        f"{UIStyles.TILE_BASE} {state}"
    ).style("min-width:48px; min-height:34px")
    with tile:
        ui.label(f"{val}{suffix}")
    if not is_sel:
        tile.on("click", lambda _: on_select(val))


def _event_value(e):
    """Read a value from NiceGUI event args across versions/components."""
    if hasattr(e, "value"):
        return e.value
    args = getattr(e, "args", None)
    if isinstance(args, dict):
        return args.get("value") or args.get("modelValue")
    if isinstance(args, list):
        return args[0] if args else None
    return args


# ── Step entry-point ──────────────────────────────────────────────────────────

def render_step2(form: dict, on_change=lambda: None) -> None:  # noqa: C901
    """Dispatch to physical or virtual hardware renderer based on form state.

    ``on_change`` is invoked after every hardware mutation so the side-panel rule
    validation re-evaluates live (not only on step navigation / save).
    """
    stype = form.get("server_type", "physical")
    env_id = form.get("environment_id") or ""
    profile_id = svc.catalog.environments().get_profile_id(env_id) if env_id else "edc"
    product_id = form.get("product_id") or None

    hw = svc.catalog.hardware()
    products_cat = svc.catalog.products()
    profiles_cat = svc.catalog.profiles()

    ram_steps: list[int] = profiles_cat.get_ram_steps(profile_id)
    multi_storage: bool = profiles_cat.multi_storage_allowed(profile_id)
    socket_options: list[int] = profiles_cat.get_socket_options(profile_id)
    vcpu_tiles: list[int] = profiles_cat.get_vcpu_tiles(profile_id)
    ram_manual_max: int = profiles_cat.get_ram_manual_max(profile_id)
    storage_manual_max: int = profiles_cat.get_storage_manual_max(profile_id)

    cpu_allowed = products_cat.get_allowed_ids(product_id, "cpu") if product_id else None
    storage_allowed = products_cat.get_allowed_ids(product_id, "storage") if product_id else None

    ui.label("Hardware Profile").classes(UIStyles.TITLE_H3)
    if product_id:
        prod = products_cat.get(product_id)
        ui.label(f"Product: {(prod or {}).get('label', product_id)} · {stype}") \
            .classes(UIStyles.TEXT_MUTED)
    else:
        ui.label(f"No product selected · {stype}").classes(UIStyles.TEXT_MUTED)
    ui.separator().classes("bg-[var(--lx-border-soft)]")

    if stype == "physical":
        _render_physical_hw(form, hw, cpu_allowed, storage_allowed,
                            ram_steps, multi_storage, socket_options,
                            ram_manual_max, storage_manual_max, on_change)
    else:
        _render_virtual_hw(form, hw, storage_allowed, ram_steps, multi_storage,
                           vcpu_tiles, profile_id, ram_manual_max,
                           storage_manual_max, on_change)


# ── Unified storage section ───────────────────────────────────────────────────

def _render_storage(  # noqa: C901
    form: dict, storage_items, multi_storage: bool, on_change, storage_manual_max: int
) -> None:
    """Storage is ordered as a PRODUCT in an amount (GB) per line — no disk sizes.

    multi_storage=True  (EDC): several products, each line picks its own product
                               (product + mount + amount GB).
    multi_storage=False (FCE): exactly one product, split into one or more
                               partitions / drive letters (mount + amount GB);
                               Windows is capped at 4 (also enforced by the rule).
    """
    with ui.card().classes(UIStyles.CARD_COMPACT + " w-full gap-3"):
        if multi_storage:
            ui.label("Storage — multiple products allowed") \
                .classes(UIStyles.LABEL_HEADING)
        else:
            ui.label("Storage — one product per server") \
                .classes(UIStyles.LABEL_HEADING)

        if not storage_items:
            ui.label("No storage products available for this selection.") \
                .classes(UIStyles.TEXT_HINT + " italic")
            return

        is_win = (form.get("os_type") or "").lower() == "windows" \
            or (form.get("os_family_id") or "").upper() == "WIN"
        storage_disks: list[dict] = form.setdefault("storage_disks", [])
        body = ui.column().classes("w-full gap-3")

        def _shared_product() -> str:
            for e in storage_disks:
                if e.get("disk_id"):
                    return e["disk_id"]
            return storage_items[0]["id"]

        def _commit_amount(idx: int, el) -> None:
            # Read the element's own value — blur/enter events do not carry it.
            try:
                v = int(el.value or 0)
            except (TypeError, ValueError):
                return
            if v > 0:
                storage_disks[idx] = {**storage_disks[idx], "size_gb": v}
                on_change()

        def _product_tile(sp: dict, is_sel: bool, on_pick) -> None:
            state = UIStyles.TILE_SELECTED if is_sel else UIStyles.TILE_DEFAULT
            tile = ui.element("div").classes(
                f"flex flex-col items-start gap-0.5 px-3 py-2 {UIStyles.TILE_BASE} {state}"
            ).style("min-width:150px").tooltip(sp.get("description", ""))
            with tile:
                ui.label(sp.get("label", sp["id"])).classes("text-xs font-bold leading-tight")
                if sp.get("type"):
                    ui.label(sp["type"]).classes("text-[10px] opacity-70")
            if not is_sel:
                tile.on("click", lambda _: on_pick())

        def _refresh() -> None:
            body.clear()
            with body:
                # FCE single-product: choose the product once for all partitions.
                if not multi_storage:
                    cur = _shared_product()
                    ui.label("Storage Product").classes(UIStyles.LABEL_MINI)
                    with ui.row().classes("gap-2 flex-wrap"):
                        for sp in storage_items:
                            def _pick(_sid=sp["id"]) -> None:
                                if storage_disks:
                                    for i in range(len(storage_disks)):
                                        storage_disks[i] = {**storage_disks[i], "disk_id": _sid}
                                else:
                                    storage_disks.append({"disk_id": _sid, "mount": "", "size_gb": 0})
                                _refresh()
                                on_change()
                            _product_tile(sp, cur == sp["id"], _pick)

                row_word = "Partition" if (not multi_storage and is_win) else "Volume"
                for idx, entry in enumerate(storage_disks):
                    with ui.card().classes(
                        UIStyles.PANEL_SUBTLE + " w-full p-3 gap-2 rounded-[var(--lx-radius-lg)]"
                    ):
                        with ui.row().classes("w-full items-center justify-between gap-2"):
                            ui.label(f"{row_word} {idx + 1}").classes(UIStyles.LABEL_FIELD)
                            ui.button(icon="delete", on_click=lambda _, i=idx: (
                                storage_disks.pop(i), _refresh(), on_change(),
                            )).props("flat round dense size=xs color=negative")

                        # Per-line product picker for multi-product (EDC) profiles.
                        if multi_storage:
                            ui.label("Product").classes(UIStyles.LABEL_MINI + " mt-1")
                            with ui.row().classes("gap-2 flex-wrap"):
                                for sp in storage_items:
                                    def _pick_line(_sid=sp["id"], i=idx) -> None:
                                        storage_disks[i] = {**storage_disks[i], "disk_id": _sid}
                                        _refresh()
                                        on_change()
                                    _product_tile(sp, entry.get("disk_id") == sp["id"], _pick_line)

                        with ui.row().classes("w-full items-center gap-3"):
                            ph = "C:\\ or D:\\" if is_win else "/ or /data"
                            mount_label = "Drive letter" if is_win else "Mount point"
                            ui.input(mount_label, value=entry.get("mount") or "") \
                                .props(f'{UIStyles.INPUT_PROPS} placeholder="{ph}"') \
                                .classes("flex-1") \
                                .on("change", lambda e, i=idx: storage_disks.__setitem__(
                                    i, {**storage_disks[i], "mount": _event_value(e)}))
                            amt = ui.number(
                                label="Menge", value=int(entry.get("size_gb") or 0) or None,
                                min=1, max=storage_manual_max, step=10, format="%d",
                            ).props(f"{UIStyles.INPUT_PROPS} suffix=GB").style("max-width:140px")
                            amt.on("blur", lambda _, i=idx, el=amt: _commit_amount(i, el))
                            amt.on("keydown.enter", lambda _, i=idx, el=amt: _commit_amount(i, el))

                # Add line / partition — capped at 4 partitions for FCE Windows.
                at_limit = (not multi_storage) and is_win and len(storage_disks) >= 4
                if at_limit:
                    ui.label("Maximum 4 partitions reached.").classes(UIStyles.STATUS_TEXT_WARNING)
                else:
                    add_word = "Add Partition" if (not multi_storage and is_win) else "Add Storage"

                    def _add() -> None:
                        did = _shared_product() if not multi_storage else storage_items[0]["id"]
                        storage_disks.append({"disk_id": did, "mount": "", "size_gb": 0})
                        _refresh()
                        on_change()
                    ui.button(icon="add", text=add_word, on_click=_add).props("flat dense size=sm")

        _refresh()


# ── Physical hardware ─────────────────────────────────────────────────────────

def _render_physical_hw(  # noqa: C901
    form: dict, hw, cpu_allowed, storage_allowed,
    ram_steps, multi_storage, socket_options,
    ram_manual_max: int = 8192,
    storage_manual_max: int = 131072,
    on_change=lambda: None,
) -> None:
    cpu_items = hw.get_cpu_options_for_product("physical", cpu_allowed)
    storage_items = hw.get_storage_options_for_product("physical", storage_allowed)

    # ── CPU ──────────────────────────────────────────────────────────────
    with ui.card().classes(UIStyles.CARD_COMPACT + " w-full gap-3"):
        ui.label("CPU Model *").classes(UIStyles.LABEL_HEADING)
        cpu_refresh_area = ui.column().classes("w-full gap-2")

        def _refresh_cpu() -> None:
            cpu_refresh_area.clear()
            with cpu_refresh_area:
                with ui.row().classes("w-full gap-3 flex-wrap"):
                    for cpu in cpu_items:
                        cid = cpu["id"]
                        is_sel = form.get("cpu_id") == cid
                        state = UIStyles.TILE_SELECTED if is_sel else UIStyles.TILE_DEFAULT
                        tile = ui.element("div").classes(
                            f"flex flex-col items-start gap-1 p-3 {UIStyles.TILE_BASE} {state}"
                        ).style("min-width:160px")
                        with tile:
                            ui.label(cpu.get("label", cid)).classes("text-xs font-bold leading-tight")
                            with ui.row().classes("gap-2 opacity-70"):
                                cores = cpu.get("cores")
                                if cores:
                                    ui.label(f"{cores}C / {cpu.get('threads', cores*2)}T").classes("text-[10px]")
                                tdp = cpu.get("tdp_w")
                                if tdp:
                                    ui.label(f"{tdp} W").classes("text-[10px]")
                                ghz = cpu.get("base_ghz")
                                if ghz:
                                    ui.label(f"{ghz} GHz").classes("text-[10px]")
                            if cpu.get("server_model"):
                                ui.label(cpu["server_model"]).classes("text-[10px] opacity-60 italic")
                        if not is_sel:
                            tile.on("click", lambda _, _cid=cid: (
                                form.update({"cpu_id": _cid}),
                                _refresh_cpu(),
                            ))
            on_change()

        _refresh_cpu()

        ui.label("Sockets").classes(UIStyles.LABEL_FIELD + " mt-1")
        socket_row = ui.row().classes("gap-2")

        def _refresh_sockets() -> None:
            socket_row.clear()
            with socket_row:
                for s in socket_options:
                    _num_tile(s, form.get("cpu_count", 1),
                              lambda v: (form.update({"cpu_count": v}), _refresh_sockets()))
            on_change()

        _refresh_sockets()

    # ── RAM ───────────────────────────────────────────────────────────────
    with ui.card().classes(UIStyles.CARD_COMPACT + " w-full gap-3"):
        ui.label("RAM (GB total) *").classes(UIStyles.LABEL_HEADING)
        ram_row = ui.row().classes("gap-2 flex-wrap items-center")

        def _refresh_ram_tiles() -> None:
            ram_row.clear()
            with ram_row:
                for gb in ram_steps:
                    _num_tile(gb, form.get("ram_gb"),
                              lambda v: (form.update({"ram_gb": v}),
                                         _refresh_ram_tiles(), on_change()))
                ui.element("div").classes("w-2")
                manual = ui.number(
                    label="manual",
                    value=int(form.get("ram_gb") or 0) or None,
                    min=1, max=ram_manual_max, step=16, format="%d",
                ).props(f"{UIStyles.INPUT_PROPS} suffix=GB").style("max-width:120px")
                def _commit_manual_phys_ram():
                    try:
                        v = int(manual.value or 0)
                    except (TypeError, ValueError):
                        return
                    if v <= 0:
                        return
                    form["ram_gb"] = v
                    _refresh_ram_tiles()
                    on_change()
                manual.on("blur", lambda _: _commit_manual_phys_ram())
                manual.on("keydown.enter", lambda _: _commit_manual_phys_ram())

        _refresh_ram_tiles()

    # ── Storage ───────────────────────────────────────────────────────────
    _render_storage(form, storage_items, multi_storage, on_change, storage_manual_max)


# ── Virtual hardware ──────────────────────────────────────────────────────────

def _render_virtual_hw(  # noqa: C901
    form: dict, hw, storage_allowed, ram_steps, multi_storage, vcpu_tiles, profile_id,
    ram_manual_max: int = 8192,
    storage_manual_max: int = 131072,
    on_change=lambda: None,
) -> None:
    """Virtual hardware UI: vCPU/RAM live matrix tiles + per-product storage."""
    storage_items = hw.get_storage_options_for_product("vm", storage_allowed)
    products_cat = svc.catalog.products()
    product_id = form.get("product_id") or None
    os_family = form.get("os_family_id") or None

    has_matrix = bool(product_id and products_cat.get_vcpu_ram_matrix(product_id))
    allowed_vcpus = products_cat.allowed_vcpu_values(product_id) if has_matrix else set()
    ram_step_for_product = products_cat.get_ram_step(product_id) if product_id else 1
    special_max = products_cat.get_ram_special_max(product_id) if product_id else None

    vcpu_tile_values = list(vcpu_tiles)
    if has_matrix:
        extras = sorted(v for v in allowed_vcpus if v not in vcpu_tile_values)
        vcpu_tile_values = sorted(set(vcpu_tile_values + extras[:8]))

    # ── State helpers ──────────────────────────────────────────────────────
    def _vcpu_state(v: int) -> str:
        if not has_matrix:
            return "selected" if form.get("vcpu_count") == v else "normal"
        if form.get("vcpu_count") == v:
            return "selected"
        if not products_cat.vcpu_allowed_for_os(product_id, v, os_family or ""):
            return "disabled"
        return "normal"

    def _ram_state(gb: int) -> str:
        if form.get("ram_gb") == gb:
            return "selected"
        if not has_matrix or not os_family or not form.get("vcpu_count"):
            return "normal"
        rng = products_cat.ram_range_for(product_id, int(form["vcpu_count"]), os_family)
        if not rng:
            return "disabled"
        rmin, rmax = rng
        if rmin <= gb <= rmax:
            if ram_step_for_product > 1 and gb % ram_step_for_product != 0:
                return "disabled"
            return "normal"
        if special_max and rmax < gb <= special_max:
            return "special"
        return "disabled"

    def _state_classes(state: str) -> str:
        return {
            "selected": UIStyles.TILE_SELECTED,
            "disabled": UIStyles.TILE_DISABLED,
            "special": UIStyles.TILE_WARNING,
        }.get(state, UIStyles.TILE_DEFAULT)

    def _state_tile(label: str, state: str, tooltip: str, on_click) -> None:
        tile = ui.element("div").classes(
            f"flex items-center justify-center px-3 py-2 text-xs font-semibold "
            f"{UIStyles.TILE_BASE} {_state_classes(state)}"
        ).style("min-width:48px; min-height:34px")
        if tooltip:
            tile.tooltip(tooltip)
        with tile:
            ui.label(label)
        if state != "disabled":  # "selected" tiles are also clickable (allows deselect)
            tile.on("click", lambda _: on_click())

    # ── Header badges ──────────────────────────────────────────────────────
    if has_matrix:
        with ui.row().classes("items-center gap-2 flex-wrap"):
            ui.icon("info", size="14px").classes(UIStyles.ICON_INFO)
            if os_family:
                ui.label(f"Matrix active for OS '{os_family}'").classes(UIStyles.TEXT_HINT)
            else:
                ui.label("Select an OS (step 1) to activate the RAM matrix.") \
                    .classes(UIStyles.STATUS_TEXT_WARNING)
            if special_max:
                ui.label(f"· Special sizing up to {special_max} GB").classes(UIStyles.STATUS_TEXT_NEUTRAL)
            if ram_step_for_product > 1:
                ui.label(f"· RAM step {ram_step_for_product} GB").classes(UIStyles.STATUS_TEXT_NEUTRAL)

    # ── vCPU + RAM: single container re-rendered together on any change ────
    hw_tiles = ui.column().classes("w-full gap-4")

    def _refresh_hw_tiles() -> None:
        hw_tiles.clear()
        with hw_tiles:
            # vCPU card
            with ui.card().classes(UIStyles.CARD_COMPACT + " w-full gap-3"):
                ui.label("vCPU Count *").classes(UIStyles.LABEL_HEADING)
                with ui.row().classes("gap-2 flex-wrap items-center"):
                    for v in vcpu_tile_values:
                        tip = ""
                        if has_matrix and os_family:
                            rng = products_cat.ram_range_for(product_id, v, os_family)
                            tip = (f"{v} vCPU → RAM {rng[0]}–{rng[1]} GB"
                                   if rng else f"{v} vCPU not allowed for {os_family}")
                        target = None if form.get("vcpu_count") == v else v
                        _state_tile(
                            str(v), _vcpu_state(v), tip,
                            lambda _t=target: (
                                form.update({"vcpu_count": _t}),
                                _refresh_hw_tiles(), on_change(),
                            ),
                        )
                    ui.element("div").classes("w-2")
                    manual_v = ui.number(
                        label="vCPU",
                        value=int(form.get("vcpu_count") or 0) or None,
                        min=1, max=512, step=1, format="%d",
                    ).props(UIStyles.INPUT_PROPS).style("max-width:90px")
                    def _commit_vcpu(_v=manual_v):
                        try:
                            v = int(_v.value or 0)
                        except (TypeError, ValueError):
                            return
                        if v > 0:
                            form["vcpu_count"] = v
                            _refresh_hw_tiles()
                            on_change()
                    manual_v.on("blur", lambda _: _commit_vcpu())
                    manual_v.on("keydown.enter", lambda _: _commit_vcpu())

            # RAM card
            with ui.card().classes(UIStyles.CARD_COMPACT + " w-full gap-3"):
                ui.label("RAM (GB) *").classes(UIStyles.LABEL_HEADING)
                ram_hint = ui.label("").classes(UIStyles.STATUS_TEXT_NEUTRAL)
                with ui.row().classes("gap-2 flex-wrap items-center"):
                    tile_gblist = sorted(set(
                        list(ram_steps) + ([special_max] if special_max else [])
                    ))
                    for gb in tile_gblist:
                        lbl = (f"{gb // 1000} TB"
                               if gb >= 1000 and gb % 1000 == 0
                               else f"{gb} GB")
                        state = _ram_state(gb)
                        tip = ""
                        if state == "special":
                            tip = "Special sizing approval required."
                        elif (state == "disabled" and has_matrix
                              and os_family and form.get("vcpu_count")):
                            rng = products_cat.ram_range_for(
                                product_id, int(form["vcpu_count"]), os_family
                            )
                            if rng:
                                tip = f"Outside allowed range {rng[0]}–{rng[1]} GB."
                        target_r = None if form.get("ram_gb") == gb else gb
                        _state_tile(
                            lbl, state, tip,
                            lambda _t=target_r: (
                                form.update({"ram_gb": _t}),
                                _refresh_hw_tiles(), on_change(),
                            ),
                        )
                    ui.element("div").classes("w-2")
                    manual_r = ui.number(
                        label="GB",
                        value=int(form.get("ram_gb") or 0) or None,
                        min=1, max=ram_manual_max,
                        step=ram_step_for_product or 1,
                        format="%d",
                    ).props(f"{UIStyles.INPUT_PROPS} suffix=GB").style("max-width:120px")
                    def _commit_ram(_v=manual_r):
                        try:
                            v = int(_v.value or 0)
                        except (TypeError, ValueError):
                            return
                        if v > 0:
                            form["ram_gb"] = v
                            _refresh_hw_tiles()
                            on_change()
                    manual_r.on("blur", lambda _: _commit_ram())
                    manual_r.on("keydown.enter", lambda _: _commit_ram())

                if has_matrix and os_family and form.get("vcpu_count") and form.get("ram_gb"):
                    verdict = products_cat.evaluate_combo(
                        product_id, os_family,
                        int(form["vcpu_count"]), int(form["ram_gb"]),
                    )
                    cls = {"ok": UIStyles.STATUS_TEXT_SUCCESS,
                           "special": UIStyles.STATUS_TEXT_WARNING,
                           "invalid": UIStyles.STATUS_TEXT_ERROR}.get(
                        verdict["status"], UIStyles.STATUS_TEXT_NEUTRAL)
                    rng_txt = ""
                    if verdict["status"] == "ok" and verdict.get("ram_min") is not None:
                        rng_txt = f" (allowed {verdict['ram_min']}–{verdict['ram_max']} GB)"
                    ram_hint.text = (
                        ("✓ matches matrix" + rng_txt) if verdict["status"] == "ok"
                        else verdict["message"]
                    )
                    ram_hint.classes(replace=cls)

    _refresh_hw_tiles()

    # ── Storage ────────────────────────────────────────────────────────────
    _render_storage(form, storage_items, multi_storage, on_change, storage_manual_max)
