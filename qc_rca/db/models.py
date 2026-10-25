"""SQLAlchemy ORM models.

Business data is normalised from the flat gold CSV into:
  cities 1─* stores 1─* rider_slots 1─* store_hourly_metrics
Rider capacity columns are constant within a (store, date, slot) in the
source data, so they live on ``rider_slots`` instead of being repeated per hour.

Source column spellings ``orginal_size`` / ``orginal_capacity_booked`` are
stored as ``original_size`` / ``original_capacity_booked``.
"""
import enum
import uuid
from datetime import date, datetime, time

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Double,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from qc_rca.config import get_settings

EMBEDDING_DIM = get_settings().embedding_dim


class Base(DeclarativeBase):
    pass


# ── Business data ────────────────────────────────────────────────────────────

class City(Base):
    __tablename__ = "cities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    # lower-cased with collapsed whitespace, e.g. "Mumbai  north" -> "mumbai north"
    normalized_name: Mapped[str] = mapped_column(String(120), unique=True)

    stores: Mapped[list["Store"]] = relationship(back_populates="city")


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id", ondelete="RESTRICT"), index=True)

    city: Mapped[City] = relationship(back_populates="stores")


class RiderSlot(Base):
    __tablename__ = "rider_slots"
    __table_args__ = (
        UniqueConstraint("store_id", "charge_date", "slot_name", name="uq_rider_slots_store_date_slot"),
        CheckConstraint("current_size >= 0 AND booked_size >= 0 AND original_size >= 0", name="ck_rider_slots_sizes"),
        CheckConstraint("noshow_count >= 0", name="ck_rider_slots_noshow"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"))
    charge_date: Mapped[date] = mapped_column(Date)
    slot_name: Mapped[str] = mapped_column(String(80))
    slot_start: Mapped[time] = mapped_column(Time)
    slot_end: Mapped[time] = mapped_column(Time)
    slot_type: Mapped[str] = mapped_column(String(32))
    original_size: Mapped[int] = mapped_column(Integer)
    current_size: Mapped[int] = mapped_column(Integer)
    booked_size: Mapped[int] = mapped_column(Integer)
    completed_count: Mapped[int] = mapped_column(Integer)
    incompleted_count: Mapped[int] = mapped_column(Integer)
    noshow_count: Mapped[int] = mapped_column(Integer)
    cancelled_count: Mapped[int] = mapped_column(Integer)
    original_capacity_booked: Mapped[float] = mapped_column(Double)
    current_capacity_booked: Mapped[float] = mapped_column(Double)
    # Source columns are named *_hours_per_hour but hold minutes.
    rider_minutes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    booked_minutes: Mapped[int] = mapped_column(BigInteger)
    man_hour: Mapped[float | None] = mapped_column(Double, nullable=True)


class StoreHourlyMetric(Base):
    __tablename__ = "store_hourly_metrics"
    __table_args__ = (
        UniqueConstraint("store_id", "charge_date", "hour", name="uq_hourly_store_date_hour"),
        CheckConstraint("hour BETWEEN 0 AND 23", name="ck_hourly_hour_range"),
        CheckConstraint("total_orders >= 0 AND breached_count >= 0", name="ck_hourly_nonneg"),
        CheckConstraint("breached_count <= total_orders", name="ck_hourly_breached_le_total"),
        CheckConstraint("pileup_count >= 0", name="ck_hourly_pileup_nonneg"),
        Index("ix_hourly_date_store", "charge_date", "store_id"),
        Index("ix_hourly_date_problem", "charge_date", "is_problem_hour"),
        Index("ix_hourly_synthetic_date", "is_synthetic", "charge_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"))
    slot_id: Mapped[int] = mapped_column(ForeignKey("rider_slots.id", ondelete="RESTRICT"), index=True)
    charge_date: Mapped[date] = mapped_column(Date)
    hour: Mapped[int] = mapped_column(SmallInteger)
    hour_start: Mapped[datetime] = mapped_column(DateTime)
    hour_end: Mapped[datetime] = mapped_column(DateTime)
    total_orders: Mapped[int] = mapped_column(Integer)
    breached_count: Mapped[int] = mapped_column(Integer)
    breached_rate: Mapped[float] = mapped_column(Double)
    is_problem_hour: Mapped[bool] = mapped_column(Boolean)
    pileup_count: Mapped[int] = mapped_column(Integer)
    pileup_flag: Mapped[bool] = mapped_column(Boolean)
    avg_or2a: Mapped[float | None] = mapped_column(Double, nullable=True)
    o2a: Mapped[float | None] = mapped_column(Double, nullable=True)
    completed_order_count: Mapped[int] = mapped_column(Integer)
    cancelled_order_count: Mapped[int] = mapped_column(Integer)
    rto_order_count: Mapped[int] = mapped_column(Integer)
    order_projection: Mapped[float | None] = mapped_column(Double, nullable=True)
    # True for generated demo data (scripts/generate_synthetic_days.py), never real operations data.
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    store: Mapped[Store] = relationship()
    slot: Mapped[RiderSlot] = relationship()


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32), default="orders_csv")
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16))  # success | failed | skipped
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ── Application data ─────────────────────────────────────────────────────────

class Role(str, enum.Enum):
    viewer = "viewer"     # dashboards / read analytics
    analyst = "analyst"   # + investigations, knowledge search
    admin = "admin"       # + ingestion, audit log, user management


class ApiUser(Base):
    __tablename__ = "api_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    role: Mapped[Role] = mapped_column(Enum(Role, name="user_role"))
    key_prefix: Mapped[str] = mapped_column(String(12))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_ts", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    user_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(255))
    status_code: Mapped[int] = mapped_column(SmallInteger)
    latency_ms: Mapped[float] = mapped_column(Double)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class Investigation(Base):
    __tablename__ = "investigations"
    __table_args__ = (Index("ix_investigations_created", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_name: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    question: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(32))  # completed | partial | rejected | needs_clarification
    llm_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    llm_used: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[float | None] = mapped_column(Double, nullable=True)
    report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_path: Mapped[str] = mapped_column(String(255), unique=True)
    title: Mapped[str] = mapped_column(String(255))
    doc_type: Mapped[str] = mapped_column(String(40), index=True)  # metric_definition | playbook | schema | sla | sop | incident
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    sha256: Mapped[str] = mapped_column(String(64))
    embedding_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunk_doc_index"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    extra: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    tsv = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(heading, '') || ' ' || content)", persisted=True),
    )

    document: Mapped[Document] = relationship(back_populates="chunks")
