# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
