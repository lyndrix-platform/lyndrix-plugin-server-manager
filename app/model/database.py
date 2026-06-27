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
    }
    with db_instance.engine.begin() as conn:
        for col, col_type in new_cols.items():
            if col not in existing:
                conn.execute(text(
                    f"ALTER TABLE server_manager_servers ADD COLUMN {col} {col_type} NULL"
                ))
                log.info(f"DB: migrated — added column '{col}' to server_manager_servers")


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
