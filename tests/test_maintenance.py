"""Maintenance-window read-model (1.4.0): the three bus events are persisted
idempotently and evaluated at read time. Runs against in-memory SQLite with
``PRAGMA foreign_keys=ON`` (so a MariaDB FK violation fails here too)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.controller import maintenance as mnt

UTC = timezone.utc
# A fixed instant keeps the window/override arithmetic independent of the wall clock.
NOW = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)
CI_A = "11111111-1111-1111-1111-111111111111"
CI_B = "22222222-2222-2222-2222-222222222222"
CI_C = "33333333-3333-3333-3333-333333333333"


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


class _FakeCtx:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, topic, payload, cluster=False):
        self.events.append((topic, payload))


@pytest.fixture()
def svc(monkeypatch):
    from app.controller.service import ServerManagerService
    from app.model import database

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    fake_db = SimpleNamespace(
        engine=engine, SessionLocal=sessionmaker(bind=engine), is_connected=True
    )
    monkeypatch.setattr(database, "db_instance", fake_db)

    service = ServerManagerService()
    # Pin the store's clock to the fixture instant: the write-side purge of
    # expired overrides must judge the rows against the same "now" the tests
    # use, not the wall clock (the suite used to fail from 2026-10-19 on).
    service.maintenance.clock = lambda: NOW
    service.set_context(_FakeCtx())
    service.on_db_connected()
    return service


def _once(start: datetime, end: datetime, note: str | None = None) -> dict:
    window = {"type": "once", "start": _iso(start), "end": _iso(end)}
    if note:
        window["note"] = note
    return window


# Window around NOW (09:00–11:00 UTC) and a later one tonight (22:00–23:00 UTC).
WINDOW_NOW = _once(NOW - timedelta(hours=1), NOW + timedelta(hours=1), "Storage move")
WINDOW_TONIGHT = _once(NOW + timedelta(hours=12), NOW + timedelta(hours=13))


def _published(source: str, items: list[dict], *, replaces: bool = True) -> dict:
    return {
        "source": source,
        "published_at": _iso(NOW),
        "replaces": replaces,
        "items": items,
    }


def _item(ci_id: str, windows: list[dict], kind: str = "host", **extra) -> dict:
    return {
        "ci_id": ci_id,
        "kind": kind,
        "name": f"host-{ci_id[:4]}",
        "windows": windows,
        "inherited_from": None,
        **extra,
    }


def _server(svc, ci_id: str | None, name: str = "docker-atlas") -> dict:
    data = {
        "source": "iac-orchestrator",
        "external_id": name,
        "name": name,
        "environment_id": "onprem:prod",
        "server_type": "container",
    }
    if ci_id:
        data["ci_id"] = ci_id
    server, _ = svc.upsert_external_server(data)
    return server


# --- schema ------------------------------------------------------------------------


def test_table_is_created_idempotently(svc, monkeypatch):
    from app.model import database

    insp = inspect(database.db_instance.engine)
    assert "server_manager_maintenance" in insp.get_table_names()
    svc.on_db_connected()  # second bootstrap must not fail
    cols = {c["name"] for c in insp.get_columns("server_manager_maintenance")}
    assert {
        "ci_id",
        "kind",
        "source",
        "override_id",
        "windows",
        "until",
        "ended_at",
    } <= cols
    uniques = {
        u["name"] for u in insp.get_unique_constraints("server_manager_maintenance")
    }
    assert "uq_server_manager_maint_key" in uniques


# --- windows_published -------------------------------------------------------------


def test_replaces_per_source_and_leaves_other_sources_alone(svc):
    svc.apply_windows_published(
        _published(
            "iac-orchestrator",
            [
                _item(CI_A, [WINDOW_NOW]),
                _item(CI_B, [WINDOW_TONIGHT]),
            ],
        )
    )
    svc.apply_windows_published(_published("monitoring", [_item(CI_B, [WINDOW_NOW])]))

    # Second publish of the orchestrator drops CI_B's orchestrator windows only.
    result = svc.apply_windows_published(
        _published("iac-orchestrator", [_item(CI_A, [WINDOW_TONIGHT])])
    )
    assert result == {
        "source": "iac-orchestrator",
        "replaces": True,
        "items": 1,
        "skipped": 0,
    }

    states = svc.maintenance_for([CI_A, CI_B], now=NOW)
    assert [w["source"] for w in states[CI_A]["windows"]] == ["iac-orchestrator"]
    assert states[CI_A]["windows"][0]["windows"] == [WINDOW_TONIGHT]
    assert states[CI_A]["active"] is False
    assert [w["source"] for w in states[CI_B]["windows"]] == ["monitoring"]
    assert states[CI_B]["active"] is True and states[CI_B]["source"] == "window"


def test_duplicate_delivery_is_idempotent(svc):
    payload = _published(
        "iac-orchestrator",
        [
            _item(CI_A, [WINDOW_NOW], inherited_from="stage_default"),
            _item(CI_B, [WINDOW_TONIGHT]),
        ],
    )
    svc.apply_windows_published(payload)
    first = svc.maintenance_for([CI_A, CI_B], now=NOW)
    svc.apply_windows_published(payload)  # every instance handles the same event
    second = svc.maintenance_for([CI_A, CI_B], now=NOW)

    for state in (first, second):
        assert len(state[CI_A]["windows"]) == 1
        assert state[CI_A]["windows"][0]["inherited_from"] == "stage_default"
        assert len(state[CI_B]["windows"]) == 1
    assert {k: v for k, v in first[CI_A].items() if k != "windows"} == {
        k: v for k, v in second[CI_A].items() if k != "windows"
    }


def test_invalid_items_are_skipped_but_the_rest_is_stored(svc):
    result = svc.apply_windows_published(
        _published(
            "iac-orchestrator",
            [
                _item(CI_A, [WINDOW_NOW]),
                {"kind": "host", "name": "no-ci", "windows": [WINDOW_NOW]},  # no ci_id
                _item(
                    CI_B,
                    [{"type": "weekly", "days": [], "start": "02:00", "end": "04:00"}],
                ),
                _item(CI_C, [WINDOW_TONIGHT], kind="network"),  # kinds are open
                "garbage",
            ],
        )
    )
    assert result["items"] == 2 and result["skipped"] == 3
    states = svc.maintenance_for([CI_A, CI_B, CI_C], now=NOW)
    assert states[CI_A]["active"] is True
    assert states[CI_B]["windows"] == [] and states[CI_B]["active"] is False
    assert states[CI_C]["windows"][0]["windows"] == [WINDOW_TONIGHT]


def test_windows_are_normalised_and_evaluated_in_their_zone(svc):
    # Input form + explicit zone: Sunday 11:00–13:00 Berlin = 09:00–11:00 UTC on 2026-10-18 (CEST).
    svc.apply_windows_published(
        _published(
            "iac-orchestrator",
            [
                _item(
                    CI_A,
                    [
                        {
                            "weekly": {
                                "days": ["sun"],
                                "start": "11:00",
                                "end": "13:00",
                                "tz": "Europe/Berlin",
                            }
                        },
                    ],
                )
            ],
        )
    )
    assert NOW.weekday() == 6  # the fixture instant is a Sunday
    state = svc.maintenance_for([CI_A], now=NOW)[CI_A]
    assert state["windows"][0]["windows"] == [
        {
            "type": "weekly",
            "days": ["sun"],
            "start": "11:00",
            "end": "13:00",
            "tz": "Europe/Berlin",
        }
    ]
    assert state["active"] is True
    assert state["until"] == _iso(NOW + timedelta(hours=1))
    # Next Sunday is after the DST switch (2026-10-25): 11:00 CET = 10:00 UTC.
    assert state["next_start"] == _iso(NOW + timedelta(days=7))


def test_payload_without_source_is_rejected(svc):
    with pytest.raises(ValueError, match="source"):
        svc.apply_windows_published({"items": []})
    with pytest.raises(ValueError, match="items"):
        svc.apply_windows_published({"source": "x", "items": {"a": 1}})


# --- overrides ---------------------------------------------------------------------


def _started(
    override_id: str,
    ci_ids: list[str],
    *,
    until: datetime,
    started: datetime,
    linger: int = 15,
    source: str = "iac-orchestrator",
) -> dict:
    return {
        "override_id": override_id,
        "ci_ids": ci_ids,
        "until": _iso(until),
        "reason": "Deploy aac-grafana",
        "source": source,
        "started_at": _iso(started),
        "linger_minutes": linger,
    }


def test_override_lifecycle_start_end_linger_expire(svc):
    svc.apply_override_started(
        _started(
            "deploy-42",
            [CI_A, CI_B],
            until=NOW + timedelta(hours=4),
            started=NOW - timedelta(minutes=10),
        )
    )
    state = svc.maintenance_for([CI_A], now=NOW)[CI_A]
    assert state["active"] is True and state["source"] == "override"
    assert state["until"] == _iso(NOW + timedelta(hours=4))
    assert state["note"] == "Deploy aac-grafana"
    assert state["overrides"][0]["override_id"] == "deploy-42"
    assert state["overrides"][0]["active"] is True

    ended_at = NOW + timedelta(minutes=5)
    touched = svc.apply_override_ended(
        {
            "override_id": "deploy-42",
            "source": "iac-orchestrator",
            "ended_at": _iso(ended_at),
            "linger_minutes": 15,
        }
    )
    assert touched == 2
    # Still active during the linger …
    in_linger = svc.maintenance_for([CI_A], now=ended_at + timedelta(minutes=10))[CI_A]
    assert in_linger["active"] is True
    assert in_linger["until"] == _iso(ended_at + timedelta(minutes=15))
    assert in_linger["overrides"][0]["ended_at"] == _iso(ended_at)
    # … and gone afterwards (ignored at read time even before the purge runs).
    after = svc.maintenance_for([CI_A, CI_B], now=ended_at + timedelta(minutes=16))
    assert after[CI_A]["active"] is False and after[CI_A]["overrides"] == []
    assert after[CI_B]["active"] is False


def test_override_without_end_expires_at_until_and_is_purged(svc):
    svc.apply_override_started(
        _started(
            "deploy-7",
            [CI_A],
            until=NOW + timedelta(hours=1),
            started=NOW,
        )
    )
    assert (
        svc.maintenance_for([CI_A], now=NOW + timedelta(minutes=59))[CI_A]["active"]
        is True
    )
    assert (
        svc.maintenance_for([CI_A], now=NOW + timedelta(minutes=61))[CI_A]["active"]
        is False
    )

    assert (
        svc.purge_expired_maintenance(now=NOW + timedelta(hours=2)) == 0
    )  # inside the grace
    assert svc.purge_expired_maintenance(now=NOW + timedelta(days=2)) == 1
    assert svc.maintenance_for([CI_A], now=NOW)[CI_A]["overrides"] == []


def test_duplicate_started_does_not_resurrect_an_ended_override(svc):
    started = _started("deploy-9", [CI_A], until=NOW + timedelta(hours=4), started=NOW)
    svc.apply_override_started(started)
    svc.apply_override_ended(
        {
            "override_id": "deploy-9",
            "source": "iac-orchestrator",
            "ended_at": _iso(NOW + timedelta(minutes=1)),
        }
    )
    svc.apply_override_started(started)  # late duplicate from another instance
    state = svc.maintenance_for([CI_A], now=NOW + timedelta(hours=1))[CI_A]
    assert state["active"] is False
    assert state["overrides"] == []  # ended_at + linger lies in the past


def test_ended_for_unknown_override_is_a_noop(svc):
    assert (
        svc.apply_override_ended({"override_id": "never-started", "source": "x"}) == 0
    )
    with pytest.raises(ValueError, match="override_id"):
        svc.apply_override_started({"ci_ids": [CI_A], "until": _iso(NOW)})
    with pytest.raises(ValueError, match="until"):
        svc.apply_override_started({"override_id": "o", "ci_ids": [CI_A]})
    assert (
        svc.apply_override_started(
            {"override_id": "o", "ci_ids": [], "until": _iso(NOW)}
        )
        == 0
    )


def test_active_override_wins_over_window_and_until_is_the_later_end(svc):
    svc.apply_windows_published(
        _published("iac-orchestrator", [_item(CI_A, [WINDOW_NOW])])
    )
    svc.apply_override_started(
        _started(
            "deploy-1",
            [CI_A],
            until=NOW + timedelta(minutes=30),
            started=NOW,
        )
    )
    state = svc.maintenance_for([CI_A], now=NOW)[CI_A]
    assert state["source"] == "override"
    assert state["until"] == _iso(NOW + timedelta(hours=1))  # the window ends later


# --- join with server records ------------------------------------------------------


def test_event_before_register_server_is_kept_and_visible_later(svc):
    svc.apply_windows_published(
        _published("iac-orchestrator", [_item(CI_A, [WINDOW_NOW])])
    )
    assert all(
        s["maintenance"] is None for s in svc.get_all_servers(now=NOW)
    )  # no record yet

    server = _server(svc, CI_A)
    listed = {s["id"]: s for s in svc.get_all_servers(now=NOW)}
    assert listed[server["id"]]["maintenance"]["active"] is True
    assert svc.get_server(server["id"], now=NOW)["maintenance"]["source"] == "window"


def test_list_servers_attaches_maintenance_only_for_records_with_ci_id(svc):
    with_ci = _server(svc, CI_A, "docker-atlas")
    without = _server(svc, None, "manual-box")
    svc.apply_windows_published(
        _published("iac-orchestrator", [_item(CI_A, [WINDOW_TONIGHT])])
    )

    listed = {s["id"]: s for s in svc.get_all_servers(now=NOW)}
    assert listed[without["id"]]["maintenance"] is None
    state = listed[with_ci["id"]]["maintenance"]
    assert state["active"] is False
    assert state["next_start"] == _iso(NOW + timedelta(hours=12))
    assert state["windows"][0]["source"] == "iac-orchestrator"
    # to_dict() and the bus payloads stay free of the read-model.
    assert "maintenance" not in svc._ctx.events[0][1]


def test_in_maintenance_filter(svc):
    active = _server(svc, CI_A, "in-window")
    idle = _server(svc, CI_B, "idle")
    manual = _server(svc, None, "manual")
    svc.apply_windows_published(
        _published(
            "iac-orchestrator",
            [
                _item(CI_A, [WINDOW_NOW]),
                _item(CI_B, [WINDOW_TONIGHT]),
            ],
        )
    )
    assert [s["id"] for s in svc.get_all_servers(in_maintenance=True, now=NOW)] == [
        active["id"]
    ]
    rest = {s["id"] for s in svc.get_all_servers(in_maintenance=False, now=NOW)}
    assert rest == {idle["id"], manual["id"]}
    assert len(svc.get_all_servers(now=NOW)) == 3


def test_get_maintenance_read_model(svc):
    server = _server(svc, CI_A)
    svc.apply_windows_published(
        _published(
            "iac-orchestrator",
            [
                _item(CI_A, [WINDOW_TONIGHT], inherited_from="host"),
            ],
        )
    )
    svc.apply_windows_published(_published("monitoring", [_item(CI_A, [WINDOW_NOW])]))
    svc.apply_override_started(
        _started(
            "manual-1",
            [CI_A],
            until=NOW + timedelta(hours=2),
            started=NOW,
            source="monitoring",
        )
    )

    model = svc.get_maintenance(CI_A, now=NOW)
    assert model["ci_id"] == CI_A and model["server_ids"] == [server["id"]]
    assert model["active"] is True and model["source"] == "override"
    assert [(w["source"], w["inherited_from"]) for w in model["windows"]] == [
        ("iac-orchestrator", "host"),
        ("monitoring", None),
    ]
    assert [o["override_id"] for o in model["overrides"]] == ["manual-1"]

    unknown = svc.get_maintenance(CI_C, now=NOW)
    assert unknown["server_ids"] == [] and unknown["active"] is False
    assert unknown["windows"] == [] and unknown["overrides"] == []


def test_read_model_failure_degrades_to_none(svc, monkeypatch):
    _server(svc, CI_A)

    def _boom(*_a, **_kw):
        raise RuntimeError("table gone")

    monkeypatch.setattr(svc.maintenance, "maintenance_for", _boom)
    servers = svc.get_all_servers(now=NOW)
    assert len(servers) == 1 and servers[0]["maintenance"] is None


# --- without the core library -----------------------------------------------------


def test_without_core_library_rows_are_stored_but_not_evaluated(svc, monkeypatch):
    monkeypatch.setattr(mnt, "mw", None)
    svc.apply_windows_published(
        _published("iac-orchestrator", [_item(CI_A, [WINDOW_NOW])])
    )
    svc.apply_override_started(
        _started(
            "deploy-3",
            [CI_A],
            until=NOW + timedelta(hours=1),
            started=NOW,
        )
    )
    svc.apply_override_ended(
        {
            "override_id": "deploy-3",
            "source": "iac-orchestrator",
            "ended_at": "2026-10-18T10:05:00Z",
        }
    )

    state = svc.maintenance_for([CI_A], now=NOW + timedelta(minutes=10))[CI_A]
    assert (
        state["active"] is None
        and state["until"] is None
        and state["next_start"] is None
    )
    assert state["windows"][0]["windows"] == [WINDOW_NOW]  # raw, as published
    assert state["overrides"][0]["active"] is True  # plain arithmetic still works
    assert state["overrides"][0]["effective_until"] == "2026-10-18T10:20:00Z"
    # The list degrades the same way and the filter finds nothing active.
    _server(svc, CI_A)
    assert svc.get_all_servers(now=NOW)[0]["maintenance"]["active"] is None
    assert svc.get_all_servers(in_maintenance=True, now=NOW) == []


def test_iso_fallback_parser_matches_core_rules(monkeypatch):
    monkeypatch.setattr(mnt, "mw", None)
    assert mnt._parse_iso("2026-10-18T12:00:00Z", "t") == datetime(
        2026, 10, 18, 12, tzinfo=UTC
    )
    assert mnt._parse_iso("2026-10-18T14:00:00+02:00", "t") == datetime(
        2026, 10, 18, 12, tzinfo=UTC
    )
    assert mnt._parse_iso("2026-10-18T12:00:00", "t") == datetime(
        2026, 10, 18, 12, tzinfo=UTC
    )
    with pytest.raises(ValueError):
        mnt._parse_iso("yesterday", "t")


def test_write_side_purge_uses_the_injected_clock(svc):
    """Regression for the date bomb: the purge that piggybacks on writes runs on
    the store's clock, so rows around a fixed test instant survive regardless of
    the real date — and are purged once that clock has moved past the grace."""
    svc.apply_override_started(
        _started("deploy-1", [CI_A], until=NOW + timedelta(hours=1), started=NOW)
    )
    svc.maintenance._last_purge = None
    svc.apply_override_ended({"override_id": "other", "source": "x"})  # triggers the purge
    assert len(svc.maintenance_for([CI_A], now=NOW)[CI_A]["overrides"]) == 1

    svc.maintenance.clock = lambda: NOW + timedelta(days=3)
    svc.maintenance._last_purge = None
    svc.apply_override_ended({"override_id": "other", "source": "x"})
    with mnt.get_session() as s:
        assert s.query(mnt.MaintenanceRecord).filter_by(override_id="deploy-1").count() == 0
