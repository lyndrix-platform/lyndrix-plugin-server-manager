"""Server Manager — business logic service.

Events emitted
--------------
server_manager:server_created   — full server dict
server_manager:server_updated   — full server dict + {"changes": {field: {old, new}}}
server_manager:server_deleted   — last known server dict
server_manager:hardware_changed — {server_id, server_name, action, old_profile,
                                    new_profile, environment_id, server_type}
server_manager:status_changed   — {server_id, server_name, old_status, new_status}
server_manager:writeback_requested — {action: update|delete, source, external_id,
                                      server, changes} — a user edited a server
                                      whose source declared writeback=True.
                                      LOCAL ONLY: emitted on the instance that
                                      handled the edit; in cluster mode a source
                                      plugin running on another instance does not
                                      see it (its next full push still reconciles).
                                      TODO(agent): leased re-emitter for pending rows.
server_manager:catalog_changed  — {catalog_dir, action: path|reload}; bridged
                                  (cluster=True) so every instance re-reads the
                                  persisted catalog path and reloads its cache

Other plugins (e.g. an order-sheet generator) subscribe to
  server_manager:hardware_changed
and inspect environment_id / old_profile / new_profile to decide what to do.

Ingest (servers pushed in by other systems)
-------------------------------------------
upsert_external_server() / remove_external_server() are keyed by
(source, external_id), so repeated pushes of the same server are idempotent.
Both the REST ingest routes and the server_manager:register_server /
server_manager:deregister_server bus topics funnel into them; the regular
created/updated/deleted events above fire as usual.

The source decides per server whether it can persist edits (``writeback``):
  writeback=False → read-only here; user edits/deletes raise ServerReadOnlyError.
  writeback=True  → user edits apply locally (optimistic), sync_state becomes
                    "pending" and writeback_requested is emitted. The source's
                    next push clears it; mark_writeback_failed() flags errors.
The source stays authoritative: its next full push overwrites anything it
did not persist.
"""
from __future__ import annotations

import asyncio
import functools
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from core.api import get_logger

from ..model.catalog import _DEFAULT_CATALOG_DIR, CatalogLoader, resolve_catalog_dir
from ..model.database import ensure_tables, get_session, retry_write
from ..model.models import ServerRecord

log = get_logger("Plugin:ServerManager:Service")

_UPDATABLE_FIELDS = (
    "name", "hostname", "environment_id", "server_type",
    "product_id", "service_class_id",
    "os_type", "os_family_id", "os_version_id",
    "status", "hardware_profile", "tags", "notes",
    "ordered_by", "ordered_at", "ci_id",
)


_REQUIRED_ON_CREATE = ("name", "environment_id", "server_type")


# Vault key (plugin's own path, ctx.set_secret/get_secret) holding the catalog
# directory chosen via REST / NiceGUI. Empty = built-in catalog.
CATALOG_DIR_KEY = "catalog_dir"


class ServerReadOnlyError(Exception):
    """A user tried to change a server whose source cannot persist edits."""


