"""Server Manager — business logic service.

Events emitted
--------------
server_manager:server_created   — full server dict
server_manager:server_updated   — full server dict + {"changes": {field: {old, new}}}
server_manager:server_deleted   — last known server dict
server_manager:hardware_changed — {server_id, server_name, action, old_profile,
                                    new_profile, environment_id, server_type}
server_manager:status_changed   — {server_id, server_name, old_status, new_status}

Other plugins (e.g. an order-sheet generator) subscribe to
  server_manager:hardware_changed
and inspect environment_id / old_profile / new_profile to decide what to do.
"""
from __future__ import annotations

from sqlalchemy import select

from core.logger import get_logger

from ..model.catalog import CatalogLoader
from ..model.database import get_session, ensure_tables
from ..model.models import ServerRecord

log = get_logger("Plugin:ServerManager:Service")

_UPDATABLE_FIELDS = (
    "name", "hostname", "environment_id", "server_type",
    "product_id", "service_class_id",
    "os_type", "os_family_id", "os_version_id",
    "status", "hardware_profile", "tags", "notes",
    "ordered_by", "ordered_at",
)


class ServerManagerService:
    def __init__(self) -> None:
        self._ctx = None
        self.catalog = CatalogLoader()
        self._ready = False

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def set_context(self, ctx) -> None:
        self._ctx = ctx

    def on_db_connected(self) -> None:
        ensure_tables()
        self._ready = True
        log.info("ServerManager: DB tables ready")

    def get_all_statuses(self) -> list[str]:
        """Status IDs ordered as declared in catalog/settings.yml."""
        return self.catalog.settings().get_status_ids()

    @property
    def is_ready(self) -> bool:
        return self._ready

    # ── Catalog ──────────────────────────────────────────────────────────────

    def set_catalog_dir(self, path: str) -> None:
        from pathlib import Path
        self.catalog.catalog_dir = Path(path)

    def reload_catalog(self) -> None:
        self.catalog.reload()

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
        self._require_ready()
        with get_session() as s:
            record = s.get(ServerRecord, server_id)
            if not record:
                return None
            old = record.to_dict()
            for field in _UPDATABLE_FIELDS:
                if field in data:
                    setattr(record, field, data[field])
            s.commit()
            s.refresh(record)
            result = record.to_dict()

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

        return result

    def delete_server(self, server_id: int) -> bool:
        self._require_ready()
        with get_session() as s:
            record = s.get(ServerRecord, server_id)
            if not record:
                return False
            data = record.to_dict()
            s.delete(record)
            s.commit()
        log.info(f"ServerManager: deleted '{data['name']}' (id={server_id})")
        self._emit("server_manager:server_deleted", data)
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

    def _emit(self, topic: str, payload: dict) -> None:
        if self._ctx:
            self._ctx.emit(topic, payload)


server_manager_service = ServerManagerService()
