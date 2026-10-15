"""Cluster readiness (1.3.0): bootstrap DDL under the advisory lock and tolerant of
lost races, 1020-retrying writers, COUNT-based health and the persisted,
cluster-announced catalog path. Runs against in-memory SQLite."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


class _FakeCtx:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict, bool]] = []
        self.secrets: dict[str, str] = {}

    def emit(self, topic, payload, cluster=False):
        self.events.append((topic, payload, cluster))

    def set_secret(self, key, value):
        self.secrets[key] = value
        return True

    def get_secret(self, key):
        return self.secrets.get(key)


def _fake_db(monkeypatch, engine, *, lock=None):
    from app.model import database

    fake = SimpleNamespace(
        engine=engine, SessionLocal=sessionmaker(bind=engine), is_connected=True,
        advisory_lock=lock,
    )
    monkeypatch.setattr(database, "db_instance", fake)
    return fake


def _engine():
    return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


@pytest.fixture()
def svc(monkeypatch):
    from app.controller.service import ServerManagerService

    _fake_db(monkeypatch, _engine())
    service = ServerManagerService()
    service.set_context(_FakeCtx())
    service.on_db_connected()
    return service


_BASE = {"name": "atlas", "environment_id": "onprem:prod", "server_type": "vm"}


# --- bootstrap DDL ----------------------------------------------------------------

def test_ensure_tables_runs_under_bootstrap_lock_and_is_idempotent(monkeypatch):
    from app.model.database import ensure_tables

    taken: list[str] = []

    @contextmanager
    def _lock(name):
        taken.append(name)
        yield True

    _fake_db(monkeypatch, _engine(), lock=_lock)
    ensure_tables()
    ensure_tables()
    assert taken == ["lyndrix_bootstrap", "lyndrix_bootstrap"]


def test_legacy_table_is_migrated(monkeypatch):
    from app.model.database import ensure_tables

    engine = _engine()
    with engine.begin() as conn:  # a 1.0-era table: no ingest columns, no indexes
        conn.execute(text(
            "CREATE TABLE server_manager_servers (id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL, "
            "hostname VARCHAR(255), environment_id VARCHAR(64) NOT NULL, server_type VARCHAR(64) NOT NULL, "
            "status VARCHAR(64) NOT NULL, hardware_profile JSON NOT NULL, tags JSON NOT NULL, notes TEXT, "
            "ordered_by VARCHAR(255), ordered_at DATETIME, created_at DATETIME, updated_at DATETIME)"
        ))
    _fake_db(monkeypatch, engine)
    ensure_tables()
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("server_manager_servers")}
    assert {"source", "external_id", "writeback", "ci_id", "sync_state"} <= cols
    idx = {i["name"] for i in insp.get_indexes("server_manager_servers")}
    assert {"uq_server_manager_source_ext", "ix_server_manager_servers_ci_id"} <= idx


def test_lost_ddl_race_counts_as_done(monkeypatch):
    """A peer added the column between our inspect and our ALTER (MariaDB 1060)."""
    from app.model import database

    class _Conn:
        def execute(self, stmt):
            raise RuntimeError("(1060, \"Duplicate column name 'ci_id'\")")

    assert database._run_ddl(_Conn(), "ALTER TABLE x ADD COLUMN ci_id VARCHAR(36)") is False

    class _Broken:
        def execute(self, stmt):
            raise RuntimeError("(1146, \"Table 'x' doesn't exist\")")

    with pytest.raises(RuntimeError):
        database._run_ddl(_Broken(), "ALTER TABLE x ADD COLUMN y INT")


def test_create_all_race_is_retried(monkeypatch):
    from app.model import database

    _fake_db(monkeypatch, _engine())
    real = database._Base.metadata.create_all
    calls = {"n": 0}

    def _racy(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("(1050, \"Table 'server_manager_servers' already exists\")")
        return real(*a, **kw)

    monkeypatch.setattr(database._Base.metadata, "create_all", _racy)
    database.ensure_tables()
    assert calls["n"] == 2


# --- 1020 retry ------------------------------------------------------------------

def test_retry_write_retries_only_snapshot_errors():
    from app.model.database import retry_write

    attempts = {"n": 0}

    def _flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("(1020, 'Record has changed since last read')")
        return "ok"

    assert retry_write(_flaky, pause=0) == "ok" and attempts["n"] == 3

    def _bad():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        retry_write(_bad, pause=0)


def test_update_retries_on_1020_and_emits_once(svc, monkeypatch):
    from app.model import database

    monkeypatch.setattr(database.time, "sleep", lambda _s: None)
    created = svc.create_server(_BASE)
    svc._ctx.events.clear()

    real_tx = svc._update_tx
    calls = {"n": 0}

    def _flaky(*args):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("(1020, 'Record has changed since last read')")
        return real_tx(*args)

    monkeypatch.setattr(svc, "_update_tx", _flaky)
    result = svc.update_server(created["id"], {"status": "decommissioned"})
    assert result["status"] == "decommissioned" and calls["n"] == 2
    assert [t for t, _, _ in svc._ctx.events].count("server_manager:server_updated") == 1


def test_delete_and_mark_writeback_failed(svc):
    server, _ = svc.upsert_external_server({**_BASE, "source": "cmdb", "external_id": "a1", "writeback": True})
    assert svc.mark_writeback_failed("cmdb", "a1", "nope") is True
    assert svc.get_server(server["id"])["sync_state"] == "failed"
    assert svc.mark_writeback_failed("cmdb", "missing", "x") is False
    assert svc.remove_external_server("cmdb", "a1") is True
    assert svc.get_server(server["id"]) is None


def test_count_servers(svc):
    assert svc.count_servers() == 0
    svc.create_server(_BASE)
    svc.create_server({**_BASE, "name": "hydra"})
    assert svc.count_servers() == 2


# --- catalog path ----------------------------------------------------------------

def test_apply_catalog_dir_persists_and_announces(svc):
    from app.model.catalog import _CATALOG_ALLOWED_ROOT, _DEFAULT_CATALOG_DIR

    examples = _CATALOG_ALLOWED_ROOT / "examples"
    target = svc.apply_catalog_dir(str(examples))
    assert target == examples.resolve()
    assert svc._ctx.secrets["catalog_dir"] == str(examples.resolve())
    topic, payload, cluster = svc._ctx.events[-1]
    assert topic == "server_manager:catalog_changed" and cluster is True
    assert payload["action"] == "path"

    svc.apply_catalog_dir("")  # reset → stored as empty = built-in
    assert svc._ctx.secrets["catalog_dir"] == ""
    assert svc.catalog.catalog_dir.resolve() == _DEFAULT_CATALOG_DIR.resolve()


def test_apply_catalog_dir_rejects_outside_root(svc):
    with pytest.raises(ValueError):
        svc.apply_catalog_dir("/etc")
    assert "catalog_dir" not in svc._ctx.secrets


def test_peer_loads_persisted_catalog_dir(svc):
    from app.model.catalog import _CATALOG_ALLOWED_ROOT, _DEFAULT_CATALOG_DIR

    examples = (_CATALOG_ALLOWED_ROOT / "examples").resolve()
    svc._ctx.secrets["catalog_dir"] = str(examples)
    assert svc.load_catalog_setting() == examples
    assert svc.catalog.catalog_dir == examples

    svc._ctx.secrets["catalog_dir"] = "/etc"  # tampered / stale → built-in
    assert svc.load_catalog_setting() == _DEFAULT_CATALOG_DIR.resolve()
    assert not svc._ctx.events  # loading never re-announces


def test_reload_catalog_announces(svc):
    svc.reload_catalog()
    assert svc._ctx.events[-1][0] == "server_manager:catalog_changed"
    assert svc._ctx.events[-1][2] is True
    svc._ctx.events.clear()
    svc.reload_catalog(announce=False)
    assert not svc._ctx.events


def test_default_dir_is_inside_root():
    from app.model.catalog import _CATALOG_ALLOWED_ROOT, resolve_catalog_dir

    assert resolve_catalog_dir(None).parent == _CATALOG_ALLOWED_ROOT
    assert isinstance(resolve_catalog_dir("  "), Path)
