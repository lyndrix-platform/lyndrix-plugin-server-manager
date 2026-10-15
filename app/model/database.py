"""Database helpers for the Server Manager plugin."""
from __future__ import annotations

import time
from contextlib import contextmanager, nullcontext
from typing import Callable, Generator, TypeVar

from sqlalchemy.orm import Session

from core.api import db_instance, get_logger

from .models import _Base

log = get_logger("Plugin:ServerManager:DB")

T = TypeVar("T")


# MariaDB error codes a lost bootstrap race produces: a peer instance created the
# table (1050), added the column (1060) or created the index (1061) between our
# inspect and our DDL. All three mean "a peer did it" and are safe to ignore.
_DDL_RACE_CODES = ("1050", "1060", "1061")
_DDL_RACE_TEXT = ("already exists", "duplicate column", "duplicate key name")

# MariaDB >= 11.6 runs innodb_snapshot_isolation=ON: a REPEATABLE READ transaction
# that updates a row another transaction changed since its first read fails with
# error 1020 instead of waiting. Sessions here run READ COMMITTED and the short
# writers retry on 1020 / 1205 (lock wait timeout) / 1213 (deadlock).
_RETRY_CODES = ("1020", "1205", "1213")

_TABLE = "server_manager_servers"

# Columns introduced after the initial table creation (name → DDL type).
_NEW_COLUMNS = {
    "product_id": "VARCHAR(64)",
    "os_type": "VARCHAR(32)",
    "service_class_id": "VARCHAR(16)",
    "os_family_id": "VARCHAR(16)",
    "os_version_id": "VARCHAR(32)",
    "source": "VARCHAR(64)",
    "external_id": "VARCHAR(255)",
    "writeback": "BOOLEAN NOT NULL DEFAULT 0",
    "sync_state": "VARCHAR(16)",
    "sync_error": "TEXT",
    "ci_id": "VARCHAR(36)",
}

# Indexes create_all() never adds to an existing table (name → (unique, columns)).
_INDEXES = {
    "uq_server_manager_source_ext": (True, "source, external_id"),
    "ix_server_manager_servers_ci_id": (False, "ci_id"),
}


def _is_ddl_race(exc: Exception) -> bool:
    msg = str(getattr(exc, "orig", exc))
    return any(code in msg for code in _DDL_RACE_CODES) or any(
        t in msg.lower() for t in _DDL_RACE_TEXT
    )


def _is_retryable(exc: Exception) -> bool:
    return any(code in str(getattr(exc, "orig", exc)) for code in _RETRY_CODES)


def _is_mariadb(engine) -> bool:
    dialect = engine.dialect
    return dialect.name == "mariadb" or bool(getattr(dialect, "is_mariadb", False))


def _bootstrap_lock():
    """``db_instance.advisory_lock("lyndrix_bootstrap")`` on cores that have it."""
    lock = getattr(db_instance, "advisory_lock", None)
    return lock("lyndrix_bootstrap") if callable(lock) else nullcontext()


def ensure_tables() -> None:
    """Create server manager tables and migrate new columns (idempotent).

    Cluster-safe: serialized across booting instances by the core's bootstrap
    advisory lock, every statement is ``IF NOT EXISTS`` where the dialect
    supports it, and a lost race (1050/1060/1061) counts as "a peer did it".
    Blocking — call it via ``asyncio.to_thread``.
    """
    if not db_instance.is_connected or not db_instance.engine:
        raise RuntimeError("Database engine not ready — call after db:connected event")
    with _bootstrap_lock():
        try:
            _Base.metadata.create_all(db_instance.engine, checkfirst=True)
        except Exception as exc:
            if not _is_ddl_race(exc):
                raise
            log.info(f"DB: create_all lost a bootstrap race ({exc}); re-checking")
            _Base.metadata.create_all(db_instance.engine, checkfirst=True)
        _migrate_columns()
    log.info(f"DB: {_TABLE} table ready")


def _run_ddl(conn, statement: str) -> bool:
    """Execute one DDL statement; ``False`` when a peer already applied it."""
    from sqlalchemy import text

    try:
        conn.execute(text(statement))
        return True
    except Exception as exc:
        if _is_ddl_race(exc):
            log.info(f"DB: skipped (already applied by a peer): {statement}")
            return False
        raise


def _migrate_columns() -> None:
    """Add columns and indexes that were introduced after initial table creation."""
    from sqlalchemy import inspect

    engine = db_instance.engine
    mariadb = _is_mariadb(engine)
    insp = inspect(engine)
    existing = {col["name"] for col in insp.get_columns(_TABLE)}

    # NOTE: every identifier below is a module constant — never interpolate
    # request/payload data into these statements.
    for col, col_type in _NEW_COLUMNS.items():
        if col in existing:
            continue
        # Types that already carry NOT NULL keep it; the rest are nullable.
        nullability = "" if "NOT NULL" in col_type else " NULL"
        if_not_exists = "IF NOT EXISTS " if mariadb else ""
        # One transaction per statement: MariaDB DDL auto-commits anyway, and a
        # race-skipped statement must not abort the following ones.
        with engine.begin() as conn:
            if _run_ddl(conn, f"ALTER TABLE {_TABLE} ADD COLUMN {if_not_exists}{col} {col_type}{nullability}"):
                log.info(f"DB: migrated — added column '{col}' to {_TABLE}")

    indexes = {ix["name"] for ix in insp.get_indexes(_TABLE)}
    indexes |= {uc["name"] for uc in insp.get_unique_constraints(_TABLE)}
    supports_if_not_exists = mariadb or engine.dialect.name == "sqlite"
    for name, (unique, columns) in _INDEXES.items():
        if name in indexes:
            continue
        kind = "UNIQUE INDEX" if unique else "INDEX"
        if_not_exists = "IF NOT EXISTS " if supports_if_not_exists else ""
        with engine.begin() as conn:
            if _run_ddl(conn, f"CREATE {kind} {if_not_exists}{name} ON {_TABLE} ({columns})"):
                log.info(f"DB: migrated — added index {name}")


def retry_write(fn: Callable[[], T], *, attempts: int = 4, pause: float = 0.05) -> T:
    """Run ``fn()`` — one complete short transaction — again on 1020/1205/1213.

    ``fn`` must open its own session so every attempt starts from fresh reads.
    """
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            if attempt + 1 < attempts and _is_retryable(exc):
                log.debug(f"DB: retrying write after {exc!s:.120}")
                time.sleep(pause * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable")  # pragma: no cover


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Yield a READ COMMITTED session (no-op on SQLite), rolling back on exception."""
    if not db_instance.is_connected or not db_instance.SessionLocal:
        raise RuntimeError("Database not connected")
    session: Session = db_instance.SessionLocal()
    try:
        _read_committed(session)
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _read_committed(session: Session) -> None:
    """Pin the session to READ COMMITTED on MySQL/MariaDB (see _RETRY_CODES)."""
    try:
        bind = session.get_bind()
        if bind is not None and bind.dialect.name in ("mysql", "mariadb"):
            session.connection(execution_options={"isolation_level": "READ COMMITTED"})
    except Exception as exc:  # isolation is an optimisation, never a reason to fail
        log.debug(f"DB: READ COMMITTED not applied: {exc}")
