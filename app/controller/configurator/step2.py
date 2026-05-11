"""Step 2 — Hardware Profile configuration (virtual & physical)."""
from __future__ import annotations

from nicegui import ui

from ..service import server_manager_service as svc


# ── Shared tile helper ────────────────────────────────────────────────────────

def _num_tile(val, current_val, on_select, suffix="") -> None:
    """Compact number tile used for sockets, RAM steps, vCPU, sizes."""
    is_sel = (int(current_val or 0) == int(val))
    border = "border-primary" if is_sel else "border-zinc-700 hover:border-zinc-500 cursor-pointer"
    bg = "bg-primary/10" if is_sel else "bg-zinc-900 hover:bg-zinc-800"
    text = "text-primary font-bold" if is_sel else "text-zinc-300 font-semibold"
    tile = ui.element("div").classes(
        f"flex items-center justify-center gap-0.5 px-3 py-2 rounded border "
        f"{border} {bg} select-none transition-colors text-xs {text}"
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

def render_step2(form: dict) -> None:  # noqa: C901
    """Dispatch to physical or virtual hardware renderer based on form state."""
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
    disk_count_options: list[int] = profiles_cat.get_disk_count_options(profile_id)
    ram_manual_max: int = profiles_cat.get_ram_manual_max(profile_id)
    vm_disk_size_steps: list[int] = profiles_cat.get_vm_disk_size_steps(profile_id)
    storage_manual_max: int = profiles_cat.get_storage_manual_max(profile_id)

    cpu_allowed = products_cat.get_allowed_ids(product_id, "cpu") if product_id else None
    storage_allowed = products_cat.get_allowed_ids(product_id, "storage") if product_id else None
    net_allowed = products_cat.get_allowed_ids(product_id, "network") if product_id else None

    ui.label("Hardware Profile").classes("text-base font-semibold text-zinc-200")
    if product_id:
        prod = products_cat.get(product_id)
        ui.label(f"Product: {(prod or {}).get('label', product_id)} \u00b7 {stype}") \
            .classes("text-xs text-zinc-400")
    else:
        ui.label(f"No product selected \u00b7 {stype}").classes("text-xs text-zinc-400")
    ui.separator().classes("border-zinc-800")

    if stype == "physical":
        _render_physical_hw(form, hw, cpu_allowed, storage_allowed, net_allowed,
                            ram_steps, multi_storage, socket_options,
                            disk_count_options, ram_manual_max, storage_manual_max)
    else:
        _render_virtual_hw(form, hw, storage_allowed, ram_steps, multi_storage,
                           vcpu_tiles, profile_id,
                           disk_count_options, ram_manual_max,
                           vm_disk_size_steps, storage_manual_max)


# ── Physical hardware ─────────────────────────────────────────────────────────

def _render_physical_hw(  # noqa: C901
    form: dict, hw, cpu_allowed, storage_allowed, net_allowed,
    ram_steps, multi_storage, socket_options,
    disk_count_options: list[int] | None = None,
    ram_manual_max: int = 8192,
    storage_manual_max: int = 131072,
) -> None:
    if disk_count_options is None:
        disk_count_options = [1, 2, 4, 6, 8]
    cpu_items = hw.get_cpu_options_for_product("physical", cpu_allowed)
    storage_items = hw.get_storage_options_for_product("physical", storage_allowed)
    net_items = hw.get_network_options_for_product("physical", net_allowed)

    # ── CPU ──────────────────────────────────────────────────────────────
    with ui.card().classes("w-full bg-zinc-900 border border-zinc-700 p-4 gap-3"):
        ui.label("CPU Model *").classes("text-sm font-semibold text-zinc-300")
        cpu_refresh_area = ui.column().classes("w-full gap-2")

        def _refresh_cpu() -> None:
            cpu_refresh_area.clear()
            with cpu_refresh_area:
                with ui.row().classes("w-full gap-3 flex-wrap"):
                    for cpu in cpu_items:
                        cid = cpu["id"]
                        is_sel = form.get("cpu_id") == cid
                        border = "border-primary" if is_sel else "border-zinc-700 hover:border-zinc-500 cursor-pointer"
                        bg = "bg-primary/10" if is_sel else "bg-zinc-900 hover:bg-zinc-800"
                        tc = "text-zinc-100" if is_sel else "text-zinc-300"
                        tile = ui.element("div").classes(
                            f"flex flex-col items-start gap-1 p-3 rounded border "
                            f"{border} {bg} select-none transition-colors"
                        ).style("min-width:160px")
                        with tile:
                            ui.label(cpu.get("label", cid)).classes(f"text-xs font-bold {tc} leading-tight")
                            with ui.row().classes("gap-2"):
                                cores = cpu.get("cores")
                                if cores:
                                    ui.label(f"{cores}C / {cpu.get('threads', cores*2)}T").classes("text-[10px] text-zinc-500")
                                tdp = cpu.get("tdp_w")
                                if tdp:
                                    ui.label(f"{tdp} W").classes("text-[10px] text-zinc-500")
                                ghz = cpu.get("base_ghz")
                                if ghz:
                                    ui.label(f"{ghz} GHz").classes("text-[10px] text-zinc-500")
                        if not is_sel:
                            tile.on("click", lambda _, _cid=cid: (
                                form.update({"cpu_id": _cid}),
                                _refresh_cpu(),
                            ))

        _refresh_cpu()

        ui.label("Sockets").classes("text-xs font-semibold text-zinc-400 mt-1")
        socket_row = ui.row().classes("gap-2")

        def _refresh_sockets() -> None:
            socket_row.clear()
            with socket_row:
                for s in socket_options:
                    _num_tile(s, form.get("cpu_count", 1),
                              lambda v: (form.update({"cpu_count": v}), _refresh_sockets()))

        _refresh_sockets()

    # ── RAM ───────────────────────────────────────────────────────────────
    with ui.card().classes("w-full bg-zinc-900 border border-zinc-700 p-4 gap-3"):
        ui.label("RAM (GB total) *").classes("text-sm font-semibold text-zinc-300")
        ram_row = ui.row().classes("gap-2 flex-wrap items-center")

        def _refresh_ram_tiles() -> None:
            ram_row.clear()
            with ram_row:
                for gb in ram_steps:
                    _num_tile(gb, form.get("ram_gb"),
                              lambda v: (form.update({"ram_gb": v}), _refresh_ram_tiles()))
                ui.element("div").classes("w-2")
                manual = ui.number(
                    label="manual",
                    value=int(form.get("ram_gb") or 0) or None,
                    min=1, max=ram_manual_max, step=16, format="%d",
                ).props("dense outlined dark suffix=GB").style("max-width:120px")
                def _on_manual_phys_ram(e):
                    try:
                        v = int(_event_value(e) or 0)
                    except Exception:
                        return
                    if v <= 0:
                        return
                    form["ram_gb"] = v
                    _refresh_ram_tiles()
                manual.on("blur", _on_manual_phys_ram)

        _refresh_ram_tiles()

    # ── Storage ───────────────────────────────────────────────────────────
    with ui.card().classes("w-full bg-zinc-900 border border-zinc-700 p-4 gap-3"):
        if multi_storage:
            ui.label("Storage (multiple product types allowed)") \
                .classes("text-sm font-semibold text-zinc-300")
        else:
            ui.label("Storage (one product type per server)") \
                .classes("text-sm font-semibold text-zinc-300")

        storage_disks: list[dict] = form.setdefault("storage_disks", [])
        disk_list = ui.column().classes("w-full gap-3")

        def _refresh_storage() -> None:
            disk_list.clear()
            if not multi_storage:
                used_types = {e.get("disk_id") for e in storage_disks if e.get("disk_id")}
                locked_type = next(iter(used_types), None)
            else:
                locked_type = None

            with disk_list:
                for idx, entry in enumerate(storage_disks):
                    with ui.card().classes(
                        "w-full bg-zinc-800/60 border border-zinc-700/80 p-3 gap-2"
                    ):
                        with ui.row().classes("w-full items-center justify-between gap-2"):
                            ui.input("Mount / Drive", value=entry.get("mount") or "") \
                                .props("dense outlined dark placeholder=/data or E:\\") \
                                .classes("flex-1") \
                                .on("change", lambda e, i=idx: (
                                    storage_disks.__setitem__(i, {**storage_disks[i], "mount": _event_value(e)}),
                                ))
                            ui.button(icon="delete",
                                      on_click=lambda _, i=idx: (
                                          storage_disks.pop(i), _refresh_storage()
                                      )).props("flat round dense size=xs color=red-4")

                        ui.label("Product").classes("text-[10px] text-zinc-500 font-semibold mt-1")
                        with ui.row().classes("gap-2 flex-wrap"):
                            for sp in storage_items:
                                sid = sp["id"]
                                is_sel = entry.get("disk_id") == sid
                                is_locked = (not multi_storage) and locked_type and sid != locked_type
                                sz = sp.get("size_gb") or 0
                                sz_label = (f"{sz//1024:.0f} TB" if sz >= 1024 else f"{sz} GB") if sz else ""
                                if is_locked:
                                    border = "border-zinc-800 opacity-40 cursor-not-allowed"
                                    bg = "bg-zinc-900/30"
                                    tc = "text-zinc-600"
                                elif is_sel:
                                    border = "border-primary"
                                    bg = "bg-primary/10"
                                    tc = "text-zinc-100"
                                else:
                                    border = "border-zinc-700 hover:border-zinc-500 cursor-pointer"
                                    bg = "bg-zinc-900 hover:bg-zinc-800"
                                    tc = "text-zinc-300"
                                tile = ui.element("div").classes(
                                    f"flex flex-col items-center justify-center gap-0.5 px-2 py-1.5 "
                                    f"rounded border {border} {bg} select-none transition-colors"
                                ).style("min-width:80px")
                                with tile:
                                    ui.label(sp.get("type", "")).classes("text-[10px] text-zinc-500")
                                    ui.label(sz_label).classes(f"text-xs font-bold {tc}")
                                if not is_locked and not is_sel:
                                    tile.on("click", lambda _, _sid=sid, i=idx: (
                                        storage_disks.__setitem__(i, {**storage_disks[i], "disk_id": _sid}),
                                        _refresh_storage(),
                                    ))

                        ui.label("Count").classes("text-[10px] text-zinc-500 font-semibold mt-1")
                        with ui.row().classes("gap-2"):
                            for cnt in disk_count_options:
                                _num_tile(cnt, entry.get("count", 1),
                                          lambda v, i=idx: (
                                              storage_disks.__setitem__(i, {**storage_disks[i], "count": v}),
                                              _refresh_storage(),
                                          ))

        _refresh_storage()

        def _add_storage() -> None:
            first_id = storage_items[0]["id"] if storage_items else ""
            storage_disks.append({"mount": "", "disk_id": first_id, "count": 1})
            form["storage_disks"] = storage_disks
            _refresh_storage()

        ui.button(icon="add", text="Add Storage", on_click=_add_storage) \
            .props("flat dense size=sm")

    # ── Network ───────────────────────────────────────────────────────────
    with ui.card().classes("w-full bg-zinc-900 border border-zinc-700 p-4 gap-3"):
        ui.label("Network Card *").classes("text-sm font-semibold text-zinc-300")
        net_refresh_area = ui.row().classes("w-full gap-3 flex-wrap")

        def _refresh_net() -> None:
            net_refresh_area.clear()
            with net_refresh_area:
                for nic in net_items:
                    nid = nic["id"]
                    is_sel = form.get("network_id") == nid
                    border = "border-primary" if is_sel else "border-zinc-700 hover:border-zinc-500 cursor-pointer"
                    bg = "bg-primary/10" if is_sel else "bg-zinc-900 hover:bg-zinc-800"
                    tc = "text-zinc-100" if is_sel else "text-zinc-300"
                    spd = nic.get("speed_gbps")
                    tile = ui.element("div").classes(
                        f"flex flex-col items-center justify-center gap-1 p-3 rounded border "
                        f"{border} {bg} select-none transition-colors"
                    ).style("min-width:90px; min-height:60px")
                    with tile:
                        ui.label(nic.get("label", nid)).classes(f"text-xs font-bold {tc} text-center leading-tight")
                        if spd:
                            ui.label(f"{spd} GbE").classes("text-[10px] text-zinc-500")
                    if not is_sel:
                        tile.on("click", lambda _, _nid=nid: (
                            form.update({"network_id": _nid}),
                            _refresh_net(),
                        ))

        _refresh_net()


# ── Virtual hardware ──────────────────────────────────────────────────────────

def _render_virtual_hw(  # noqa: C901
    form: dict, hw, storage_allowed, ram_steps, multi_storage, vcpu_tiles, profile_id,
    disk_count_options: list[int] | None = None,
    ram_manual_max: int = 8192,
    vm_disk_size_steps: list[int] | None = None,
    storage_manual_max: int = 131072,
) -> None:
    if disk_count_options is None:
        disk_count_options = [1, 2, 4, 6, 8]
    if vm_disk_size_steps is None:
        vm_disk_size_steps = [10, 20, 50, 100, 200, 500, 1000, 2000]
    """Virtual hardware UI: vCPU/RAM live matrix tiles + per-volume storage."""
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

    def _state_classes(state: str) -> tuple[str, str, str]:
        if state == "selected":
            return ("border-primary", "bg-primary/10", "text-primary font-bold")
        if state == "disabled":
            return ("border-zinc-800 opacity-40 cursor-not-allowed",
                    "bg-zinc-900/30", "text-zinc-600")
        if state == "special":
            return ("border-amber-500/60 hover:border-amber-400 cursor-pointer",
                    "bg-amber-900/20", "text-amber-300 font-semibold")
        return ("border-zinc-700 hover:border-zinc-500 cursor-pointer",
                "bg-zinc-900 hover:bg-zinc-800", "text-zinc-300 font-semibold")

    def _state_tile(label: str, state: str, tooltip: str, on_click) -> None:
        bd, bg, txt = _state_classes(state)
        tile = ui.element("div").classes(
            f"flex items-center justify-center px-3 py-2 rounded border "
            f"{bd} {bg} select-none transition-colors text-xs {txt}"
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
            ui.icon("info", size="14px").classes("text-blue-400")
            if os_family:
                ui.label(f"Matrix active for OS '{os_family}'").classes("text-[11px] text-blue-300")
            else:
                ui.label("Select an OS (step 1) to activate the RAM matrix.") \
                    .classes("text-[11px] text-amber-300")
            if special_max:
                ui.label(f"· Special sizing up to {special_max} GB").classes("text-[11px] text-zinc-500")
            if ram_step_for_product > 1:
                ui.label(f"· RAM step {ram_step_for_product} GB").classes("text-[11px] text-zinc-500")

    # ── vCPU + RAM: single container re-rendered together on any change ────
    hw_tiles = ui.column().classes("w-full gap-4")

    def _refresh_hw_tiles() -> None:
        hw_tiles.clear()
        with hw_tiles:
            # vCPU card
            with ui.card().classes("w-full bg-zinc-900 border border-zinc-700 p-4 gap-3"):
                ui.label("vCPU Count *").classes("text-sm font-semibold text-zinc-300")
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
                                _refresh_hw_tiles(),
                            ),
                        )
                    ui.element("div").classes("w-2")
                    manual_v = ui.number(
                        label="vCPU",
                        value=int(form.get("vcpu_count") or 0) or None,
                        min=1, max=512, step=1, format="%d",
                    ).props("dense outlined dark").style("max-width:90px")
                    def _on_vcpu(e):
                        try:
                            v = int(_event_value(e) or 0)
                        except Exception:
                            return
                        if v > 0:
                            form["vcpu_count"] = v
                            _refresh_hw_tiles()
                    manual_v.on("blur", _on_vcpu)

            # RAM card
            with ui.card().classes("w-full bg-zinc-900 border border-zinc-700 p-4 gap-3"):
                ui.label("RAM (GB) *").classes("text-sm font-semibold text-zinc-300")
                ram_hint = ui.label("").classes("text-[10px] text-zinc-500")
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
                                _refresh_hw_tiles(),
                            ),
                        )
                    ui.element("div").classes("w-2")
                    manual_r = ui.number(
                        label="GB",
                        value=int(form.get("ram_gb") or 0) or None,
                        min=1, max=ram_manual_max,
                        step=ram_step_for_product or 1,
                        format="%d",
                    ).props("dense outlined dark suffix=GB").style("max-width:120px")
                    def _on_ram(e):
                        try:
                            v = int(_event_value(e) or 0)
                        except Exception:
                            return
                        if v > 0:
                            form["ram_gb"] = v
                            _refresh_hw_tiles()
                    manual_r.on("blur", _on_ram)

                if has_matrix and os_family and form.get("vcpu_count") and form.get("ram_gb"):
                    verdict = products_cat.evaluate_combo(
                        product_id, os_family,
                        int(form["vcpu_count"]), int(form["ram_gb"]),
                    )
                    col = {"ok": "text-emerald-400", "special": "text-amber-400",
                           "invalid": "text-red-400"}.get(verdict["status"], "text-zinc-500")
                    rng_txt = ""
                    if verdict["status"] == "ok" and verdict.get("ram_min") is not None:
                        rng_txt = f" (allowed {verdict['ram_min']}–{verdict['ram_max']} GB)"
                    ram_hint.text = (
                        ("✓ matches matrix" + rng_txt) if verdict["status"] == "ok"
                        else verdict["message"]
                    )
                    ram_hint.classes(replace=f"text-[10px] {col}")

    _refresh_hw_tiles()

    # ── Storage volumes ────────────────────────────────────────────────────
    with ui.card().classes("w-full bg-zinc-900 border border-zinc-700 p-4 gap-3"):
        ui.label("Storage Volumes").classes("text-sm font-semibold text-zinc-300")
        storage_disks: list[dict] = form.setdefault("storage_disks", [])
        disk_list = ui.column().classes("w-full gap-3")
        vm_size_steps = vm_disk_size_steps

        def _vol_size_label(sz: int) -> str:
            return (f"{sz // 1000} TB" if sz >= 1000 and sz % 1000 == 0 else f"{sz} GB")

        def _make_size_refresh(v_idx: int, size_area):
            """Returns a closure that refreshes ONLY the size tiles for one volume."""
            def _do() -> None:
                size_area.clear()
                entry = storage_disks[v_idx]
                with size_area:
                    for sz in vm_size_steps:
                        lbl = _vol_size_label(sz)
                        is_sel = entry.get("size_gb") == sz
                        bd = ("border-primary" if is_sel
                              else "border-zinc-700 hover:border-zinc-500 cursor-pointer")
                        bgg = "bg-primary/10" if is_sel else "bg-zinc-900 hover:bg-zinc-800"
                        txc = "text-primary font-bold" if is_sel else "text-zinc-300 font-semibold"
                        tile = ui.element("div").classes(
                            f"flex items-center justify-center px-2 py-1.5 rounded border "
                            f"{bd} {bgg} select-none transition-colors text-xs {txc}"
                        ).style("min-width:52px")
                        with tile:
                            ui.label(lbl)
                        if not is_sel:
                            tile.on("click", lambda _, _sz=sz: (
                                storage_disks.__setitem__(v_idx,
                                    {**storage_disks[v_idx], "size_gb": _sz}),
                                _do(),
                            ))
                    ui.element("div").classes("w-1")
                    manual_s = ui.number(
                        label="GB",
                        value=int(entry.get("size_gb") or 0) or None,
                        min=1, max=storage_manual_max, step=1, format="%d",
                    ).props("dense outlined dark suffix=GB").style("max-width:110px")
                    def _on_sz(e, _i=v_idx, _fn=_do):
                        try:
                            v = int(_event_value(e) or 0)
                        except Exception:
                            return
                        if v > 0:
                            storage_disks[_i]["size_gb"] = v
                            _fn()
                    manual_s.on("blur", _on_sz)
            return _do

        def _refresh_vm_storage() -> None:
            disk_list.clear()
            with disk_list:
                for idx, entry in enumerate(storage_disks):
                    with ui.card().classes(
                        "w-full bg-zinc-800/60 border border-zinc-700/80 p-3 gap-2"
                    ):
                        with ui.row().classes("w-full items-center justify-between"):
                            ui.label(f"Volume {idx + 1}").classes(
                                "text-xs font-semibold text-zinc-400"
                            )
                            ui.button(
                                icon="delete",
                                on_click=lambda _, i=idx: (
                                    storage_disks.pop(i), _refresh_vm_storage()
                                ),
                            ).props("flat round dense size=xs color=red-4")

                        if len(storage_items) > 1:
                            ui.label("Storage Product").classes(
                                "text-[10px] text-zinc-500 font-semibold"
                            )
                            dt_area = ui.row().classes("gap-2 flex-wrap")

                            def _make_dt_refresh(i, dta):
                                def _dt():
                                    dta.clear()
                                    cur_dt = storage_disks[i].get("disk_id") or (
                                        storage_items[0]["id"] if storage_items else ""
                                    )
                                    with dta:
                                        for sp in storage_items:
                                            sid = sp["id"]
                                            is_s = cur_dt == sid
                                            bd = ("border-primary" if is_s
                                                  else "border-zinc-700 hover:border-zinc-500 cursor-pointer")
                                            bgg = "bg-primary/10" if is_s else "bg-zinc-900 hover:bg-zinc-800"
                                            tc = "text-zinc-100 font-bold" if is_s else "text-zinc-300"
                                            tile = ui.element("div").classes(
                                                f"flex flex-col items-center justify-center gap-0.5 "
                                                f"px-3 py-2 rounded border {bd} {bgg} "
                                                f"select-none transition-colors"
                                            ).style("min-width:80px")
                                            with tile:
                                                ui.label(sp.get("label", sid)).classes(
                                                    f"text-xs {tc} leading-tight"
                                                )
                                            if not is_s:
                                                tile.on("click", lambda _, _sid=sid, _i=i, _fn=_dt: (
                                                    storage_disks.__setitem__(
                                                        _i, {**storage_disks[_i], "disk_id": _sid}
                                                    ),
                                                    _fn(),
                                                ))
                                return _dt

                            _make_dt_refresh(idx, dt_area)()
                        else:
                            if storage_items:
                                sp = storage_items[0]
                                if entry.get("disk_id") != sp["id"]:
                                    storage_disks[idx]["disk_id"] = sp["id"]
                                ui.label(sp.get("label", sp["id"])).classes(
                                    "text-[10px] text-zinc-400"
                                )

                        is_win = (form.get("os_family_id") or "").upper() == "WIN"
                        ph = "e.g. D:\\ or E:\\" if is_win else "e.g. / or /data"
                        ui.label("Partition" if is_win else "Mount Point").classes(
                            "text-[10px] text-zinc-500 font-semibold mt-1"
                        )
                        ui.input(
                            value=entry.get("mount") or "",
                            on_change=lambda e, i=idx: storage_disks.__setitem__(
                                i, {**storage_disks[i], "mount": _event_value(e)}
                            ),
                        ).props(f'dense outlined dark placeholder="{ph}"').classes("w-full")

                        ui.label("Size").classes(
                            "text-[10px] text-zinc-500 font-semibold mt-1"
                        )
                        size_area = ui.row().classes("gap-2 flex-wrap items-center")
                        _make_size_refresh(idx, size_area)()

        _refresh_vm_storage()

        def _add_vm_volume() -> None:
            cur_disk = next(
                (e.get("disk_id") for e in storage_disks if e.get("disk_id")),
                storage_items[0]["id"] if storage_items else "",
            )
            storage_disks.append({"mount": "", "disk_id": cur_disk, "size_gb": 50})
            form["storage_disks"] = storage_disks
            _refresh_vm_storage()

        ui.button(icon="add", text="Add Volume", on_click=_add_vm_volume) \
            .props("flat dense size=sm")
