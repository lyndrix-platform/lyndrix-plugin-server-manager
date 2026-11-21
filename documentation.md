# Server Manager — Documentation

## Overview

The Server Manager is a central server inventory with configurable hardware catalogs and combination rules. It records servers together with their hardware profiles, environment assignments and status changes. Rich events are emitted on the global event bus, which can trigger downstream ordering and provisioning workflows in other plugins (e.g. the IaC Orchestrator).

---

## Architecture

```
lyndrix-plugin-server-manager/
├── entrypoint.py               # Manifest + lifecycle hooks
├── examples/
│   ├── hardware.yml            # CPU, RAM, storage, network options + combination rules
│   └── environments.yml        # Environments and IT provider ordering workflows
└── app/
    ├── api.py                  # FastAPI router (server CRUD + catalog + maintenance read model)
    ├── model/
    │   ├── models.py           # SQLAlchemy models (server_manager_servers, server_manager_maintenance)
    │   ├── database.py         # DB session helpers, table initialisation
    │   └── catalog.py          # YAML/JSON catalog loader + rule evaluator
    ├── controller/
    │   ├── service.py          # CRUD + event emission (singleton: server_manager_service)
    │   ├── maintenance.py      # Maintenance window read model (Core API ≥ 1.13, consumer)
    │   └── configurator/       # 3-step add/edit dialog
    └── ui/
        ├── overview.py         # Server list with search / filter (NiceGUI)
        ├── settings.py         # Plugin settings + catalog documentation (NiceGUI)
        ├── widget.py           # Compact dashboard widget (NiceGUI)
        └── page.py             # Main page /server-manager (NiceGUI)
```

**`server_manager_service`** is a singleton that performs the CRUD operations on the database and emits matching events on the event bus for every change.

**`catalog.py`** loads the YAML catalog files and evaluates combination rules to ensure that only valid hardware combinations can be selected.

---

## API Reference

All routes are reachable under `/api/plugins/lyndrix.plugin.server_manager/` and require valid authentication.

