# lyndrix-server-manager

Lyndrix plugin for server lifecycle management, built around YAML catalogs instead of hardcoded Python lists.

## Project structure

```
entrypoint.py          — manifest, lifecycle hooks (pure wiring layer)
app/
  model/
    catalog.py         — YAML/JSON catalog loader + rule evaluator
    database.py        — DB session helpers + table bootstrap
    models.py          — SQLAlchemy model (server_manager_servers table)
  controller/
    service.py         — CRUD + event emission (singleton: server_manager_service)
    configurator/      — 3-step guided add/edit dialog
  ui/
    page.py            — Main /server-manager page
    overview.py        — Server list with search / filter
    settings.py        — Plugin settings + catalog/event-bus documentation
    widget.py          — Compact dashboard widget
examples/              — Annotated sample catalog files
tests/                 — Smoke tests
```

## What lives in the catalog

The plugin loads configuration from `catalog/` at runtime:

- `hardware.yml` for CPUs, RAM, storage, network and server type options.
- `environments.yml` for provider/stage pairs and the profile each environment belongs to.
- `profiles.yml` for EDC/FCE feature flags, validation rules, and configurator UI limits.
- `products.yml` for product-level constraints, service classes, OS variants, and matrices.
- `settings.yml` for global values shared across the UI, such as server statuses and OS types.

## Runtime API

The catalog loader lives in `app/model/catalog.py` and is used by the controller and UI layers.

- `svc.catalog.hardware()` returns `HardwareCatalog`.
- `svc.catalog.environments()` returns `EnvironmentCatalog`.
- `svc.catalog.profiles()` returns `ProfileCatalog`.
- `svc.catalog.products()` returns `ProductCatalog`.
- `svc.catalog.settings()` returns `SettingsCatalog`.
- `svc.catalog.reload()` forces a re-read from disk.

The new profile helpers used by the configurator are:

- `get_ram_manual_max(profile_id)`
- `get_disk_count_options(profile_id)`
- `get_vm_disk_size_steps(profile_id)`
- `get_storage_manual_max(profile_id)`

`SettingsCatalog` provides:

- `get_statuses()` for ordered status definitions.
- `get_status_ids()` for the canonical server status list.
- `get_os_types()` and `get_os_type_map()` for OS dropdowns.

## Configuration examples

`examples/` contains a compact, annotated example set of all five catalog files. Use it as a reference when adding new fields or when you want to understand how the UI derives its options from YAML.

## Development flow

1. Edit the YAML catalog file that owns the behavior you want to change.
2. Reload the catalog from the UI or call `svc.catalog.reload()`.
3. Verify the change in the configurator or overview screens.

## Notes

- Status ordering and labels now come from `catalog/settings.yml`.
- Profile-specific configurator limits now come from `catalog/profiles.yml`.
- The Python code should stay thin and defer catalog-specific values to YAML whenever possible.
