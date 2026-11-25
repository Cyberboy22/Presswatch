"""Database schema (SQLAlchemy 2.0). Works with PostgreSQL and SQLite.

Timestamps are stored as naive UTC so both databases behave the same.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Cycle(Base):
    """One press cycle as received from the machine (or from the initial DoE)."""

    __tablename__ = "cycles"
    __table_args__ = (
        UniqueConstraint("machine_id", "source", "machine_cycle_id", name="uq_cycle"),
        Index("ix_cycles_source_id", "source", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    machine_id: Mapped[str] = mapped_column(String(64))
    machine_cycle_id: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16))  # "production" | "doe"
    recipe: Mapped[str] = mapped_column(String(64))
    produced_at: Mapped[datetime] = mapped_column(DateTime)
    received_at: Mapped[datetime] = mapped_column(DateTime)
    params: Mapped[dict] = mapped_column(JSONType)
    gob_mass_mg: Mapped[float] = mapped_column(Float)
    curves: Mapped[dict] = mapped_column(JSONType)
    features: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    issues: Mapped[list] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(16))  # "ok" | "warning" | "quarantined"

    predictions: Mapped[list[Prediction]] = relationship(
        back_populates="cycle", order_by="Prediction.id", cascade="all, delete-orphan"
    )
    measurement: Mapped[Measurement | None] = relationship(
        back_populates="cycle", uselist=False, cascade="all, delete-orphan"
    )


class Prediction(Base):
    """A prediction is traceable: which model version saw exactly which inputs."""

    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("cycles.id"), index=True)
    model_version: Mapped[str] = mapped_column(String(32), index=True)
    center_thickness_mm: Mapped[float] = mapped_column(Float)
    pv_um: Mapped[float] = mapped_column(Float)
    in_spec: Mapped[bool] = mapped_column(Boolean)
    out_of_range: Mapped[list] = mapped_column(JSONType, default=list)
    input_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)

    cycle: Mapped[Cycle] = relationship(back_populates="predictions")


class Measurement(Base):
    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("cycles.id"), unique=True)
    center_thickness_mm: Mapped[float] = mapped_column(Float)
    pv_um: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32))
    measured_at: Mapped[datetime] = mapped_column(DateTime)

    cycle: Mapped[Cycle] = relationship(back_populates="measurement")


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String(32), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))  # active | proposed | archived | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    parent_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    metrics: Mapped[dict] = mapped_column(JSONType)
    card: Mapped[dict] = mapped_column(JSONType)
    artifact: Mapped[bytes] = mapped_column(LargeBinary)
    artifact_sha256: Mapped[str] = mapped_column(String(64))


class AuditEntry(Base):
    """Append-only log of who did what and why."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
