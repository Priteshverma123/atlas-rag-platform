"""Telemetry for Atlas, built on the reusable Pulse SDK (``src/pulse``).

Spans follow the OTel GenAI semantic conventions plus ``pulse.*`` attributes (observation type,
input/output, user/session). Attribute names live in one place (``GenAI``) because the GenAI
conventions moved repositories in 2026 and are still at Development stability.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.trace import Span, Status, StatusCode

import pulse
from pulse import conventions as PC


class GenAI:
    """Attribute names (semconv-genai, pinned 2026-11)."""

    OPERATION = "gen_ai.operation.name"
    PROVIDER = "gen_ai.provider.name"
    REQUEST_MODEL = "gen_ai.request.model"
    RESPONSE_MODEL = "gen_ai.response.model"
    MAX_TOKENS = "gen_ai.request.max_tokens"
    INPUT_TOKENS = "gen_ai.usage.input_tokens"
    OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
    CACHE_READ_TOKENS = "gen_ai.usage.cache_read.input_tokens"
    CACHE_WRITE_TOKENS = "gen_ai.usage.cache_write.input_tokens"
    FINISH_REASONS = "gen_ai.response.finish_reasons"
    CONVERSATION_ID = "gen_ai.conversation.id"
    PROMPT_NAME = "gen_ai.prompt.name"
    PROMPT_VERSION = "gen_ai.prompt.version"


def setup_telemetry(service_name: str, otlp_endpoint: str | None, *, api_key: str | None = None,
                    capture_content: bool = False, environment: str = "dev") -> None:
    """Idempotent. Captured content is PII-redacted before it leaves the process."""
    from atlas.guardrails import redact_pii

    pulse.init(service_name, endpoint=otlp_endpoint, api_key=api_key, capture_content=capture_content,
               environment=environment, release="0.1.0", redact=redact_pii)


def tracer(name: str = "atlas") -> trace.Tracer:
    return trace.get_tracer(name)


def meter(name: str = "atlas") -> metrics.Meter:
    return metrics.get_meter(name)


@contextmanager
def span(name: str, *, kind: str = "span", **attributes: Any) -> Iterator[Span]:
    """Raw-span helper (records exceptions, drops None attributes, tags the Pulse observation type)."""
    with tracer().start_as_current_span(name) as s:
        s.set_attribute(PC.TYPE, kind)
        for k, v in attributes.items():
            if v is not None:
                s.set_attribute(k, v)
        try:
            yield s
        except Exception as exc:
            s.record_exception(exc)
            s.set_status(Status(StatusCode.ERROR, str(exc)))
            s.set_attribute(PC.LEVEL, "ERROR")
            s.set_attribute(PC.STATUS_MESSAGE, f"{exc.__class__.__name__}: {exc}"[:500])
            raise


observe = pulse.observe
trace_context = pulse.trace_context
