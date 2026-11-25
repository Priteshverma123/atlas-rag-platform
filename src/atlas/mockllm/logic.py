"""Deterministic 'LLM' used by CI, chaos tests and key-less demos.

It is not a toy echo: for RAG prompts it performs *extractive* answering, picking the evidence
sentences that best overlap the question and citing them with ``[n]`` markers. Offline evals with
the mock therefore still measure retrieval quality and citation plumbing end to end. For
structured-output requests it instantiates a schema-valid object.
"""

from __future__ import annotations

import json
import re
from typing import Any

_SOURCE_RE = re.compile(r'<source id="(\d+)"[^>]*>(.*?)</source>', re.S)
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "to", "in", "and", "or", "is", "are", "was", "what", "which", "who", "how",
         "for", "on", "with", "does", "do", "did", "be", "by", "it", "this", "that", "as", "at", "from"}


def _text(m: dict[str, Any]) -> str:
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(p.get("text", "") for p in c if isinstance(p, dict))
    return ""


def _terms(s: str) -> set[str]:
    return {w for w in _WORD.findall(s.lower()) if w not in _STOP and len(w) > 1}


def _last_user(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return _text(m)
    return ""


def _question(user_text: str) -> str:
    m = re.search(r"<question>(.*?)</question>", user_text, re.S)
    return (m.group(1) if m else user_text).strip()


def instantiate(schema: dict[str, Any], hint: str, defs: dict[str, Any] | None = None) -> Any:
    defs = defs if defs is not None else schema.get("$defs", {})
    if "$ref" in schema:  # pydantic puts enums/nested models in $defs
        schema = defs.get(schema["$ref"].rsplit("/", 1)[-1], {})
    if "allOf" in schema and schema["allOf"]:
        schema = schema["allOf"][0] if "$ref" not in schema["allOf"][0] else             defs.get(schema["allOf"][0]["$ref"].rsplit("/", 1)[-1], {})
    t = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if t == "object":
        out = {}
        for name, sub in (schema.get("properties") or {}).items():
            if sub.get("type") == "string" and any(k in name for k in ("query", "question", "rewrite")):
                out[name] = hint
            else:
                out[name] = instantiate(sub, hint, defs)
        return out
    if t == "array":
        return []
    if t in ("number", "integer"):
        return 0.5 if t == "number" else 1
    if t == "boolean":
        return True
    return "mock"


def extractive_answer(messages: list[dict[str, Any]], max_sentences: int = 3) -> str:
    all_text = "\n".join(_text(m) for m in messages)
    sources = _SOURCE_RE.findall(all_text)
    question = _question(_last_user(messages))
    if not sources:
        return f"(mock) You said: {question[:200]}"
    q = _terms(question)
    scored: list[tuple[float, int, str]] = []
    for sid, body in sources:
        for sent in re.split(r"(?<=[.!?])\s+", " ".join(body.split())):
            if len(sent) < 20:
                continue
            overlap = len(q & _terms(sent))
            if overlap:
                scored.append((overlap / (1 + len(q)), int(sid), sent))
    if not scored:
        return "I could not find this in the provided sources."
    scored.sort(key=lambda x: (-x[0], x[1]))
    picked = scored[:max_sentences]
    return " ".join(f"{s.rstrip()} [{sid}]" for _, sid, s in picked)


def generate_reply(messages: list[dict[str, Any]], response_format: dict[str, Any] | None) -> str:
    if response_format and response_format.get("type") == "json_schema":
        schema = response_format.get("json_schema", {}).get("schema", {})
        hint = _question(_last_user(messages))
        return json.dumps(instantiate(schema, hint))
    if response_format and response_format.get("type") == "json_object":
        return "{}"
    return extractive_answer(messages)