class ServerManagerService:
    def __init__(self) -> None:
        self._ctx = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.catalog = CatalogLoader()
        self._ready = False

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def set_context(self, ctx) -> None:
        self._ctx = ctx
        # setup() runs on the core event loop; remember it so _emit() can hop
        # back onto it from the asyncio.to_thread workers the API uses.
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    def on_db_connected(self) -> None:
        """Bootstrap the schema (blocking — run via ``asyncio.to_thread``)."""
        ensure_tables()
        self._ready = True
        log.info("ServerManager: DB tables ready")

    async def bootstrap(self) -> bool:
        """Run the cluster-safe schema bootstrap off the event loop."""
        try:
            await asyncio.to_thread(self.on_db_connected)
            return True
        except Exception as exc:
            log.error(f"ServerManager: DB init failed — {exc}")
            return False

    def get_all_statuses(self) -> list[str]:
        """Status IDs ordered as declared in catalog/settings.yml."""
        return self.catalog.settings().get_status_ids()

    @property
    def is_ready(self) -> bool:
        return self._ready

    async def wait_ready(self, timeout: float = 30.0) -> bool:
        """Wait (non-blocking) for the background schema bootstrap to finish."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not self._ready and loop.time() < deadline:
            await asyncio.sleep(0.2)
        return self._ready

    # ── Catalog ──────────────────────────────────────────────────────────────

    def set_catalog_dir(self, path: str) -> None:
        self.catalog.catalog_dir = Path(path)

    def reload_catalog(self, *, announce: bool = True) -> None:
        """Re-read the catalog files; ``announce`` tells every peer to do the same."""
        self.catalog.reload()
        if announce:
            self._emit_catalog_changed("reload")

    def apply_catalog_dir(self, raw: str | None) -> Path:
        """Validate, apply, persist and announce a new catalog directory.

        ``raw`` empty/None resets to the built-in catalog. Raises ValueError for
        a path outside the plugin install root or a missing directory. The path
        is persisted in the plugin's Vault store so restarts and peer instances
        pick it up. Blocking (Vault) — call it via ``asyncio.to_thread``.
        """
        target = resolve_catalog_dir(raw)
        self.set_catalog_dir(str(target))
        stored = "" if target == _DEFAULT_CATALOG_DIR.resolve() else str(target)
        if self._ctx is not None and hasattr(self._ctx, "set_secret"):
            if self._ctx.set_secret(CATALOG_DIR_KEY, stored) is False:
                log.warning("ServerManager: catalog path applied but not persisted (Vault unavailable)")
        self._emit_catalog_changed("path")
        return target

    def load_catalog_setting(self) -> Path:
        """Apply the persisted catalog directory (boot, peer change, settings save).

        An invalid or vanished stored path falls back to the built-in catalog
        instead of failing. Blocking (Vault) — call it via ``asyncio.to_thread``.
        """
        stored = ""
        if self._ctx is not None and hasattr(self._ctx, "get_secret"):
            try:
                stored = self._ctx.get_secret(CATALOG_DIR_KEY) or ""
            except Exception as exc:
                log.warning(f"ServerManager: could not read catalog path from Vault — {exc}")
        try:
            target = resolve_catalog_dir(stored)
        except ValueError as exc:
            log.warning(f"ServerManager: stored catalog path ignored — {exc}")
            target = _DEFAULT_CATALOG_DIR.resolve()
        if target != self.catalog.catalog_dir.resolve():
            self.set_catalog_dir(str(target))  # setter reloads
        else:
            self.catalog.reload()
        return target

    def _emit_catalog_changed(self, action: str) -> None:
        self._emit(
            "server_manager:catalog_changed",
            {"catalog_dir": str(self.catalog.catalog_dir), "action": action},
            cluster=True,
        )

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def _require_ready(self) -> None:
        if not self._ready:
            raise RuntimeError(
                "Server Manager database tables not ready. "
                "Ensure the db:connected event has been received and ensure_tables() succeeded."
            )

    def get_all_servers(self) -> list[dict]:
        self._require_ready()
        with get_session() as s:
            rows = s.execute(
                select(ServerRecord).order_by(ServerRecord.name)
            ).scalars().all()
            return [r.to_dict() for r in rows]

    def count_servers(self) -> int:
        """Cheap ``SELECT COUNT(*)`` for health probes."""
        self._require_ready()
        with get_session() as s:
            return int(s.execute(select(func.count(ServerRecord.id))).scalar_one())

    def get_server(self, server_id: int) -> dict | None:
        self._require_ready()
        with get_session() as s:
            row = s.get(ServerRecord, server_id)
            return row.to_dict() if row else None

    def create_server(self, data: dict) -> dict:
        self._require_ready()
        statuses = self.get_all_statuses()
        default_status = statuses[0] if statuses else "active"
        with get_session() as s:
            record = ServerRecord(
                name=data["name"],
                hostname=data.get("hostname") or None,
                environment_id=data["environment_id"],
                server_type=data["server_type"],
                product_id=data.get("product_id") or None,
                service_class_id=data.get("service_class_id") or None,
                os_family_id=data.get("os_family_id") or None,
                os_version_id=data.get("os_version_id") or None,
                os_type=data.get("os_type") or None,
                status=data.get("status") or default_status,
                hardware_profile=data.get("hardware_profile") or {},
                tags=data.get("tags") or [],
                notes=data.get("notes") or None,
                source=data.get("source") or None,
                external_id=data.get("external_id") or None,
                writeback=bool(data.get("writeback", False)),
                ci_id=data.get("ci_id") or None,
            )
            s.add(record)
            s.commit()
            s.refresh(record)
            result = record.to_dict()

        log.info(f"ServerManager: created '{result['name']}' (id={result['id']})")
        self._emit("server_manager:server_created", result)

        if result.get("hardware_profile"):
            self._emit("server_manager:hardware_changed", {
                "server_id": result["id"],
                "server_name": result["name"],
                "action": "created",
                "old_profile": None,
                "new_profile": result["hardware_profile"],
                "environment_id": result["environment_id"],
                "server_type": result["server_type"],
            })

        return result

    def update_server(self, server_id: int, data: dict) -> dict | None:
        """User-initiated update (UI / PATCH). Honours read-only + write-back."""
        return self._update(server_id, data, from_source=False)

    def _update(self, server_id: int, data: dict, *, from_source: bool) -> dict | None:
        self._require_ready()
        outcome = retry_write(functools.partial(self._update_tx, server_id, data, from_source))
        if outcome is None:
            return None
        old, result, writeback = outcome
        return self._after_update(old, result, writeback)

    def _update_tx(self, server_id: int, data: dict, from_source: bool):
        """One short transaction (retried on 1020/1205/1213) — no events here."""
        with get_session() as s:
            record = s.get(ServerRecord, server_id)
            if not record:
                return None
            old = record.to_dict()
            if not from_source and record.read_only:
                raise ServerReadOnlyError(
                    f"'{record.name}' is managed by '{record.source}' and is read-only"
                )
            requested = {
                k: data[k] for k in _UPDATABLE_FIELDS
                if k in data and data[k] != old.get(k)
            }
            for field, value in requested.items():
                setattr(record, field, value)
            if from_source:
                # Any push from the source is its current truth: it either
                # persisted the pending edit or overrides it.
                if "writeback" in data and data["writeback"] is not None:
                    record.writeback = bool(data["writeback"])
                record.sync_state = None
                record.sync_error = None
            writeback = bool(not from_source and record.writeback and requested)
            if writeback:
                record.sync_state = "pending"
                record.sync_error = None
            s.commit()
            s.refresh(record)
            return old, record.to_dict(), writeback

    def _after_update(self, old: dict, result: dict, writeback: bool) -> dict:
        changes = {
            k: {"old": old.get(k), "new": result.get(k)}
            for k in _UPDATABLE_FIELDS
            if old.get(k) != result.get(k)
        }
        log.info(f"ServerManager: updated '{result['name']}' changed={list(changes.keys())}")
        self._emit("server_manager:server_updated", {**result, "changes": changes})

        if (old.get("hardware_profile") or {}) != (result.get("hardware_profile") or {}):
            self._emit("server_manager:hardware_changed", {
                "server_id": result["id"],
                "server_name": result["name"],
                "action": "updated",
                "old_profile": old.get("hardware_profile") or {},
                "new_profile": result.get("hardware_profile") or {},
                "environment_id": result["environment_id"],
                "server_type": result["server_type"],
            })

        if old.get("status") != result.get("status"):
            self._emit("server_manager:status_changed", {
                "server_id": result["id"],
                "server_name": result["name"],
                "old_status": old.get("status"),
                "new_status": result.get("status"),
            })

        if writeback:
            self._emit_writeback("update", result, changes)

        return result

    def delete_server(self, server_id: int) -> bool:
        """User-initiated delete (UI / DELETE). Honours read-only + write-back."""
        return self._delete(server_id, from_source=False)

    def _delete(self, server_id: int, *, from_source: bool) -> bool:
        self._require_ready()

        def _tx() -> dict | None:
            with get_session() as s:
                record = s.get(ServerRecord, server_id)
                if not record:
                    return None
                if not from_source and record.read_only:
                    raise ServerReadOnlyError(
                        f"'{record.name}' is managed by '{record.source}' and is read-only"
                    )
                last = record.to_dict()
                s.delete(record)
                s.commit()
                return last

        data = retry_write(_tx)
        if data is None:
            return False
        log.info(f"ServerManager: deleted '{data['name']}' (id={server_id})")
        self._emit("server_manager:server_deleted", data)
        # Deleted optimistically: if the source fails to persist the removal,
        # its next push simply re-creates the server.
        if not from_source and data.get("writeback"):
            self._emit_writeback("delete", data, {})
        return True

    # ── Ingest (external systems) ────────────────────────────────────────────

    def _find_external_id(self, source: str, external_id: str) -> int | None:
        with get_session() as s:
            return s.execute(
                select(ServerRecord.id).where(
                    ServerRecord.source == source,
                    ServerRecord.external_id == external_id,
                )
            ).scalar_one_or_none()

    def upsert_external_server(self, data: dict) -> tuple[dict, bool]:
        """Create or update a server owned by an external system.

        ``data`` must carry ``source`` and ``external_id``; on create it also
        needs name/environment_id/server_type, on update only the supplied
        fields are applied (PATCH semantics). Manual UI records (source NULL)
        can never be matched, so a pushing system cannot overwrite them.
        Returns ``(server, created)``.
        """
        self._require_ready()
        source = (data.get("source") or "").strip()
        external_id = (data.get("external_id") or "").strip()
        if not source or not external_id:
            raise ValueError("source and external_id are required for ingest")
        data = {**data, "source": source, "external_id": external_id}
        update = {k: v for k, v in data.items() if k in _UPDATABLE_FIELDS}

        server_id = self._find_external_id(source, external_id)
        if server_id is None:
            missing = [f for f in _REQUIRED_ON_CREATE if not data.get(f)]
            if missing:
                raise ValueError(f"Missing field(s) for new server: {', '.join(missing)}")
            try:
                return self.create_server(data), True
            except IntegrityError:
                # A concurrent push of the same key won the insert — fall
                # through and apply this payload as an update instead.
                server_id = self._find_external_id(source, external_id)
                if server_id is None:
                    raise

        if "writeback" in data:
            update["writeback"] = data["writeback"]
        result = self._update(server_id, update, from_source=True)
        if result is None:  # deleted between lookup and update
            raise LookupError(f"Server {source}/{external_id} vanished during upsert")
        return result, False

    def remove_external_server(self, source: str, external_id: str) -> bool:
        """Delete the server a system pushed under (source, external_id)."""
        self._require_ready()
        server_id = self._find_external_id(source, external_id)
        return server_id is not None and self._delete(server_id, from_source=True)

    def mark_writeback_failed(self, source: str, external_id: str, error: str) -> bool:
        """The source could not persist a requested edit — surface it in the UI."""
        self._require_ready()

        def _tx() -> dict | None:
            with get_session() as s:
                record = s.execute(
                    select(ServerRecord).where(
                        ServerRecord.source == source,
                        ServerRecord.external_id == external_id,
                    )
                ).scalar_one_or_none()
                if record is None:
                    return None
                record.sync_state = "failed"
                record.sync_error = (error or "unknown error")[:2000]
                s.commit()
                s.refresh(record)
                return record.to_dict()

        result = retry_write(_tx)
        if result is None:
            return False
        log.warning(f"ServerManager: write-back failed for '{result['name']}' — {error}")
        self._emit("server_manager:server_updated", {**result, "changes": {}})
        return True

    # ── Validation ───────────────────────────────────────────────────────────
    def validate_server(  # noqa: PLR0913
        self,
        server_type: str,
        profile: dict,
        *,
        profile_id: str | None = None,
        product_id: str | None = None,
        environment_id: str | None = None,
        os_type: str | None = None,
        os_family_id: str | None = None,
    ) -> list[dict]:
        """Run the full constraint engine and return [{rule_id, severity, message}]."""
        resolved_profile_id = profile_id
        if not resolved_profile_id and environment_id:
            resolved_profile_id = self.catalog.environments().get_profile_id(environment_id)
        resolved_profile_id = resolved_profile_id or "edc"

        # Inject os_family_id into the profile dict so the v2 matrix rules
        # (vcpu_ram_matrix, ram_special_max) can pick it up via _ctx.
        eff_profile = dict(profile or {})
        if os_family_id and not eff_profile.get("os_family_id"):
            eff_profile["os_family_id"] = os_family_id

        return self.catalog.engine().validate(
            server_type=server_type,
            profile_id=resolved_profile_id,
            product_id=product_id,
            hardware_profile=eff_profile,
            os_type=os_type,
            profile_catalog=self.catalog.profiles(),
            product_catalog=self.catalog.products(),
        )

    # ── Stats ────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        servers = self.get_all_servers()
        by_env: dict[str, int] = {}
        by_status: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for s in servers:
            by_env[s["environment_id"]] = by_env.get(s["environment_id"], 0) + 1
            by_status[s["status"]] = by_status.get(s["status"], 0) + 1
            by_type[s["server_type"]] = by_type.get(s["server_type"], 0) + 1
        return {
            "total": len(servers),
            "by_env": by_env,
            "by_status": by_status,
            "by_type": by_type,
        }

    # ── Private ──────────────────────────────────────────────────────────────

    def _emit_writeback(self, action: str, server: dict, changes: dict) -> None:
        # Local bus only (see module docstring): a source plugin on another
        # cluster instance will not receive this event.
        self._emit("server_manager:writeback_requested", {
            "action": action,
            "source": server.get("source"),
            "external_id": server.get("external_id"),
            "server": server,
            "changes": changes,
        })

    def _emit(self, topic: str, payload: dict, *, cluster: bool = False) -> None:
        if not self._ctx:
            return
        # Only pass ``cluster`` when set so older cores / test doubles keep working.
        emit = functools.partial(self._ctx.emit, cluster=True) if cluster else self._ctx.emit
        # The bus schedules async subscribers with asyncio.create_task(), which
        # only works on the loop thread. CRUD runs in to_thread workers, so
        # marshal those emits back onto the loop instead of losing them.
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        loop = self._loop
        if running is None and loop is not None and loop.is_running():
            loop.call_soon_threadsafe(emit, topic, payload)
            return
        emit(topic, payload)


server_manager_service = ServerManagerService()
