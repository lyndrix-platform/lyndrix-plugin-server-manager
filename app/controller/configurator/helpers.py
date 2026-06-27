"""Form helpers shared across all configurator steps."""
from __future__ import annotations


def _val(args) -> str:
    """Safely extract a string value from NiceGUI event args.

    NiceGUI may deliver e.args as a bare string, a list [value], or a dict
    {"value": ...} depending on the component and version. This normalises all
    three forms so callers don't have to guard against unhashable types.
    """
    if isinstance(args, str):
        return args
    if isinstance(args, list):
        return str(args[0]) if args else ""
    if isinstance(args, dict):
        return str(args.get("value") or args.get("modelValue") or "")
    return str(args) if args is not None else ""



def _unpack_profile(profile: dict) -> dict:
    # TODO(agent): pass the hardware accessor in instead of reaching back into the
    # service singleton — the local import here only exists to dodge a circular
    # import (helpers ← service ← configurator ← helpers).
    from ..service import server_manager_service as _svc  # local import to avoid circular
    # Migrate legacy ram_modules → total ram_gb
    ram_gb = profile.get("ram_gb")
    if not ram_gb and profile.get("ram_modules"):
        try:
            hw = _svc.catalog.hardware()
            ram_gb = sum(
                (hw.get_item(m.get("module_id")) or {}).get("size_gb", 0) * m.get("count", 1)
                for m in profile["ram_modules"]
            ) or None
        except Exception:
            ram_gb = None

    # Migrate legacy storage_gb single value → storage_disks mount-point list
    storage_disks = list(profile.get("storage_disks") or [])
    if not storage_disks and profile.get("storage_gb"):
        storage_disks = [{"mount": "/", "disk_id": "virtual-disk",
                          "size_gb": int(profile["storage_gb"])}]

    return {
        "cpu_id": profile.get("cpu_id") or "",
        "cpu_count": profile.get("cpu_count") or 1,
        "ram_gb": ram_gb,
        "storage_disks": storage_disks,
        "vcpu_count": profile.get("vcpu_count") or None,
    }


def _build_profile(form: dict) -> dict:
    stype = form.get("server_type", "physical")
    base = {
        "os_family_id": form.get("os_family_id") or None,
    }
    if stype == "physical":
        return {
            **base,
            "cpu_id": form.get("cpu_id") or None,
            "cpu_count": int(form.get("cpu_count") or 1),
            "ram_gb": int(form.get("ram_gb") or 0) or None,
            "storage_disks": list(form.get("storage_disks") or []),
        }
    return {
        **base,
        "vcpu_count": int(form.get("vcpu_count") or 0) or None,
        "ram_gb": int(form.get("ram_gb") or 0) or None,
        "storage_disks": list(form.get("storage_disks") or []),
    }


def _collect_form(form: dict) -> dict:
    tags = [t.strip() for t in (form.get("tags_raw") or "").split(",") if t.strip()]
    return {
        "name": (form.get("name") or "").strip(),
        "hostname": (form.get("hostname") or "").strip() or None,
        "environment_id": form.get("environment_id") or "",
        "server_type": form.get("server_type") or "physical",
        "product_id": form.get("product_id") or None,
        "service_class_id": form.get("service_class_id") or None,
        "os_family_id": form.get("os_family_id") or None,
        "os_version_id": form.get("os_version_id") or None,
        "os_type": form.get("os_type") or None,
        "status": form.get("status") or "active",
        "tags": tags,
        "notes": (form.get("notes") or "").strip() or None,
        "hardware_profile": _build_profile(form),
    }
