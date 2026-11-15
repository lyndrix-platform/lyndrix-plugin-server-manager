# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
Proposed version: **1.5.0** (security audit 2026-11; the manifest version is not bumped yet). Minimum core unchanged.
React bundle rebuild needed (`can_manage` flag in the settings view).
**Before the rollout:** a service account that pushes records over REST needs the new `server_manager:ingest`
permission (role `source`) and may only act as the source named like its account; the system key and admins may act
for any source. The bus ingest `server_manager:register_server` is unchanged.

### Security
- New custom permissions `server_manager:ingest` and `server_manager:admin` (admin role; new unmapped role `source`
  for service accounts). `PUT`/`DELETE /servers/external/...` and `POST /servers/import` require the ingest permission
  and the source is bound to the caller; `POST /catalog/reload` and `/catalog/path` require the admin permission.
  `GET /catalog` carries `can_manage` (SM-03).
- The rule evaluator is bounded: sequence repetition and concatenation up to 10 000 elements, no `%` formatting on
  strings, integer results up to 128 bits, expressions up to 500 characters and 100 AST nodes. A malformed profile no
  longer raises HTTP 500 in validate (SM-06).
- The NiceGUI `/server-manager` page requires `api:write`; read-only access stays with the React UI (SM-02).

### Fixed
- An installed release without a site catalog serves the bundled `examples/` catalog as a whole instead of an empty
  one, and `health()` reports degraded `catalog_not_loaded` when no server types or environments are loaded.
  `GET /catalog` gains `bundled_fallback` and `effective_dir` (SM-01).
- A push with `ci_id` null or empty never clears a stored CI id; only a different explicit id replaces it (SM-04).
- A `maintenance:override_ended` that arrives before its `override_started` is stored as a tombstone (new table
  `server_manager_maintenance_tombstones`) and the late start is stored as already ended; a re-delivered end no longer
  overwrites `ended_at`. Same contract as the entity registry (SM-05).
- The six tests that failed on the 1.4.0 tree pass: four needed a catalog (see SM-01), two were date bombs (the
  maintenance read-model now runs on an injectable clock).

## [1.4.0] - 2026-10-18
### Added
- **Maintenance badge (core API ≥ 1.13 contract, consumer side).** The plugin
  subscribes to `maintenance:windows_published`, `maintenance:override_started`
  and `maintenance:override_ended` and persists them per `ci_id` in the new
  table `server_manager_maintenance` (created by the existing locked
  `create_all` bootstrap). `replaces: true` drops every window row of that
  source in one transaction; overrides are upserted per `override_id`, linger
  `linger_minutes` after `override_ended` and are purged once long expired.
  Events for CIs without a server record are kept and joined at read time, so
  the usual order `windows_published` → `register_server` just works. Handlers
  run on every cluster instance and write idempotently (unique key); the
  emitter's `_origin` is deliberately not filtered.
- `GET /servers` and `GET /servers/{id}` carry a `maintenance` field for every
  record with a `ci_id` (`null` otherwise): `active`, `until`, `source`
  (`window` | `override`), `next_start`, `note`, the window sets per emitting
  source and the live overrides — evaluated at read time through
  `core.api.maintenance.effective_state`, one batch query for the whole list.
  `GET /servers?in_maintenance=true|false` filters on it; `GET /maintenance/{ci_id}`
  returns the full read-model of one CI plus the `server_ids` carrying it.
- React list: `MaintenanceBadge` next to the status badge ("Maintenance until
  hh:mm" while a window/override runs, "Maintenance from …" for a window
  within 24 h; tooltip with source, reason, end) and a "Maintenance" filter
  (all / in maintenance / not in maintenance). Badge class
  `lx-badge--maintenance` with `lx-badge--paused` as fallback on older shells.
  Locales de/en extended. NiceGUI pages unchanged (decision E: no detail view).

### Compatibility
- `min_core_version` stays `0.0.6`: the core library is imported with a
  `try/except ImportError`. Without it the events are still stored and returned
  raw, but `maintenance.active` is `null` and the badge stays hidden.
- `ServerRecord.to_dict()` and the `server_manager:*` event payloads are
  unchanged; the read-model is attached in the service layer only.

