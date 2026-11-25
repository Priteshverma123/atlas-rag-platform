"""Request/response schemas for the public REST API."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200, pattern=r"^[\w\-. ]+$")
    description: str = ""
    chunking: dict[str, Any] | None = Field(None, examples=[{"template": "auto"}])
    retrieval: dict[str, Any] | None = Field(None, examples=[{"weights": {"dense": 1, "sparse": 1}, "top_k": 6,
                                                               "rerank": True, "score_threshold": 0.0}])


class CollectionUpdate(BaseModel):
    chunking: dict[str, Any] | None = None
    retrieval: dict[str, Any] | None = None


class TextDocument(BaseModel):
    title: str = Field(max_length=500)
    text: str = Field(min_length=1, max_length=5_000_000)
    source_id: str | None = Field(None, description="stable id; re-posting the same id creates a new version")
    acl_groups: list[str] | None = None
    metadata: dict[str, Any] | None = None


class UrlDocument(BaseModel):
    url: HttpUrl
    acl_groups: list[str] | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    collection_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
    top_k: int = Field(8, ge=1, le=50)
    mode: Literal["hybrid", "dense", "sparse"] = "hybrid"
    rerank: bool = True
    debug: bool = False


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    collection_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
    conversation_id: uuid.UUID | None = None
    mode: Literal["fast", "standard", "deep"] = "standard"
    stream: bool = True
    top_k: int | None = Field(None, ge=1, le=20)
    use_cache: bool = True
    use_memory: bool = True


class ResearchRequest(BaseModel):
    question: str = Field(min_length=5, max_length=4000)
    collection_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
    effort: Literal["low", "medium", "high"] = "medium"


class FeedbackRequest(BaseModel):
    message_id: uuid.UUID
    rating: Literal[-1, 1]
    comment: str = Field("", max_length=4000)
    corrected_answer: str | None = Field(None, max_length=20000)


class BootstrapRequest(BaseModel):
    tenant_name: str = Field(min_length=1, max_length=200)
    monthly_budget_usd: float | None = None


class KeyCreate(BaseModel):
    name: str
    scopes: list[Literal["read", "write", "llm", "admin", "impersonate"]] = ["read", "write", "llm"]
    rpm: int = Field(600, ge=0)
    tpm: int = Field(400_000, ge=0)
    budget_usd_day: float | None = None
    user_external_id: str | None = None
