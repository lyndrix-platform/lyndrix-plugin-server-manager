"""Maintenance-window read-model (core API >= 1.13) for the Server Manager.

The Server Manager is a *consumer* of the maintenance bus contract: it stores
what the emitters publish (``maintenance:windows_published``,
``maintenance:override_started`` / ``maintenance:override_ended``) in its own
table ``server_manager_maintenance`` and answers "is this host in maintenance
right now?" when the server list is read. See ``lyndrix-core/docs/plugins.md``
→ "Maintenance windows" for the contract.

Rules (P0 result, decision 8):
  * ``replaces: true`` → every window row of that ``source`` is replaced in one
    transaction (delete + insert); the same payload twice ends in the same rows.
  * Overrides are upserted per ``override_id``; ``override_ended`` only sets
    ``ended_at``/``linger_minutes``, and a late duplicate ``started`` never
    resurrects an ended override.
  * Events for ``ci_id``s without a server record are stored anyway
    (``windows_published`` before ``register_server`` is the normal order) and
    joined to ``server_manager_servers.ci_id`` at read time.
  * Expired overrides are ignored when read and purged occasionally on write —
    no timer, no lease.
  * Handlers run on every cluster instance; the writers are idempotent (unique
    key, ``IntegrityError`` = "a peer wrote the same rows"), so
    ``payload["_origin"]`` is deliberately not filtered.

The core library is optional (``min_core_version`` stays 0.0.6): without it
the rows are still stored and returned raw, but ``active`` is ``None`` because
windows cannot be evaluated.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence, TypeVar

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError

from core.api import get_logger

try:  # core >= 1.11 (API 1.13)
    from core.api import maintenance as mw
except ImportError:  # pragma: no cover - older core: store raw, do not evaluate
    mw = None  # type: ignore[assignment]

from ..model.database import get_session, retry_write
from ..model.models import (
    MAINT_KIND_OVERRIDE,
    MAINT_KIND_WINDOW,
    MAINT_NO_OVERRIDE,
    MaintenanceRecord,
    MaintenanceTombstone,
)

log = get_logger("Plugin:ServerManager:Maintenance")

UTC = timezone.utc
DEFAULT_LINGER_MINUTES = 15
# Expired overrides are deleted this long after their effective end — late
# enough that a straggling `ended` still finds its row.
PURGE_GRACE = timedelta(days=1)
# How often a writer sweeps expired overrides (per process).
_PURGE_INTERVAL_S = 600.0
_IN_CHUNK = 500
_CI_ID_MAX = 36
_SOURCE_MAX = 64
_OVERRIDE_ID_MAX = 128
_INHERITED_MAX = 32
_REASON_MAX = 2000

T = TypeVar("T")


class MaintenancePayloadError(ValueError):
    """A maintenance bus payload is malformed (missing source / id / timestamp)."""


# ── Time helpers ───────────────────────────────────────────────────────────────


def utcnow() -> datetime:
    """Aware UTC now, whole seconds (the bus carries second precision)."""
    return datetime.now(UTC).replace(microsecond=0)


def _aware(dt: datetime | None) -> datetime | None:
    """Naive → UTC; aware → UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _to_db(dt: datetime | None) -> datetime | None:
    """Aware → naive UTC for the DateTime columns (MariaDB stores no zone)."""
    aware = _aware(dt)
    return aware.replace(tzinfo=None) if aware is not None else None


def _iso(dt: datetime | None) -> str | None:
    aware = _aware(dt)
    if aware is None:
        return None
    return aware.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any, field: str) -> datetime:
    """ISO-8601 (``Z`` accepted, naive = UTC) → aware UTC.

    Delegates to the core parser when present so both sides apply the same
    rules; the stdlib fallback covers older cores.
    """
    if isinstance(value, datetime):
        return _aware(value)  # type: ignore[return-value]
    if not isinstance(value, str) or not value.strip():
        raise MaintenancePayloadError(f"{field}: ISO-8601 timestamp required")
    if mw is not None:
        try:
            return mw.parse_iso(value)
        except mw.MaintenanceError as exc:
            raise MaintenancePayloadError(f"{field}: {exc}") from exc
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MaintenancePayloadError(
            f"{field}: invalid ISO-8601 timestamp {value!r}"
        ) from exc
    return _aware(parsed)  # type: ignore[return-value]


