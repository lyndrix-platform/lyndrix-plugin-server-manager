"""Route gates (SM-03): the core grants ``api:write`` to every ordinary user, so
ingest (acting as a source system) and the catalog controls need the custom
``server_manager:ingest`` / ``server_manager:admin`` permissions, and a REST
caller can only act as its own source."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.api import ApiIdentity

from app import api as sm_api

READ, WRITE = sm_api.PERM_READ, sm_api.PERM_WRITE
INGEST, ADMIN = sm_api.PERM_INGEST, sm_api.PERM_ADMIN
OTHER_CI = "99999999-9999-9999-9999-999999999999"

_IAC = {
    "source": "iac-orchestrator",
    "external_id": "docker-atlas",
    "name": "docker-atlas",
    "environment_id": "onprem:prod",
    "server_type": "container",
    "ci_id": "11111111-1111-1111-1111-111111111111",
}
_BODY = {"name": "db-1", "environment_id": "onprem:prod", "server_type": "vm"}


class _FakeCtx:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.secrets: dict[str, str] = {}

    def emit(self, topic, payload, cluster=False):
        self.events.append((topic, payload))

    def set_secret(self, key, value):
        self.secrets[key] = value
        return True

    def get_secret(self, key):
        return self.secrets.get(key)


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


@pytest.fixture()
def as_user(svc, monkeypatch):
    """``as_user(name, *perms)`` → TestClient authenticated as that identity."""
    from core.api import security

    app = FastAPI()
    app.include_router(sm_api.build_plugin_router(svc))
    current: dict[str, ApiIdentity] = {}
    monkeypatch.setattr(security, "require_api_auth", lambda request: current["id"])
    client = TestClient(app)

    def _login(username: str, *perms: str, is_system: bool = False) -> TestClient:
        current["id"] = ApiIdentity(
            method="api_key" if is_system else "session",
            username=username,
            permissions=frozenset(perms),
            is_system=is_system,
        )
        return client

    return _login


def test_custom_permissions_are_not_api_read_write_suffixed():
    """Only names NOT ending in :api:read/:api:write escape the baseline grants
    and the global api:read/api:write fallback."""
    for perm in (INGEST, ADMIN):
        assert not perm.endswith((":api:read", ":api:write"))
    everyday = ApiIdentity(
        method="session", username="alice",
        permissions=frozenset({READ, WRITE, "api:read", "api:write"}),
    )
    assert everyday.allows(WRITE) and not everyday.allows(INGEST) and not everyday.allows(ADMIN)


def test_ordinary_writer_cannot_use_the_ingest_routes(svc, as_user):
    """The audited bypass: PUT as 'iac-orchestrator' with writeback=true made an
    IaC-owned read-only record editable, re-pointed its ci_id and allowed DELETE."""
    owned, _ = svc.upsert_external_server(_IAC)
    client = as_user("alice", READ, WRITE)

    url = "/servers/external/iac-orchestrator/docker-atlas"
    hijack = {"writeback": True, "ci_id": OTHER_CI, "notes": "hijacked"}
    assert client.put(url, json=hijack).status_code == 403
    assert client.delete(url).status_code == 403
    assert client.post(
        "/servers/import", json={"servers": [{**_IAC, **hijack}]}
    ).status_code == 403

    after = svc.get_server(owned["id"])
    assert after["read_only"] is True and after["writeback"] is False
    assert after["ci_id"] == _IAC["ci_id"] and after["notes"] is None
    # … and the normal API still refuses to edit the read-only record.
    assert client.patch(f"/servers/{owned['id']}", json={"notes": "x"}).status_code == 409
    # Manual CRUD is untouched by the new gates.
    assert client.post("/servers", json=_BODY).status_code == 200


def test_ingest_holder_acts_only_as_its_own_source(svc, as_user):
    owned, _ = svc.upsert_external_server(_IAC)
    client = as_user("cmdb", READ, INGEST)

    # Own source: create, flip writeback, delete — all allowed.
    own = client.put("/servers/external/cmdb/db-1", json=_BODY)
    assert own.status_code == 200 and own.json()["created"] is True
    flipped = client.put("/servers/external/cmdb/db-1", json={"writeback": True})
    assert flipped.json()["server"]["writeback"] is True

    # Another source's record: neither writeback/ci_id nor delete.
    foreign = "/servers/external/iac-orchestrator/docker-atlas"
    denied = client.put(foreign, json={"writeback": True, "ci_id": OTHER_CI})
    assert denied.status_code == 403 and "iac-orchestrator" in denied.json()["detail"]
    assert client.delete(foreign).status_code == 403
    after = svc.get_server(owned["id"])
    assert after["writeback"] is False and after["ci_id"] == _IAC["ci_id"]

    # Batch: the foreign item fails on its own, the caller's item is applied.
    result = client.post(
        "/servers/import",
        json={"servers": [
            {**_IAC, "writeback": True},
            {"source": "cmdb", "external_id": "db-2", **_BODY},
        ]},
    ).json()
    assert result["failed"] == 1 and result["created"] == 1
    assert result["results"][0]["ok"] is False
    assert svc.get_server(owned["id"])["writeback"] is False

    assert client.delete("/servers/external/cmdb/db-1").status_code == 200


def test_admin_and_system_key_may_act_for_any_source(svc, as_user):
    svc.upsert_external_server(_IAC)
    url = "/servers/external/iac-orchestrator/docker-atlas"
    admin = as_user("root", READ, WRITE, INGEST, ADMIN)
    assert admin.put(url, json={"notes": "migrated"}).status_code == 200
    system = as_user("system", is_system=True)
    assert system.delete(url).status_code == 200


def test_catalog_controls_need_the_admin_permission(svc, as_user, tmp_path):
    client = as_user("alice", READ, WRITE)
    assert client.get("/catalog").json()["can_manage"] is False
    assert client.post("/catalog/reload").status_code == 403
    assert client.post("/catalog/path", json={"path": "examples"}).status_code == 403
    assert svc._ctx.secrets == {} and svc._ctx.events == []

    # Ingest alone is not catalog administration either.
    assert as_user("cmdb", READ, INGEST).post("/catalog/reload").status_code == 403

    admin = as_user("root", READ, WRITE, INGEST, ADMIN)
    assert admin.get("/catalog").json()["can_manage"] is True
    reloaded = admin.post("/catalog/reload")
    assert reloaded.status_code == 200 and reloaded.json()["can_manage"] is True


def test_manifest_declares_the_permissions_for_admins_only(plugin_entrypoint):
    manifest = plugin_entrypoint.manifest
    declared = {f"plugin:{manifest.id}:{p.id}" for p in manifest.custom_permissions}
    assert declared == {INGEST, ADMIN}
    roles = {r.id: r for r in manifest.roles}
    assert {INGEST, ADMIN} <= set(roles["admin"].permissions)
    assert roles["admin"].auto_map_groups == ["INT_ADMIN"]
    for role_id, role in roles.items():
        if role_id != "admin":
            assert ADMIN not in role.permissions
            assert role.auto_map_groups == []  # nothing new reaches INT_USER/INT_VIEWER
