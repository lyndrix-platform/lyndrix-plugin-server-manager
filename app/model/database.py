"""Database helpers for the Server Manager plugin."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy.orm import Session

from core.api import db_instance, get_logger

from .models import _Base

log = get_logger("Plugin:ServerManager:DB")


def ensure_tables() -> None:
    """Create server manager tables and migrate new columns (idempotent)."""
    if not db_instance.is_connected or not db_instance.engine:
        raise RuntimeError("Database engine not ready — call after db:connected event")
    _Base.metadata.create_all(db_instance.engine, checkfirst=True)
    _migrate_columns()
    log.info("DB: server_manager_servers table ready")


def _migrate_columns() -> None:
    """Add columns that were introduced after initial table creation."""
    from sqlalchemy import text, inspect

    insp = inspect(db_instance.engine)
    existing = {col["name"] for col in insp.get_columns("server_manager_servers")}
    new_cols = {
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
    with db_instance.engine.begin() as conn:
        for col, col_type in new_cols.items():
            if col not in existing:
                # Types that already carry NOT NULL keep it; the rest are nullable.
                nullability = "" if "NOT NULL" in col_type else " NULL"
                conn.execute(text(
                    f"ALTER TABLE server_manager_servers ADD COLUMN {col} {col_type}{nullability}"
                ))
                log.info(f"DB: migrated — added column '{col}' to server_manager_servers")

    # create_all() never adds constraints to an existing table, so the ingest
    # idempotency key is backfilled here for installs that predate it.
    indexes = {ix["name"] for ix in insp.get_indexes("server_manager_servers")}
    indexes |= {uc["name"] for uc in insp.get_unique_constraints("server_manager_servers")}
    if "uq_server_manager_source_ext" not in indexes:
        with db_instance.engine.begin() as conn:
            conn.execute(text(
                "CREATE UNIQUE INDEX uq_server_manager_source_ext "
                "ON server_manager_servers (source, external_id)"
            ))
        log.info("DB: migrated — added unique index uq_server_manager_source_ext")
    if "ix_server_manager_servers_ci_id" not in indexes:
        with db_instance.engine.begin() as conn:
            conn.execute(text(
                "CREATE INDEX ix_server_manager_servers_ci_id ON server_manager_servers (ci_id)"
            ))
        log.info("DB: migrated — added index ix_server_manager_servers_ci_id")


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session, rolling back on exception."""
    if not db_instance.is_connected or not db_instance.SessionLocal:
        raise RuntimeError("Database not connected")
    session: Session = db_instance.SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
