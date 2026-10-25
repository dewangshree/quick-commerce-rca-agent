"""initial schema: business data, app tables, RAG store

Revision ID: 0001
Revises:
Create Date: 2026-10-25
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from qc_rca.config import get_settings

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "cities",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("normalized_name", sa.String(120), nullable=False, unique=True),
    )
    op.create_table(
        "stores",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("city_id", sa.Integer, sa.ForeignKey("cities.id", ondelete="RESTRICT"), nullable=False),
    )
    op.create_index("ix_stores_city_id", "stores", ["city_id"])

    op.create_table(
        "rider_slots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("store_id", sa.Integer, sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("charge_date", sa.Date, nullable=False),
        sa.Column("slot_name", sa.String(80), nullable=False),
        sa.Column("slot_start", sa.Time, nullable=False),
        sa.Column("slot_end", sa.Time, nullable=False),
        sa.Column("slot_type", sa.String(32), nullable=False),
        sa.Column("original_size", sa.Integer, nullable=False),
        sa.Column("current_size", sa.Integer, nullable=False),
        sa.Column("booked_size", sa.Integer, nullable=False),
        sa.Column("completed_count", sa.Integer, nullable=False),
        sa.Column("incompleted_count", sa.Integer, nullable=False),
        sa.Column("noshow_count", sa.Integer, nullable=False),
        sa.Column("cancelled_count", sa.Integer, nullable=False),
        sa.Column("original_capacity_booked", sa.Double, nullable=False),
        sa.Column("current_capacity_booked", sa.Double, nullable=False),
        sa.Column("rider_minutes", sa.BigInteger, nullable=True),
        sa.Column("booked_minutes", sa.BigInteger, nullable=False),
        sa.Column("man_hour", sa.Double, nullable=True),
        sa.UniqueConstraint("store_id", "charge_date", "slot_name", name="uq_rider_slots_store_date_slot"),
        sa.CheckConstraint("current_size >= 0 AND booked_size >= 0 AND original_size >= 0", name="ck_rider_slots_sizes"),
        sa.CheckConstraint("noshow_count >= 0", name="ck_rider_slots_noshow"),
    )

    op.create_table(
        "store_hourly_metrics",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("store_id", sa.Integer, sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slot_id", sa.Integer, sa.ForeignKey("rider_slots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("charge_date", sa.Date, nullable=False),
        sa.Column("hour", sa.SmallInteger, nullable=False),
        sa.Column("hour_start", sa.DateTime, nullable=False),
        sa.Column("hour_end", sa.DateTime, nullable=False),
        sa.Column("total_orders", sa.Integer, nullable=False),
        sa.Column("breached_count", sa.Integer, nullable=False),
        sa.Column("breached_rate", sa.Double, nullable=False),
        sa.Column("is_problem_hour", sa.Boolean, nullable=False),
        sa.Column("pileup_count", sa.Integer, nullable=False),
        sa.Column("pileup_flag", sa.Boolean, nullable=False),
        sa.Column("avg_or2a", sa.Double, nullable=True),
        sa.Column("o2a", sa.Double, nullable=True),
        sa.Column("completed_order_count", sa.Integer, nullable=False),
        sa.Column("cancelled_order_count", sa.Integer, nullable=False),
        sa.Column("rto_order_count", sa.Integer, nullable=False),
        sa.Column("order_projection", sa.Double, nullable=True),
        sa.UniqueConstraint("store_id", "charge_date", "hour", name="uq_hourly_store_date_hour"),
        sa.CheckConstraint("hour BETWEEN 0 AND 23", name="ck_hourly_hour_range"),
        sa.CheckConstraint("total_orders >= 0 AND breached_count >= 0", name="ck_hourly_nonneg"),
        sa.CheckConstraint("breached_count <= total_orders", name="ck_hourly_breached_le_total"),
        sa.CheckConstraint("pileup_count >= 0", name="ck_hourly_pileup_nonneg"),
    )
    op.create_index("ix_hourly_date_store", "store_hourly_metrics", ["charge_date", "store_id"])
    op.create_index("ix_hourly_date_problem", "store_hourly_metrics", ["charge_date", "is_problem_hour"])
    op.create_index("ix_store_hourly_metrics_slot_id", "store_hourly_metrics", ["slot_id"])

    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("row_count", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ingestion_runs_sha256", "ingestion_runs", ["sha256"])

    user_role = postgresql.ENUM("viewer", "analyst", "admin", name="user_role")
    op.create_table(
        "api_users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("role", user_role, nullable=False),
        sa.Column("key_prefix", sa.String(12), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=True),
        sa.Column("user_name", sa.String(80), nullable=True),
        sa.Column("role", sa.String(16), nullable=True),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("path", sa.String(255), nullable=False),
        sa.Column("status_code", sa.SmallInteger, nullable=False),
        sa.Column("latency_ms", sa.Double, nullable=False),
        sa.Column("client_ip", sa.String(64), nullable=True),
        sa.Column("details", postgresql.JSONB, nullable=True),
    )
    op.create_index("ix_audit_ts", "audit_logs", ["ts"])

    op.create_table(
        "investigations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_name", sa.String(80), nullable=True),
        sa.Column("thread_id", sa.String(64), nullable=True),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("intent", sa.String(40), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("llm_model", sa.String(80), nullable=True),
        sa.Column("llm_used", sa.Boolean, nullable=False),
        sa.Column("latency_ms", sa.Double, nullable=True),
        sa.Column("report", postgresql.JSONB, nullable=True),
        sa.Column("metrics", postgresql.JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_index("ix_investigations_created", "investigations", ["created_at"])
    op.create_index("ix_investigations_user_name", "investigations", ["user_name"])

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_path", sa.String(255), nullable=False, unique=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("doc_type", sa.String(40), nullable=False),
        sa.Column("is_sample", sa.Boolean, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("embedding_model", sa.String(80), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_documents_doc_type", "documents", ["doc_type"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("heading", sa.String(255), nullable=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column("embedding", Vector(get_settings().embedding_dim), nullable=True),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR,
            sa.Computed("to_tsvector('english', coalesce(heading, '') || ' ' || content)", persisted=True),
        ),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_chunk_doc_index"),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index("ix_chunks_tsv", "document_chunks", ["tsv"], postgresql_using="gin")
    op.create_index(
        "ix_chunks_embedding_hnsw", "document_chunks", ["embedding"],
        postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    for table in ("document_chunks", "documents", "investigations", "audit_logs", "api_users",
                  "ingestion_runs", "store_hourly_metrics", "rider_slots", "stores", "cities"):
        op.drop_table(table)
    op.execute("DROP TYPE IF EXISTS user_role")
