"""Ingest (external push) tests — run the real service against in-memory SQLite."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


class _FakeCtx:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, topic, payload):
        self.events.append((topic, payload))

    def topics(self) -> list[str]:
        return [t for t, _ in self.events]


@pytest.fixture()
def svc(monkeypatch):
    from app.controller.service import ServerManagerService
    from app.model import database

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    fake_db = SimpleNamespace(
        engine=engine, SessionLocal=sessionmaker(bind=engine), is_connected=True
    )
    monkeypatch.setattr(database, "db_instance", fake_db)

    service = ServerManagerService()
    service.set_context(_FakeCtx())
    service.on_db_connected()
    return service


_BASE = {
    "source": "iac-orchestrator",
    "external_id": "docker-atlas",
    "name": "docker-atlas",
    "environment_id": "onprem:prod",
    "server_type": "container",
}


def test_upsert_creates_then_updates_without_duplicates(svc):
    server, created = svc.upsert_external_server(_BASE)
    assert created is True
    assert server["source"] == "iac-orchestrator"
    assert server["external_id"] == "docker-atlas"

    updated, created = svc.upsert_external_server(
        {"source": "iac-orchestrator", "external_id": "docker-atlas", "status": "provisioning"}
    )
    assert created is False
    assert updated["id"] == server["id"]
    assert updated["status"] == "provisioning"
    assert updated["name"] == "docker-atlas"  # untouched fields survive (PATCH)
    assert len(svc.get_all_servers()) == 1
    assert svc._ctx.topics() == [
        "server_manager:server_created",
        "server_manager:server_updated",
        "server_manager:status_changed",
    ]


def test_same_external_id_from_other_source_is_a_new_server(svc):
    svc.upsert_external_server(_BASE)
    _, created = svc.upsert_external_server({**_BASE, "source": "cmdb"})
    assert created is True
    assert len(svc.get_all_servers()) == 2


def test_manual_records_are_never_matched(svc):
    manual = svc.create_server(
        {"name": "docker-atlas", "environment_id": "onprem:prod", "server_type": "vm"}
    )
    _, created = svc.upsert_external_server(_BASE)
    assert created is True
    assert svc.get_server(manual["id"])["server_type"] == "vm"


def test_create_requires_core_fields_and_key(svc):
    with pytest.raises(ValueError, match="name"):
        svc.upsert_external_server({"source": "x", "external_id": "y", "server_type": "vm",
                                    "environment_id": "e"})
    with pytest.raises(ValueError, match="external_id"):
        svc.upsert_external_server({**_BASE, "external_id": "  "})


def test_identity_fields_cannot_be_changed_by_update(svc):
    server, _ = svc.upsert_external_server({**_BASE, "writeback": True})
    svc.update_server(server["id"], {"source": "evil", "external_id": "other", "writeback": False})
    again = svc.get_server(server["id"])
    assert (again["source"], again["external_id"]) == ("iac-orchestrator", "docker-atlas")
    assert again["writeback"] is True  # only the source may change it


def test_remove_external_server(svc):
    svc.upsert_external_server(_BASE)
    assert svc.remove_external_server("iac-orchestrator", "docker-atlas") is True
    assert svc.remove_external_server("iac-orchestrator", "docker-atlas") is False
    assert svc.get_all_servers() == []
    assert svc._ctx.topics()[-1] == "server_manager:server_deleted"


def test_migration_is_idempotent(svc):
    svc.on_db_connected()  # second run must not re-add columns or the index
    _, created = svc.upsert_external_server(_BASE)
    assert created is True


def test_emit_from_worker_thread_hops_onto_the_loop():
    """CRUD runs in to_thread workers; emits must still reach the loop thread."""
    import asyncio
    import threading

    from app.controller.service import ServerManagerService

    seen: list[str] = []

    async def main():
        loop_thread = threading.get_ident()
        service = ServerManagerService()
        ctx = SimpleNamespace(
            emit=lambda topic, payload: seen.append(
                "loop" if threading.get_ident() == loop_thread else "worker"
            )
        )
        service.set_context(ctx)
        await asyncio.to_thread(service._emit, "server_manager:server_created", {})
        await asyncio.sleep(0)  # let call_soon_threadsafe run

    asyncio.run(main())
    assert seen == ["loop"]


# ── Read-only vs write-back (source declares capability) ──────────────────────

def test_pushed_server_defaults_to_read_only(svc):
    from app.controller.service import ServerReadOnlyError

    server, _ = svc.upsert_external_server(_BASE)
    assert server["read_only"] is True and server["writeback"] is False
    with pytest.raises(ServerReadOnlyError):
        svc.update_server(server["id"], {"status": "decommissioned"})
    with pytest.raises(ServerReadOnlyError):
        svc.delete_server(server["id"])
    # The source itself can still change and remove it.
    updated, _ = svc.upsert_external_server({**_BASE, "status": "provisioning"})
    assert updated["status"] == "provisioning"
    assert svc.remove_external_server("iac-orchestrator", "docker-atlas") is True


def test_writeback_edit_applies_and_requests_persistence(svc):
    server, _ = svc.upsert_external_server({**_BASE, "writeback": True})
    assert server["read_only"] is False
    svc._ctx.events.clear()

    edited = svc.update_server(server["id"], {"notes": "moved to rack 3"})
    assert edited["notes"] == "moved to rack 3"
    assert edited["sync_state"] == "pending"
    topic, payload = svc._ctx.events[-1]
    assert topic == "server_manager:writeback_requested"
    assert payload["action"] == "update"
    assert (payload["source"], payload["external_id"]) == ("iac-orchestrator", "docker-atlas")
    assert payload["changes"] == {"notes": {"old": None, "new": "moved to rack 3"}}

    # Source commits and pushes back → in sync again.
    synced, _ = svc.upsert_external_server({**_BASE, "notes": "moved to rack 3"})
    assert synced["sync_state"] is None
    assert synced["writeback"] is True  # unchanged when the push omits it


def test_noop_edit_does_not_request_writeback(svc):
    server, _ = svc.upsert_external_server({**_BASE, "writeback": True})
    svc._ctx.events.clear()
    again = svc.update_server(server["id"], {"name": "docker-atlas"})
    assert again["sync_state"] is None
    assert "server_manager:writeback_requested" not in svc._ctx.topics()


def test_writeback_failure_is_recorded_and_cleared_by_next_push(svc):
    server, _ = svc.upsert_external_server({**_BASE, "writeback": True})
    svc.update_server(server["id"], {"status": "decommissioned"})
    assert svc.mark_writeback_failed("iac-orchestrator", "docker-atlas", "push rejected")
    failed = svc.get_server(server["id"])
    assert (failed["sync_state"], failed["sync_error"]) == ("failed", "push rejected")
    assert svc.mark_writeback_failed("iac-orchestrator", "nope", "x") is False

    # The source stays authoritative: its next push restores its own state.
    restored, _ = svc.upsert_external_server({**_BASE, "status": "active"})
    assert (restored["status"], restored["sync_state"], restored["sync_error"]) == (
        "active", None, None
    )


def test_writeback_delete_emits_request(svc):
    server, _ = svc.upsert_external_server({**_BASE, "writeback": True})
    svc._ctx.events.clear()
    assert svc.delete_server(server["id"]) is True
    topic, payload = svc._ctx.events[-1]
    assert topic == "server_manager:writeback_requested"
    assert payload["action"] == "delete"


def test_source_can_revoke_writeback(svc):
    svc.upsert_external_server({**_BASE, "writeback": True})
    server, _ = svc.upsert_external_server({**_BASE, "writeback": False})
    assert server["read_only"] is True


def test_manual_servers_stay_editable_without_writeback_events(svc):
    manual = svc.create_server(
        {"name": "lab-box", "environment_id": "onprem:dev", "server_type": "vm"}
    )
    assert manual["read_only"] is False
    svc.update_server(manual["id"], {"notes": "x"})
    assert "server_manager:writeback_requested" not in svc._ctx.topics()
