"""RawSpan → observation row (+ scores). Pulse attributes first, GenAI semconv as fallback."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pulse import conventions as C
from pulse.server.otlp import RawSpan

_SKIP_META = {"http.request.header.authorization", "http.request.header.cookie"}


def _first(attrs: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if attrs.get(k) not in (None, ""):
            return attrs[k]
    return None


def _text(v: Any) -> str | None:
    if v is None:
        return None
    s = v if isinstance(v, str) else json.dumps(v, default=str, ensure_ascii=False)
    return s[: C.MAX_CONTENT_CHARS * 2]


def infer_type(span: RawSpan) -> str:
    a = span.attributes
    t = a.get(C.TYPE)
    if t in C.TYPES:
        return str(t)
    op = str(a.get(C.GEN_OPERATION, "")).lower()
    if op in ("chat", "text_completion", "generate_content", "completion"):
        return "generation"
    if op in ("embeddings", "embedding"):
        return "embedding"
    if op == "execute_tool":
        return "tool"
    if op in ("invoke_agent", "create_agent"):
        return "agent"
    if any(k in a for k in ("http.method", "http.request.method", "http.route", "url.full", "http.url", "asgi.event.type"))             or span.name.endswith((" http receive", " http send")):
        return "http"
    n = span.name.lower()
    if n.startswith(("retriev", "search", "memory.recall")):
        return "retriever"
    if n.startswith("guardrail"):
        return "guardrail"
    return "span"


def to_ts(ns: int) -> datetime:
    return datetime.fromtimestamp(ns / 1e9, tz=UTC)


def normalize(project_id: str, span: RawSpan) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    a = span.attributes
    typ = infer_type(span)
    level = a.get(C.LEVEL) or ("ERROR" if span.status_code == 2 else "DEFAULT")
    status_message = a.get(C.STATUS_MESSAGE) or span.status_message or None
    if level == "ERROR" and not status_message:
        exc = next((e for e in span.events if e.get("name") == "exception"), None)
        if exc:
            status_message = str(exc["attributes"].get("exception.message", ""))[:500] or None
    inp = a.get(C.INPUT) or _first(a, C.GEN_INPUT_MESSAGES)
    out = a.get(C.OUTPUT) or _first(a, C.GEN_OUTPUT_MESSAGES)
    in_tok = int(_first(a, C.GEN_INPUT_TOKENS) or 0)
    out_tok = int(_first(a, C.GEN_OUTPUT_TOKENS) or 0)
    cache_tok = int(_first(a, C.GEN_CACHE_READ) or 0)
    cost = _first(a, C.COST_FALLBACKS)
    tags = a.get(C.TAGS)
    meta: dict[str, Any] = {}
    for k, v in a.items():
        if k.startswith(C.METADATA_PREFIX):
            meta[k[len(C.METADATA_PREFIX):]] = v
        elif not k.startswith(("pulse.", "gen_ai.usage.", "gen_ai.input", "gen_ai.output", "gen_ai.prompt",
                               "gen_ai.completion")) and k not in _SKIP_META:
            meta[k] = v
    row = {
        "project_id": project_id, "trace_id": span.trace_id, "span_id": span.span_id,
        "parent_span_id": span.parent_span_id, "name": span.name, "type": typ,
        "start_time": to_ts(span.start_ns), "end_time": to_ts(span.end_ns) if span.end_ns else None,
        "latency_ms": max(0.0, (span.end_ns - span.start_ns) / 1e6) if span.end_ns else None,
        "level": str(level).upper(), "status_message": status_message,
        "model": _first(a, C.GEN_MODEL), "provider": _first(a, C.GEN_PROVIDER),
        "input_tokens": in_tok, "output_tokens": out_tok, "cache_read_tokens": cache_tok,
        "total_tokens": in_tok + out_tok + cache_tok, "cost": float(cost) if cost is not None else None,
        "input": _text(inp), "output": _text(out), "metadata": meta,
        "service": span.resource.get("service.name"), "environment": span.resource.get("deployment.environment"),
        "release": span.resource.get("service.version"),
        "trace_name": a.get(C.TRACE_NAME), "user_id": a.get(C.USER_ID), "session_id": a.get(C.SESSION_ID),
        "tags": list(tags) if isinstance(tags, list | tuple) else ([tags] if tags else None),
        "trace_input": _text(a.get(C.TRACE_INPUT)), "trace_output": _text(a.get(C.TRACE_OUTPUT)),
    }
    scores = [{"project_id": project_id, "trace_id": span.trace_id, "observation_id": span.span_id,
               "name": k[len(C.SCORE_PREFIX):], "value": float(v), "source": "eval", "data_type": "numeric"}
              for k, v in a.items() if k.startswith(C.SCORE_PREFIX) and isinstance(v, int | float)]
    return row, scores
