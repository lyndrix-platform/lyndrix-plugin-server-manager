"""Step 3 — Review & Save summary."""
from __future__ import annotations

from nicegui import ui
from core.api import UIStyles

from ..service import server_manager_service as svc
from .helpers import _collect_form


def render_step3(form: dict) -> None:
    """Render step 3: read-only summary of all configured values."""
    ui.label("Review & Save").classes(UIStyles.TITLE_H3)
    data = _collect_form(form)
    hw = svc.catalog.hardware()
    envs = svc.catalog.environments()
    env = envs.get(data.get("environment_id") or "") or {}
    profile_id = envs.get_profile_id(data.get("environment_id") or "")
    profile = svc.catalog.profiles().get(profile_id) or {}
    prod = svc.catalog.products().get(data.get("product_id") or "") or {}

    with ui.card().classes(UIStyles.CARD_COMPACT + " w-full gap-2"):
        rows = [
            ("Name", data.get("name")),
            ("Hostname", data.get("hostname") or "—"),
            ("Environment", env.get("label", data.get("environment_id"))),
            ("Profile", profile.get("label", profile_id)),
            ("Provider", env.get("provider_label", "—")),
            ("Order workflow", env.get("order_workflow") or "none"),
            ("Product", prod.get("label", data.get("product_id") or "—")),
        ]
        sc_id = data.get("service_class_id")
        if sc_id:
            sc = svc.catalog.products().get_service_class(
                data.get("product_id") or "", sc_id
            ) or {}
            sc_label = f"{sc.get('label', sc_id)} — {sc.get('sub_label', '')}".rstrip(" —")
            rows.append(("Service Class", sc_label))
        fam_id = data.get("os_family_id")
        ver_id = data.get("os_version_id")
        if fam_id:
            fam = svc.catalog.products().get_os_variant(
                data.get("product_id") or "", fam_id
            ) or {}
            rows.append(("OS Family", fam.get("label", fam_id)))
        if ver_id:
            ver = svc.catalog.products().get_os_version(
                data.get("product_id") or "", fam_id or "", ver_id
            ) or {}
            lc = ver.get("lifecycle", "supported").replace("_", " ")
            eos = f" (until {ver['end_of_support']})" if ver.get("end_of_support") else ""
            rows.append(("OS Version", f"{ver.get('label', ver_id)} — {lc}{eos}"))
        rows.extend([
            ("Type", data.get("server_type")),
            ("Status", data.get("status")),
            ("Tags", ", ".join(data.get("tags") or []) or "—"),
        ])
        hp = data.get("hardware_profile") or {}
        stype = data.get("server_type")
        if stype == "physical":
            cpu = hw.get_item(hp.get("cpu_id") or "")
            rows.append(("CPU", f"{hp.get('cpu_count', 1)}× {cpu['label']}" if cpu else "—"))
            ram_gb = hp.get("ram_gb") or 0
            rows.append(("RAM", f"{ram_gb} GB" if ram_gb else "—"))
        else:
            rows.extend([
                ("vCPU", str(hp.get("vcpu_count") or "—")),
                ("RAM", f"{hp.get('ram_gb', '—')} GB"),
            ])

        for label, value in rows:
            with ui.row().classes("w-full justify-between gap-4"):
                ui.label(label).classes(UIStyles.LABEL_FIELD + " w-36 shrink-0")
                ui.label(str(value or "")).classes("text-xs text-slate-800 dark:text-zinc-100")

        storage_vols = hp.get("storage_disks") or []
        if storage_vols:
            ui.separator().classes("bg-slate-200 dark:bg-white/10 my-1")
            ui.label("Storage").classes(UIStyles.LABEL_FIELD)
            total_gb = 0
            for vol in storage_vols:
                item = hw.get_item(vol.get("disk_id") or "")
                sz = int(vol.get("size_gb") or 0) or ((item or {}).get("size_gb") or 0)
                total_gb += sz
                sz_str = (f"{sz//1024:.1f} TB" if sz >= 1024 else f"{sz} GB") if sz else "?"
                label_str = (item or {}).get("label") or vol.get("disk_id") or "—"
                mount = vol.get("mount") or "—"
                with ui.row().classes("w-full justify-between gap-4 pl-2"):
                    ui.label(mount).classes("text-xs w-36 shrink-0 font-mono text-slate-600 dark:text-zinc-300")
                    ui.label(f"{sz_str}  [{label_str}]").classes(UIStyles.TEXT_MUTED)
            if total_gb:
                lbl = f"{total_gb//1024:.1f} TB" if total_gb >= 1024 else f"{total_gb} GB"
                ui.label(f"Total: {lbl}").classes(UIStyles.STATUS_TEXT_SUCCESS + " font-semibold pl-2")