| Method | Path | Description |
|---|---|---|
| `GET` | `/servers` | Complete server list; every entry with `ci_id` carries the field `maintenance` (see [Maintenance windows](#maintenance-windows)). `?in_maintenance=true\|false` filters by the current maintenance state |
| `GET` | `/servers/{id}` | One server (incl. `maintenance`) |
| `POST` | `/servers` | Add a server (hardware profile + environment) |
| `PATCH` | `/servers/{id}` | Update a server |
| `DELETE` | `/servers/{id}` | Remove a server |
| `GET` | `/maintenance/{ci_id}` | Complete maintenance read model of a Configuration Item (windows per source, active overrides, `server_ids`) |
| `GET` | `/catalog` | Available hardware catalog + combination rules |
| `PUT` | `/servers/external/{source}/{external_id}` | Create or update a server from an external system (upsert) |
| `DELETE` | `/servers/external/{source}/{external_id}` | Remove a server delivered by an external system |
| `POST` | `/servers/import` | Batch upsert `{servers: [...]}`, result per entry |

### Servers from external systems (ingest)

Other systems (IaC Orchestrator, CMDB, scripts) can push servers in via the API or the event bus. The key is the pair **`source` + `external_id`** (e.g. `iac-orchestrator` / `docker-atlas`):

- **Idempotent:** Sending the same server repeatedly does not create duplicates — a sync job can resend its complete inventory on every run.
- **Creating** requires `name`, `environment_id` and `server_type`; on later pushes only the fields that are sent are applied (PATCH semantics).
- **Manually created servers** (empty `source`) are never matched and cannot be overwritten by an external system.
- `source` and `external_id` are immutable after creation.
- **`ci_id` is never deleted:** A push with `ci_id` `null` or empty leaves a stored CI ID unchanged; only another, explicitly sent ID replaces it.
- The regular events (`server_created`, `server_updated`, …) are emitted.
- **Write protection vs. writeback (`writeback`):** The delivering system decides per server whether it can persist changes itself.
  - `writeback: false` (default): The server is **read-only** in both UIs and via the regular API (🔒, `PATCH`/`DELETE` → `409`). Only the source itself changes it through the ingest path.
  - `writeback: true`: The server is editable. A change is applied locally right away, marked as `sync_state = pending` and reported to the source as `server_manager:writeback_requested` (e.g. a commit into the IaC repository by the IaC plugin). The source's next push sets the server back to in sync; if the source reports `server_manager:writeback_failed`, the UI shows the error.
  - The source remains the source of truth: whatever it does not persist is overwritten by its next push.
- **Permission (REST):** The ingest routes require the dedicated permission `plugin:lyndrix.plugin.server_manager:server_manager:ingest` (role "Server Manager Source System" or Administrator) — `api:write` is not sufficient, because the core grants that right to every normal user. The `source` is not freely selectable: a caller may only deliver as the source that is named like its account (service account/API key of the sync job, e.g. user `cmdb` → `source` `cmdb`), otherwise `403`. Only the system API key and holders of `…:server_manager:admin` may act for arbitrary sources. This means nobody can use REST to make a foreign, read-only record editable via `writeback: true`, re-attach its `ci_id`, or delete it. The event bus path (`register_server`) is unchanged.
- Reloading the catalog / setting the catalog path (`POST /catalog/reload`, `POST /catalog/path`) requires `…:server_manager:admin`; `GET /catalog` returns `can_manage` for this.
- There is no automatic cleanup: servers that a source no longer delivers must be removed explicitly via `DELETE` or `deregister_server`.

```bash
curl -X PUT -H "Authorization: Bearer <api-key>" -H "Content-Type: application/json" \
  https://<lyndrix>/api/plugins/lyndrix.plugin.server_manager/servers/external/iac-orchestrator/docker-atlas \
  -d '{"name": "docker-atlas", "environment_id": "onprem:prod", "server_type": "container", "status": "active"}'
```

---

## Events

### Emitted events

| Topic | Payload | Description |
|---|---|---|
| `server_manager:server_created` | `{server}` | New server was created |
| `server_manager:server_updated` | `{server, changes: {field: {old, new}}}` | Server data has changed |
| `server_manager:server_deleted` | `{server}` | Server was removed (last known state) |
| `server_manager:hardware_changed` | `{server_id, server_name, action, old_profile, new_profile, environment_id, server_type}` | Hardware profile changed |
| `server_manager:status_changed` | `{server_id, server_name, old_status, new_status}` | Status has changed |

| `server_manager:ingest_failed` | `{topic, source, external_id, error}` | A server fed in via the event bus was rejected |
| `server_manager:writeback_requested` | `{action: update\|delete, source, external_id, server, changes}` | A user has changed a server with `writeback: true` — the source should persist it |

### Subscribed events (ingest)

| Topic | Payload | Description |
|---|---|---|
| `server_manager:register_server` | `{source, external_id, name, environment_id, server_type, …}` | Create or update a server (same rules as the REST upsert) |
| `server_manager:deregister_server` | `{source, external_id}` | Remove a server |
| `server_manager:writeback_failed` | `{source, external_id, error}` | The source could not persist a requested change |

The sending plugin must declare the topic in its own `permissions.emit`. Since the event bus returns no response, errors are reported back via `server_manager:ingest_failed`.

```python
ctx.emit("server_manager:register_server", {
    "source": "iac-orchestrator",
    "external_id": "docker-atlas",
    "name": "docker-atlas",
    "environment_id": "onprem:prod",
    "server_type": "container",
})
```

---

## Maintenance windows

The Server Manager is a **consumer** of the maintenance window contract of Core API ≥ 1.13
(`core.api.maintenance`, see `lyndrix-core/docs/plugins.md` → *Maintenance windows*). It does not create
windows itself and emits no `maintenance:*` events — it shows on the host whether a maintenance window
or an override (e.g. a running deploy) is currently active (decision E: badge,
tooltip and filter; a detail view is a follow-up project).

### Subscribed events

| Topic | Payload | Processing |
|---|---|---|
| `maintenance:windows_published` | `{source, published_at, replaces, items: [{ci_id, kind, name, windows: [normal form…], inherited_from, …}]}` | `replaces: true` replaces **all** window rows of this `source` in one transaction; `kind` is open (every Registry kind); invalid items are logged and skipped, the rest is stored |
| `maintenance:override_started` | `{override_id, ci_ids, until, reason, source, started_at, linger_minutes}` | Upsert per `(ci_id, override_id)`; active from `started_at` until `until` |
| `maintenance:override_ended` | `{override_id, source, ended_at, linger_minutes}` | Sets `ended_at`; the override keeps running for `linger_minutes` (default 15), is ignored on read after that, and is deleted later; a redelivered end does not overwrite `ended_at` |

Rules:

- **Order-tolerant (since 1.5.0).** An `override_ended` that arrives before its `override_started` is stored as a tombstone (table `server_manager_maintenance_tombstones`, until `ended_at` + `linger_minutes`); the late-arriving start event is then stored already as ended. Same contract as in the Entity Registry.
- **Own table, evaluation at read time.** Only what the emitters send is stored. Whether a
  window is currently running is computed by `core.api.maintenance.effective_state` when the list is read —
  no timer, no lease.
- **Unknown `ci_id`s are stored.** It is normal for `windows_published` to arrive before
  `register_server`; the assignment to the server record happens at read time via
  `server_manager_servers.ci_id`. Manual servers without `ci_id` have no maintenance read model.
- **Cluster:** The handlers run on every instance and write idempotently into the shared
  table (unique key, delete+insert per source). `payload["_origin"]` is deliberately not filtered.
- **Without the core library** (`min_core_version` stays `0.0.6`): Events are stored and delivered raw,
  `active` is `null`, the badge stays off.

### Read model (`maintenance` on every server with `ci_id`)

```json
{
  "active": true,
  "until": "2026-10-18T11:00:00Z",
  "source": "override",
  "next_start": "2026-10-25T10:00:00Z",
  "note": "Deploy aac-grafana",
  "windows": [
    {"source": "iac-orchestrator", "inherited_from": "stage_default",
     "windows": [{"type": "weekly", "days": ["sun"], "start": "11:00", "end": "13:00", "tz": "Europe/Berlin"}],
     "updated_at": "2026-10-18T09:58:12Z"}
  ],
  "overrides": [
    {"override_id": "deploy-42", "source": "iac-orchestrator", "reason": "Deploy aac-grafana",
     "until": "2026-10-18T14:00:00Z", "started_at": "2026-10-18T09:50:00Z", "ended_at": null,
     "linger_minutes": 15, "effective_until": "2026-10-18T14:00:00Z", "active": true}
  ]
}
```

`source` is `window` or `override` (an active override wins, `until` is the later end),
`next_start` is the start of the next window that is not yet running (horizon 30 days). Expired
overrides do not appear. `GET /maintenance/{ci_id}` returns the same plus `ci_id` and
`server_ids`; for an unknown CI it returns `200` with empty lists ("not in maintenance").

### Display (React)

In the status column, a maintenance badge appears next to the status badge: "Maintenance until hh:mm"
while a window or override is running, "Maintenance from …" when the next window starts within
24 h. The tooltip names the source (emitter, possibly "inherited from host/stage_default/…"), reason
and end. The filter bar has a filter "Maintenance: all / In maintenance / Not in maintenance".
The badge uses the shell class `lx-badge--maintenance` (token `--lx-state-maintenance`) with
`lx-badge--paused` as fallback on older shells. The NiceGUI pages are unchanged.

---

## Configuration & Catalog

The hardware and environment catalogs are loaded from YAML files in the catalog directory (default `catalog/` in the plugin directory, changeable in the settings). The `catalog/` directory is site-specific and not part of the repository: if it is missing or contains no catalog files, the plugin loads the bundled example catalog from `examples/` as a whole (`bundled_fallback: true` in `GET /catalog` and in the health details). If the directory contains files but no usable catalog (no server types/environments), `health()` reports `degraded` with `reason: catalog_not_loaded`. The catalogs define:

- **`hardware.yml`:** Available CPU, RAM, storage and network options as well as combination rules that exclude invalid configurations.
- **`environments.yml`:** Environment definitions (e.g. `prod`, `staging`, `dev`) and the associated IT provider workflows for ordering processes.

Custom catalog files can be uploaded via the plugin settings.

**Rule evaluator (limited, since 1.5.0):** Expressions in profile rules are limited to 500 characters and 100 AST nodes; sequence repetition and concatenation yield at most 10,000 elements, `%` formatting on strings is not allowed, integer results are limited to 128 bits. An invalid profile no longer leads to HTTP 500 during validation.

**NiceGUI page:** `/server-manager` (NiceGUI) requires `api:write`; read-only access remains reserved for the React UI.

**`auto_enable_on_install=False`** — before first use, the catalog files must be reviewed and adjusted if necessary.

---

## Database schema

Table: `server_manager_servers`

| Column | Type | Description |
|---|---|---|
| `id` | UUID / int | Primary key |
| `name` | string | Server name |
| `hardware_profile` | JSON | Selected hardware profile (from catalog) |
| `environment_id` | string | Assigned environment |
| `server_type` | string | Type classification |
| `status` | string | Current status (`active`, `decommissioned`, …) |
| `source` | string, nullable | Originating system; empty = created manually |
| `external_id` | string, nullable | ID in the originating system; unique together with `source` |
| `writeback` | bool | Source can persist changes (otherwise read-only) |
| `sync_state` / `sync_error` | string, nullable | Writeback status: empty = in sync, `pending`, `failed` (+ error text) |
| `ci_id` | string(36), nullable, index | Entity Registry UUID of the Configuration Item; join key for the maintenance read model |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last change timestamp |

Table: `server_manager_maintenance` (read model of the maintenance windows, since 1.4.0; created by the
existing `create_all` under the bootstrap lock)

| Column | Type | Description |
|---|---|---|
| `id` | int | Primary key |
| `ci_id` | string(36), index | Configuration Item, also without server record |
| `kind` | string(16) | `window` (published window set) or `override` |
| `source` | string(64), index | Emitter (`iac-orchestrator`, `monitoring`, …) |
| `override_id` | string(128), index | Override ID; empty (`""`, so the unique key applies) for `window` rows |
| `windows` | JSON, nullable | Windows in the bus normal form (`{"type": …}`) |
| `inherited_from` | string(32), nullable | `host`, `stage_default`, `site_default` or empty |
| `until` / `started_at` / `ended_at` | datetime (UTC, naive), nullable | Override times |
| `linger_minutes` | int, nullable | Follow-up time after `ended_at` (default 15) |
| `reason` | text, nullable | Reason of the override |
| `updated_at` | datetime | Last write timestamp |

Table: `server_manager_maintenance_tombstones` (since 1.5.0) — noted ends for not-yet-known overrides: `source` (empty = any), `override_id`, `ended_at`, `linger_minutes`, `expires_at`, `created_at`; unique key `(source, override_id)`.

Unique key of table `server_manager_maintenance`: `(ci_id, source, kind, override_id)`. Expired overrides are ignored on read
and occasionally deleted one day after their effective end (during write operations, at most
every 10 minutes per process).

---

## Development & Tests

```bash
# From the plugin directory (lyndrix-plugin-server-manager/)
pip install -r requirements-dev.txt

# Run tests
pytest

# Type checking
mypy .

# Linter
ruff check .

# Check formatter
black --check .
```

The model and controller layers are testable without a running core. `ModuleContext` can be mocked for lifecycle tests.