def _as_int(value: Any, field: str, default: int | None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool):
        raise MaintenancePayloadError(f"{field} must be an integer")
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        raise MaintenancePayloadError(f"{field} must be an integer, got {value!r}")
    if number < 0:
        raise MaintenancePayloadError(f"{field} must be >= 0")
    return number


def _ci_id(value: Any) -> str | None:
    """A usable ci_id (non-empty string that fits the column), else None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > _CI_ID_MAX:
        return None
    return text


def _chunks(items: Sequence[T], size: int = _IN_CHUNK) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


# ── Payload normalisation (unknown fields are ignored, contract §3) ────────────


def parse_windows_published(
    payload: Mapping[str, Any],
) -> tuple[str, bool, dict[str, dict], int]:
    """``maintenance:windows_published`` → ``(source, replaces, {ci_id: item}, skipped)``.

    Items with an unusable ``ci_id`` or invalid windows are skipped (and
    logged); the rest of the payload is still applied. A ``ci_id`` listed twice
    keeps the last item. Windows are stored in the bus normal form.
    """
    source = payload.get("source")
    if not isinstance(source, str) or not source.strip():
        raise MaintenancePayloadError("windows_published: source is required")
    source = source.strip()[:_SOURCE_MAX]
    replaces_raw = payload.get("replaces")
    replaces = True if replaces_raw is None else bool(replaces_raw)
    raw_items = payload.get("items")
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, (list, tuple)):
        raise MaintenancePayloadError("windows_published: items must be a list")

    items: dict[str, dict] = {}
    skipped = 0
    for idx, raw in enumerate(raw_items):
        if not isinstance(raw, Mapping):
            skipped += 1
            log.warning(f"Maintenance: {source} items[{idx}] skipped — not an object")
            continue
        ci_id = _ci_id(raw.get("ci_id"))
        if ci_id is None:
            skipped += 1
            log.warning(f"Maintenance: {source} items[{idx}] skipped — unusable ci_id")
            continue
        windows_raw = raw.get("windows")
        windows: list[dict] = []
        if windows_raw is not None:
            if not isinstance(windows_raw, (list, tuple)):
                skipped += 1
                log.warning(
                    f"Maintenance: {source} items[{idx}] ({ci_id}) skipped — windows not a list"
                )
                continue
            if mw is not None:
                try:
                    windows = mw.to_dicts(mw.parse_windows(list(windows_raw)))
                except mw.MaintenanceError as exc:
                    skipped += 1
                    log.warning(
                        f"Maintenance: {source} items[{idx}] ({ci_id}) skipped — {exc}"
                    )
                    continue
            else:
                windows = [dict(w) for w in windows_raw if isinstance(w, Mapping)]
        inherited = raw.get("inherited_from")
        items[ci_id] = {
            "ci_id": ci_id,
            "windows": windows,
            "inherited_from": str(inherited)[:_INHERITED_MAX] if inherited else None,
        }
    return source, replaces, items, skipped


def _override_id(payload: Mapping[str, Any], topic: str) -> str:
    override_id = payload.get("override_id")
    if not isinstance(override_id, str) or not override_id.strip():
        raise MaintenancePayloadError(f"{topic}: override_id is required")
    override_id = override_id.strip()
    if len(override_id) > _OVERRIDE_ID_MAX:
        raise MaintenancePayloadError(
            f"{topic}: override_id longer than {_OVERRIDE_ID_MAX}"
        )
    return override_id


def parse_override_started(
    payload: Mapping[str, Any], now: datetime | None = None
) -> dict:
    """``maintenance:override_started`` → normalised dict (aware UTC datetimes).

    ``now`` is the processing time used for a missing ``started_at``."""
    override_id = _override_id(payload, "override_started")
    raw_ids = payload.get("ci_ids")
    if raw_ids is None:
        raw_ids = []
    if not isinstance(raw_ids, (list, tuple)):
        raise MaintenancePayloadError("override_started: ci_ids must be a list")
    ci_ids: list[str] = []
    for raw in raw_ids:
        ci_id = _ci_id(raw)
        if ci_id is None:
            log.warning(
                f"Maintenance: override {override_id} — unusable ci_id {raw!r} ignored"
            )
        elif ci_id not in ci_ids:
            ci_ids.append(ci_id)
    if payload.get("until") is None:
        raise MaintenancePayloadError("override_started: until is required")
    until = _parse_iso(payload.get("until"), "until")
    started_raw = payload.get("started_at")
    started_at = (
        _parse_iso(started_raw, "started_at")
        if started_raw is not None
        else (now or utcnow())
    )
    reason = payload.get("reason")
    return {
        "override_id": override_id,
        "ci_ids": ci_ids,
        "until": until,
        "started_at": started_at,
        "linger_minutes": _as_int(
            payload.get("linger_minutes"), "linger_minutes", DEFAULT_LINGER_MINUTES
        ),
        "reason": str(reason)[:_REASON_MAX] if reason else None,
        "source": str(payload.get("source") or "")[:_SOURCE_MAX],
    }


def parse_override_ended(
    payload: Mapping[str, Any], now: datetime | None = None
) -> dict:
    """``maintenance:override_ended`` → normalised dict (``ended_at`` missing = now)."""
    override_id = _override_id(payload, "override_ended")
    ended_raw = payload.get("ended_at")
    source = payload.get("source")
    return {
        "override_id": override_id,
        "source": str(source)[:_SOURCE_MAX] if source else None,
        "ended_at": (
            _parse_iso(ended_raw, "ended_at")
            if ended_raw is not None
            else (now or utcnow())
        ),
        "linger_minutes": _as_int(
            payload.get("linger_minutes"), "linger_minutes", None
        ),
    }


# ── Read-time evaluation ───────────────────────────────────────────────────────


def override_effective_end(
    until: datetime | None, ended_at: datetime | None, linger_minutes: int | None
) -> datetime | None:
    """``ended_at + linger`` once ended, otherwise ``until`` (same rule as the core)."""
    if ended_at is not None:
        linger = (
            DEFAULT_LINGER_MINUTES if linger_minutes is None else max(0, linger_minutes)
        )
        return ended_at + timedelta(minutes=linger)
    return until


def _override_dict(
    row: MaintenanceRecord, now: datetime
) -> tuple[dict, datetime | None]:
    until, started, ended = (
        _aware(row.until),
        _aware(row.started_at),
        _aware(row.ended_at),
    )
    end = override_effective_end(until, ended, row.linger_minutes)
    active = started is not None and end is not None and started <= now < end
    linger = (
        DEFAULT_LINGER_MINUTES if row.linger_minutes is None else row.linger_minutes
    )
    return (
        {
            "override_id": row.override_id,
            "source": row.source,
            "reason": row.reason,
            "until": _iso(until),
            "started_at": _iso(started),
            "ended_at": _iso(ended),
            "linger_minutes": linger,
            "effective_until": _iso(end),
            "active": active,
        },
        end,
    )


def evaluate(
    window_rows: Iterable[MaintenanceRecord],
    override_rows: Iterable[MaintenanceRecord],
    now: datetime,
) -> dict:
    """Read-model of one CI at ``now``: effective state plus the raw windows per
    source and the live (not yet expired) overrides.

    ``active`` is ``None`` when the core library is missing (windows cannot be
    evaluated); the raw data is returned either way.
    """
    windows_out = [
        {
            "source": row.source,
            "inherited_from": row.inherited_from,
            "windows": list(row.windows or []),
            "updated_at": _iso(row.updated_at),
        }
        for row in sorted(window_rows, key=lambda r: r.source)
    ]
    overrides_out: list[dict] = []
    live_rows: list[MaintenanceRecord] = []
    for row in override_rows:
        item, end = _override_dict(row, now)
        if end is None or end <= now:
            continue  # expired: ignored at read time, purged later
        overrides_out.append(item)
        live_rows.append(row)

    state: dict[str, Any] = {
        "active": None,
        "until": None,
        "source": None,
        "next_start": None,
        "note": None,
    }
    if mw is not None:
        schedules = []
        for row in window_rows:
            try:
                schedules.extend(mw.parse_windows(list(row.windows or [])))
            except mw.MaintenanceError as exc:  # stored by an older plugin version
                log.warning(
                    f"Maintenance: {row.source}/{row.ci_id} windows unreadable — {exc}"
                )
        overrides = [
            mw.Override(
                override_id=row.override_id,
                ci_ids=(row.ci_id,),
                until=_aware(row.until) or now,
                started_at=_aware(row.started_at) or now,
                ended_at=_aware(row.ended_at),
                linger_minutes=(
                    DEFAULT_LINGER_MINUTES
                    if row.linger_minutes is None
                    else row.linger_minutes
                ),
                reason=row.reason or "",
                source=row.source,
            )
            for row in live_rows
        ]
        effective = mw.effective_state(schedules, overrides, now)
        state = {
            "active": effective.active,
            "until": _iso(effective.until),
            "source": effective.source,
            "next_start": _iso(effective.next_start),
            "note": effective.note,
        }
    return {**state, "windows": windows_out, "overrides": overrides_out}


# ── Store ──────────────────────────────────────────────────────────────────────


class MaintenanceStore:
    """DB side of the read-model: idempotent writers and one batch reader.

    All writers are blocking (call them via ``asyncio.to_thread``), run one
    short READ COMMITTED transaction each and retry on MariaDB 1020/1205/1213.
    """

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        # Every "what time is it" of the store goes through this clock (default
        # values of missing timestamps, the expiry purge, read-time evaluation
        # without an explicit ``now``), so tests can pin it instead of racing
        # the wall clock.
        self.clock: Callable[[], datetime] = clock or utcnow
        self._last_purge: float | None = None

    def _now(self) -> datetime:
        return _aware(self.clock()) or utcnow()

    # ── Writers ──

    def apply_windows_published(self, payload: Mapping[str, Any]) -> dict:
        """Replace (``replaces: true``) or upsert the window sets of one source."""
        source, replaces, items, skipped = parse_windows_published(payload)

        def _tx() -> None:
            with get_session() as s:
                scope = delete(MaintenanceRecord).where(
                    MaintenanceRecord.source == source,
                    MaintenanceRecord.kind == MAINT_KIND_WINDOW,
                )
                if replaces:
                    s.execute(scope)
                else:
                    for chunk in _chunks(list(items)):
                        s.execute(scope.where(MaintenanceRecord.ci_id.in_(chunk)))
                for item in items.values():
                    s.add(
                        MaintenanceRecord(
                            ci_id=item["ci_id"],
                            kind=MAINT_KIND_WINDOW,
                            source=source,
                            override_id=MAINT_NO_OVERRIDE,
                            windows=item["windows"],
                            inherited_from=item["inherited_from"],
                        )
                    )
                s.commit()

        try:
            retry_write(_tx)
        except IntegrityError:
            # A peer instance applied the very same publish concurrently — its
            # rows carry the same content, so this delivery is done.
            log.debug(f"Maintenance: {source} publish already applied by a peer")
        log.info(
            f"Maintenance: {source} published {len(items)} window set(s)"
            + (f", {skipped} item(s) skipped" if skipped else "")
        )
        self._maybe_purge()
        return {
            "source": source,
            "replaces": replaces,
            "items": len(items),
            "skipped": skipped,
        }

    # Out-of-order contract (identical to the Entity Registry's):
    #   1. An override is identified by (source, override_id).
    #   2. An "ended" for an override without stored rows is remembered as a
    #      tombstone (its ended_at, kept until ended_at + linger).
    #   3. A "started" arriving while the tombstone lives is stored already
    #      ended with the tombstone's ended_at — the end state equals in-order
    #      delivery. After the tombstone expired it is a new override.
    #   4. A re-delivered "ended" is a no-op: the first ended_at wins and no
    #      "ended" ever touches `until`.
    #   5. A re-delivered "started" never revives an ended override.

    @staticmethod
    def _live_tombstone(s, source: str | None, override_id: str, now: datetime):
        """The unexpired tombstone for this override, if any. A tombstone from a
        source-less "ended" (``source == ""``) matches any source."""
        stmt = select(MaintenanceTombstone).where(
            MaintenanceTombstone.override_id == override_id,
            MaintenanceTombstone.expires_at > _to_db(now),
        )
        if source is not None:
            stmt = stmt.where(MaintenanceTombstone.source.in_(sorted({source, ""})))
        return s.execute(stmt.order_by(MaintenanceTombstone.ended_at)).scalars().first()

    def _apply_tombstone(self, source: str | None, override_id: str, now: datetime) -> int:
        """End every still-open row of an override a live tombstone exists for."""

        def _tx() -> int:
            with get_session() as s:
                tomb = self._live_tombstone(s, source, override_id, now)
                if tomb is None:
                    return 0
                query = select(MaintenanceRecord).where(
                    MaintenanceRecord.kind == MAINT_KIND_OVERRIDE,
                    MaintenanceRecord.override_id == override_id,
                    MaintenanceRecord.ended_at.is_(None),
                )
                if tomb.source:
                    query = query.where(MaintenanceRecord.source == tomb.source)
                rows = s.execute(query).scalars().all()
                for row in rows:
                    row.ended_at = tomb.ended_at
                    row.linger_minutes = tomb.linger_minutes
                s.commit()
                return len(rows)

        return retry_write(_tx)

    def apply_override_started(self, payload: Mapping[str, Any]) -> int:
        """Upsert one override row per ``ci_id``; returns the number of rows touched."""
        now = self._now()
        data = parse_override_started(payload, now)
        if not data["ci_ids"]:
            log.info(
                f"Maintenance: override {data['override_id']} has no usable ci_ids — ignored"
            )
            return 0

        def _tx() -> int:
            with get_session() as s:
                existing = {
                    row.ci_id: row
                    for row in s.execute(
                        select(MaintenanceRecord).where(
                            MaintenanceRecord.kind == MAINT_KIND_OVERRIDE,
                            MaintenanceRecord.source == data["source"],
                            MaintenanceRecord.override_id == data["override_id"],
                        )
                    ).scalars()
                }
                # Already over? Either the "ended" overtook us (tombstone) or this
                # is a re-delivery naming CIs the first "started" did not have —
                # new rows are then born ended instead of becoming active.
                ended_at, ended_linger = None, None
                tomb = self._live_tombstone(s, data["source"], data["override_id"], now)
                if tomb is not None:
                    ended_at, ended_linger = tomb.ended_at, tomb.linger_minutes
                else:
                    done = next(
                        (r for r in sorted(
                            (r for r in existing.values() if r.ended_at is not None),
                            key=lambda r: r.ended_at,
                        )),
                        None,
                    )
                    if done is not None:
                        ended_at, ended_linger = done.ended_at, done.linger_minutes
                for ci_id in data["ci_ids"]:
                    row = existing.get(ci_id)
                    is_new = row is None
                    if row is None:
                        row = MaintenanceRecord(
                            ci_id=ci_id,
                            kind=MAINT_KIND_OVERRIDE,
                            source=data["source"],
                            override_id=data["override_id"],
                            ended_at=None,
                        )
                        s.add(row)
                    row.until = _to_db(data["until"])
                    row.started_at = _to_db(data["started_at"])
                    row.reason = data["reason"]
                    # A duplicate `started` after `ended` must not resurrect the
                    # override: ended_at (and the linger it came with) stay.
                    if row.ended_at is None:
                        row.linger_minutes = data["linger_minutes"]
                        if ended_at is not None and (is_new or tomb is not None):
                            row.ended_at = ended_at
                            row.linger_minutes = ended_linger
                s.commit()
                return len(data["ci_ids"])

        try:
            touched = retry_write(_tx)
        except IntegrityError:
            # A peer inserted the same (ci_id, override_id) first; the second
            # pass finds the rows and takes the update path.
            touched = retry_write(_tx)
        # The "ended" may have committed its tombstone between our read and our
        # commit (both handlers run as concurrent tasks). Each side re-checks
        # after its own commit, so at least one of them sees the other.
        self._apply_tombstone(data["source"], data["override_id"], now)
        log.info(
            f"Maintenance: override {data['override_id']} ({data['source']}) started "
            f"for {touched} CI(s) until {_iso(data['until'])}"
        )
        return touched

    def apply_override_ended(self, payload: Mapping[str, Any]) -> int:
        """Mark an override as ended (it lingers ``linger_minutes``); rows touched.

        An "ended" for an override without rows is remembered (see the contract
        above) and ends the rows of a "started" that commits meanwhile."""
        now = self._now()
        data = parse_override_ended(payload, now)

        def _tx() -> int:
            with get_session() as s:
                query = select(MaintenanceRecord).where(
                    MaintenanceRecord.kind == MAINT_KIND_OVERRIDE,
                    MaintenanceRecord.override_id == data["override_id"],
                )
                if data["source"] is not None:
                    query = query.where(MaintenanceRecord.source == data["source"])
                rows = s.execute(query).scalars().all()
                linger = data["linger_minutes"]
                if not rows:
                    # Unknown override: the "started" has not been processed yet
                    # (or was ignored). Remember the end for the linger period;
                    # the first tombstone wins on re-delivery. A lost insert race
                    # raises IntegrityError → re-run → no-op.
                    key_source = data["source"] or ""
                    known = s.execute(
                        select(MaintenanceTombstone).where(
                            MaintenanceTombstone.source == key_source,
                            MaintenanceTombstone.override_id == data["override_id"],
                        )
                    ).scalar_one_or_none()
                    if known is not None and known.expires_at > _to_db(now):
                        return 0
                    if known is not None:  # expired leftover of a reused id
                        s.delete(known)
                        s.flush()
                    keep = DEFAULT_LINGER_MINUTES if linger is None else max(0, linger)
                    ended = _to_db(data["ended_at"])
                    s.add(MaintenanceTombstone(
                        source=key_source, override_id=data["override_id"],
                        ended_at=ended, linger_minutes=keep,
                        expires_at=ended + timedelta(minutes=keep),
                    ))
                    s.commit()
                    return 0
                touched = 0
                for row in rows:
                    if row.ended_at is not None:
                        continue  # re-delivered "ended": the first end wins
                    row.ended_at = _to_db(data["ended_at"])
                    if linger is not None:
                        row.linger_minutes = linger
                    elif row.linger_minutes is None:
                        row.linger_minutes = DEFAULT_LINGER_MINUTES
                    touched += 1
                s.commit()
                return touched

        try:
            touched = retry_write(_tx)
        except IntegrityError:
            touched = retry_write(_tx)
        if not touched:
            # Tombstone written (or already there): the "started" may have
            # committed meanwhile — close its rows now.
            touched = self._apply_tombstone(data["source"], data["override_id"], now)
        if touched:
            log.info(
                f"Maintenance: override {data['override_id']} ended for {touched} CI(s)"
            )
        else:
            log.debug(
                f"Maintenance: override {data['override_id']} ended but unknown here"
            )
        self._maybe_purge()
        return touched

    def purge_expired(self, now: datetime | None = None) -> int:
        """Delete overrides whose effective end lies more than ``PURGE_GRACE`` in
        the past. Windows never expire (``once`` windows are replaced by the
        next publish of their source)."""
        at = _aware(now) or self._now()
        cutoff = at - PURGE_GRACE
        cutoff_db = _to_db(cutoff)

        def _tx() -> int:
            with get_session() as s:
                candidates = s.execute(
                    select(MaintenanceRecord).where(
                        MaintenanceRecord.kind == MAINT_KIND_OVERRIDE,
                        or_(
                            MaintenanceRecord.until < cutoff_db,
                            MaintenanceRecord.ended_at < cutoff_db,
                        ),
                    )
                ).scalars()
                doomed: list[int] = []
                for row in candidates:
                    end = override_effective_end(
                        _aware(row.until), _aware(row.ended_at), row.linger_minutes
                    )
                    if end is not None and end < cutoff:
                        doomed.append(row.id)
                for chunk in _chunks(doomed):
                    s.execute(
                        delete(MaintenanceRecord).where(MaintenanceRecord.id.in_(chunk))
                    )
                s.execute(
                    delete(MaintenanceTombstone).where(
                        MaintenanceTombstone.expires_at < _to_db(at)
                    )
                )
                s.commit()
                return len(doomed)

        purged = retry_write(_tx)
        self._last_purge = time.monotonic()
        return purged

    def _maybe_purge(self) -> None:
        """Sweep expired overrides at most every ``_PURGE_INTERVAL_S`` per process."""
        if (
            self._last_purge is not None
            and time.monotonic() - self._last_purge < _PURGE_INTERVAL_S
        ):
            return
        try:
            purged = self.purge_expired()
        except Exception as exc:  # housekeeping must never fail the event
            log.warning(f"Maintenance: purge of expired overrides failed — {exc}")
            self._last_purge = time.monotonic()
            return
        if purged:
            log.info(f"Maintenance: purged {purged} expired override row(s)")

    # ── Reader ──

    def maintenance_for(
        self, ci_ids: Iterable[str], now: datetime | None = None
    ) -> dict[str, dict]:
        """Read-model per ``ci_id`` from one batch query (``IN``, chunked).

        Every requested id gets an entry — a CI without any rows is simply
        "not in maintenance, nothing scheduled".
        """
        wanted = [ci_id for ci_id in dict.fromkeys(ci_ids) if ci_id]
        at = _aware(now) or self._now()
        buckets: dict[str, tuple[list[MaintenanceRecord], list[MaintenanceRecord]]] = {
            ci_id: ([], []) for ci_id in wanted
        }
        if not wanted:
            return {}
        with get_session() as s:
            for chunk in _chunks(wanted):
                rows = s.execute(
                    select(MaintenanceRecord).where(MaintenanceRecord.ci_id.in_(chunk))
                ).scalars()
                for row in rows:
                    bucket = buckets.get(row.ci_id)
                    if bucket is None:
                        continue
                    (bucket[0] if row.kind == MAINT_KIND_WINDOW else bucket[1]).append(
                        row
                    )
            return {
                ci_id: evaluate(windows, overrides, at)
                for ci_id, (windows, overrides) in buckets.items()
            }