## [1.3.0] - 2026-10-15
### Fixed
- **Cluster-safe schema bootstrap.** `ensure_tables()` now runs off the event loop
  (background task from `setup()` / `db:connected`) under
  `db_instance.advisory_lock("lyndrix_bootstrap")` (getattr-guarded for older
  cores), uses `ADD COLUMN IF NOT EXISTS` / `CREATE [UNIQUE] INDEX IF NOT EXISTS`
  on MariaDB (plain DDL on SQLite/MySQL), and treats MariaDB 1050/1060/1061 as
  "a peer did it". A lost race no longer leaves the plugin un-ready (503 on
  every route). Bus ingest waits up to 30 s for the bootstrap instead of
  rejecting early pushes.
- Short writers (`update`, `delete`, `mark_writeback_failed`) run in READ
  COMMITTED sessions and retry on MariaDB 1020/1205/1213 (snapshot isolation,
  lock wait, deadlock); events are emitted once, after the commit.
- `health()` uses `SELECT COUNT(*)` instead of loading every server row.

### Changed
- The catalog directory (`POST /catalog/path`, NiceGUI settings) is validated
  against the plugin install root on every path (the NiceGUI form skipped the
  check before), persisted in the plugin's Vault store (`catalog_dir`) and
  re-applied on boot. Path changes and reloads are announced as
  `server_manager:catalog_changed` with `cluster=True`; peers re-read the stored
  path and reload. The plugin also re-reads it on `plugin:settings_changed`
  (core ≥ 1.5.1) for its own id.

### Known limitations
- `server_manager:writeback_requested` is emitted on the local bus only. In
  cluster mode a source plugin running on another instance does not receive it;
  the source's next full push still reconciles. A leased re-emitter for pending
  rows is a follow-up.

## [0.1.0] - 2026-06-27
### Security
- Replaced the legacy `eval()`-based combination-rule evaluator with a hardened
  AST sandbox (allowlisted nodes/operators only — no attribute access or calls),
  closing a latent code-execution primitive in the catalog rule engine.
- `POST /catalog/path` now restricts the catalog directory to the plugin install
  root, blocking arbitrary-directory file reads and path traversal.

### Fixed
- All REST handlers now run their blocking DB/YAML work off the event loop via
  `asyncio.to_thread`, preventing the "connection lost" / slow-dashboard symptom
  under concurrent load.
- `ConstraintEngine` no longer stashes per-request context on the cached
  singleton — context is threaded through the rule handlers, removing a race
  once validation runs in worker threads.
- DB-backed routes return a retryable `503` (instead of a 500) while the database
  is not yet connected.
- NiceGUI overview search/filter inputs now actually re-run the list on change
  (removed the dead one-shot-timer loop).
- React fetch wrapper now bounds every request with an `AbortController` timeout
  and routes 401 redirects through SPA navigation instead of a full page reload.
- Restored the documented `core.api`-only import boundary: logging now imports
  `get_logger` from `core.api` rather than the internal `core.logger` module
  (the 0.0.3 changelog claim below was inaccurate — the imports were still present).

### Changed
- Required-field validation now treats a literal `0` as a present value.
- Removed the unused `messaging:outbound` emit permission from the manifest.

## [0.0.3] - 2026-05-26
### Changed
- Manifest `repo_url` corrected to the canonical `lyndrix-platform` repository URL.
- `entrypoint.py` is now a pure wiring layer — inline UI page and stat-card helper moved to `app/ui/page.py`.
- Logging routed through `ctx.log` instead of importing the internal `core.logger` module.
- Catalog example directory renamed from `catalog_example/` to `examples/` to match the documented convention.

### Added
- `CHANGELOG.md`.
- `requirements-dev.txt` with the standard plugin toolchain (pytest, pytest-asyncio, pytest-cov, mypy, ruff, black).
- `tests/` scaffold with smoke tests for `server_manager_service` and the catalog loader.

### Fixed
- `repo_url` previously pointed to a personal fork.
- Removed import of internal `core.logger` — plugins must only import from `core.api`.

## [0.0.2] - earlier
- Initial public release on the new `./app/` sub-package layout.
