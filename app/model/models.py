"""SQLAlchemy model for the Server Manager plugin."""
from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, JSON, String, Text, func
from sqlalchemy.orm import DeclarativeBase


class _Base(DeclarativeBase):
    pass


class ServerRecord(_Base):
    __tablename__ = "server_manager_servers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    hostname = Column(String(255), nullable=True, index=True)
    environment_id = Column(String(64), nullable=False)
    server_type = Column(String(64), nullable=False)  # physical | vm | container
    product_id = Column(String(64), nullable=True)     # e.g. edc-standard, fce-mirrored
    service_class_id = Column(String(16), nullable=True)  # e.g. SK1..SK4
    os_family_id = Column(String(16), nullable=True)      # WIN | RHEL | CRHEL | SOL
    os_version_id = Column(String(32), nullable=True)     # e.g. win2022, rhel9
    os_type = Column(String(32), nullable=True)        # windows | linux | unix | unknown
    # active | decommissioned | ordered | provisioning
    status = Column(String(64), nullable=False, default="active")
    hardware_profile = Column(JSON, nullable=False, default=dict)
    tags = Column(JSON, nullable=False, default=list)
    notes = Column(Text, nullable=True)
    ordered_by = Column(String(255), nullable=True)
    ordered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "hostname": self.hostname,
            "environment_id": self.environment_id,
            "server_type": self.server_type,
            "product_id": self.product_id,
            "service_class_id": self.service_class_id,
            "os_family_id": self.os_family_id,
            "os_version_id": self.os_version_id,
            "os_type": self.os_type,
            "status": self.status,
            "hardware_profile": self.hardware_profile or {},
            "tags": self.tags or [],
            "notes": self.notes,
            "ordered_by": self.ordered_by,
            "ordered_at": self.ordered_at.isoformat() if self.ordered_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
