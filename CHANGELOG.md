# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
