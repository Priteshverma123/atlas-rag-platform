"""SQLAlchemy 2.0 models. Tenant-owned tables carry ``tenant_id`` and are protected by Postgres
row-level security policies (see migrations/versions/0001_initial.py). The application connects
as the non-owner role ``atlas_app``, so RLS always applies to it."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[str]: ARRAY(String), uuid.UUID: UUID(as_uuid=True)}


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _tenant() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200), unique=True)
    plan: Mapped[str] = mapped_column(String(50), default="free")
    monthly_budget_usd: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = _created()


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    name: Mapped[str] = mapped_column(String(200))
    key_prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[str]] = mapped_column(default=list)
    user_external_id: Mapped[str | None] = mapped_column(String(200))
    rpm: Mapped[int] = mapped_column(Integer, default=600)
    tpm: Mapped[int] = mapped_column(Integer, default=400_000)
    budget_usd_day: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = _created()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "external_id"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    external_id: Mapped[str] = mapped_column(String(200))
    groups: Mapped[list[str]] = mapped_column(default=list)
    created_at: Mapped[datetime] = _created()


class Collection(Base):
    __tablename__ = "collections"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    embedding_model: Mapped[str] = mapped_column(String(200))
    embed_dim: Mapped[int] = mapped_column(Integer)
    chunking: Mapped[dict[str, Any]] = mapped_column(default=dict)
    retrieval: Mapped[dict[str, Any]] = mapped_column(default=dict)
    qdrant_collection: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = _created()


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("collection_id", "source_uri"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    collection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("collections.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(500), default="")
    source_uri: Mapped[str] = mapped_column(String(2000))
    mime: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(30), default="received", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    acl_groups: Mapped[list[str]] = mapped_column(default=list)
    doc_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    page_count: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class DocumentStage(Base):
    __tablename__ = "document_stages"
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    stage: Mapped[str] = mapped_column(String(30), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = _tenant()
    status: Mapped[str] = mapped_column(String(20))  # running | done | failed | skipped
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    cache_key: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict[str, Any]] = mapped_column(default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (Index("ix_chunks_doc_version", "document_id", "version"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    collection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("collections.id", ondelete="CASCADE"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(20), default="text")  # text | table | figure | parent
    text: Mapped[str] = mapped_column(Text)
    context_text: Mapped[str | None] = mapped_column(Text)
    section_path: Mapped[str] = mapped_column(Text, default="")
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    bboxes: Mapped[dict[str, Any]] = mapped_column(default=dict)
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    token_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    collection_ids: Mapped[list[str]] = mapped_column(default=list)
    title: Mapped[str] = mapped_column(String(300), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    summary_upto_seq: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_id", "seq"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[dict[str, Any]] = mapped_column(default=dict)
    verification: Mapped[dict[str, Any]] = mapped_column(default=dict)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created()


class MemoryFact(Base):
    """Long-term user memory with bi-temporal validity and provenance."""

    __tablename__ = "memory_facts"
    __table_args__ = (Index("ix_memory_user_valid", "tenant_id", "user_id", "invalid_at"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    fact: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40), default="fact")  # preference | fact | goal | context
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    invalid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()


class LlmCall(Base):
    """Usage ledger (written asynchronously by the gateway's spend flusher)."""

    __tablename__ = "llm_calls"
    __table_args__ = (Index("ix_llm_calls_tenant_ts", "tenant_id", "ts"),)
    id: Mapped[uuid.UUID] = _pk()
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    route: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(200))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    uncached_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    ttft_ms: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30))
    error_kind: Mapped[str | None] = mapped_column(String(30))
    cache: Mapped[str] = mapped_column(String(20), default="miss")
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    trace_id: Mapped[str | None] = mapped_column(String(32))


class ResearchJob(Base):
    __tablename__ = "research_jobs"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    question: Mapped[str] = mapped_column(Text)
    effort: Mapped[str] = mapped_column(String(10), default="medium")
    collection_ids: Mapped[list[str]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    budget: Mapped[dict[str, Any]] = mapped_column(default=dict)
    spent: Mapped[dict[str, Any]] = mapped_column(default=dict)
    report: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant()
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    rating: Mapped[int] = mapped_column(SmallInteger)  # -1 | 1
    comment: Mapped[str] = mapped_column(Text, default="")
    corrected_answer: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class EvalRun(Base):
    __tablename__ = "eval_runs"
    id: Mapped[uuid.UUID] = _pk()
    git_sha: Mapped[str] = mapped_column(String(64), default="")
    dataset: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = _created()


TENANT_TABLES = ["api_keys", "users", "collections", "documents", "document_stages", "chunks", "conversations",
                 "messages", "memory_facts", "research_jobs", "feedback"]
