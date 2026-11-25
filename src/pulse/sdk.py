"""Pulse SDK: a thin, typed layer over OpenTelemetry.

* ``init()`` configures a TracerProvider that batches spans to the Pulse server over OTLP/HTTP
  (non-blocking: export happens on a background thread, never in the request path).
* ``trace_context()`` sets trace-level fields (user, session, tags, trace name) in a contextvar;
  a SpanProcessor stamps them on every span started inside it, so the server can aggregate
  traces even when spans arrive out of order or from several services.
* ``observe()`` creates a typed observation (span, generation, retriever, tool, guardrail...)
  with input/output capture (truncated; can be globally disabled for privacy).
"""

from __future__ import annotations

import functools
import inspect
import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

from pulse import conventions as C

F = TypeVar("F", bound=Callable[..., Any])
_trace_ctx: ContextVar[dict[str, Any]] = ContextVar("pulse_trace_ctx", default={})  # noqa: B039


@dataclass
class _State:
    capture: bool = True
    endpoint: str | None = None
    api_key: str | None = None
    redact: Callable[[str], str] | None = None


_state = _State()


class _TraceContextProcessor(SpanProcessor):
    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        for k, v in _trace_ctx.get().items():
            span.set_attribute(k, v)

    def on_end(self, span: ReadableSpan) -> None:  # pragma: no cover - nothing to do
        return None

    def shutdown(self) -> None:  # pragma: no cover
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:  # pragma: no cover
        return True


def init(service_name: str, *, endpoint: str | None = None, api_key: str | None = None, capture_content: bool = True,
         environment: str = "default", release: str | None = None,
         redact: Callable[[str], str] | None = None) -> None:
    """Idempotent per process. Without an endpoint spans are created but not exported."""
    _state.capture, _state.endpoint, _state.api_key, _state.redact = capture_content, endpoint, api_key, redact
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider) and getattr(current, "_pulse", False):
        return
    res = {"service.name": service_name, "deployment.environment": environment}
    if release:
        res["service.version"] = release
    provider = TracerProvider(resource=Resource.create(res))
    provider.add_span_processor(_TraceContextProcessor())
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        provider.add_span_processor(BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces", headers=headers),
            max_queue_size=8192, schedule_delay_millis=1000, max_export_batch_size=512))
    provider._pulse = True  # type: ignore[attr-defined]
    trace.set_tracer_provider(provider)


def _content(value: Any) -> str | None:
    if value is None or not _state.capture:
        return None
    s = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    if _state.redact:
        s = _state.redact(s)
    return s if len(s) <= C.MAX_CONTENT_CHARS else s[: C.MAX_CONTENT_CHARS] + "…[truncated]"


@contextmanager
def trace_context(*, user_id: str | None = None, session_id: str | None = None, tags: Sequence[str] | None = None,
                  name: str | None = None, metadata: dict[str, Any] | None = None) -> Iterator[None]:
    """Trace-level fields inherited by every span created inside the block."""
    ctx = dict(_trace_ctx.get())
    if user_id:
        ctx[C.USER_ID] = str(user_id)
    if session_id:
        ctx[C.SESSION_ID] = str(session_id)
    if tags:
        ctx[C.TAGS] = sorted({*ctx.get(C.TAGS, ()), *tags})
    if name:
        ctx[C.TRACE_NAME] = name
    for k, v in (metadata or {}).items():
        if v is not None:
            ctx[C.METADATA_PREFIX + k] = v if isinstance(v, str | int | float | bool) else str(v)
    token = _trace_ctx.set(ctx)
    try:
        yield
    finally:
        _trace_ctx.reset(token)


class Observation:
    """Handle to the current observation (wraps an OTel span)."""

    def __init__(self, span: trace.Span) -> None:
        self.span = span

    def set_input(self, value: Any) -> Observation:
        if (c := _content(value)) is not None:
            self.span.set_attribute(C.INPUT, c)
        return self

    def set_output(self, value: Any) -> Observation:
        if (c := _content(value)) is not None:
            self.span.set_attribute(C.OUTPUT, c)
        return self

    def set_trace_io(self, input: Any = None, output: Any = None) -> Observation:
        if (c := _content(input)) is not None:
            self.span.set_attribute(C.TRACE_INPUT, c)
        if (c := _content(output)) is not None:
            self.span.set_attribute(C.TRACE_OUTPUT, c)
        return self

    def set_model(self, model: str | None, provider: str | None = None) -> Observation:
        if model:
            self.span.set_attribute(C.GEN_MODEL[0], model)
        if provider:
            self.span.set_attribute(C.GEN_PROVIDER[0], provider)
        return self

    def set_usage(self, input_tokens: int = 0, output_tokens: int = 0, cache_read_tokens: int = 0,
                  cost_usd: float | None = None) -> Observation:
        self.span.set_attribute(C.GEN_INPUT_TOKENS[0], int(input_tokens))
        self.span.set_attribute(C.GEN_OUTPUT_TOKENS[0], int(output_tokens))
        if cache_read_tokens:
            self.span.set_attribute(C.GEN_CACHE_READ[0], int(cache_read_tokens))
        if cost_usd is not None:
            self.span.set_attribute(C.COST, float(cost_usd))
        return self

    def set_metadata(self, **kv: Any) -> Observation:
        for k, v in kv.items():
            if v is not None:
                self.span.set_attribute(C.METADATA_PREFIX + k, v if isinstance(v, str | int | float | bool) else
                                        json.dumps(v, default=str))
        return self

    def score(self, name: str, value: float) -> Observation:
        """Attach a numeric eval score to this observation (stored by the server as source=eval)."""
        self.span.set_attribute(C.SCORE_PREFIX + name, float(value))
        return self

    def warn(self, message: str) -> Observation:
        self.span.set_attribute(C.LEVEL, "WARNING")
        self.span.set_attribute(C.STATUS_MESSAGE, message[:500])
        return self

    def error(self, message: str) -> Observation:
        self.span.set_attribute(C.LEVEL, "ERROR")
        self.span.set_attribute(C.STATUS_MESSAGE, message[:500])
        self.span.set_status(Status(StatusCode.ERROR, message[:200]))
        return self

    @property
    def trace_id(self) -> str:
        return format(self.span.get_span_context().trace_id, "032x")


@contextmanager
def observe(name: str, *, type: str = "span", input: Any = None,
            tracer_name: str = "pulse", **attributes: Any) -> Iterator[Observation]:
    """Typed observation. Exceptions mark it ERROR (with message) and propagate."""
    with trace.get_tracer(tracer_name).start_as_current_span(name) as span:
        span.set_attribute(C.TYPE, type)
        for k, v in attributes.items():
            if v is not None:
                span.set_attribute(k, v if isinstance(v, str | int | float | bool) else str(v))
        obs = Observation(span)
        if input is not None:
            obs.set_input(input)
        try:
            yield obs
        except Exception as exc:
            span.record_exception(exc)
            obs.error(f"{exc.__class__.__name__}: {exc}")  # (`type` is a parameter here, not the builtin)
            raise


def observed(name: str | None = None, *, type: str = "span", capture_args: bool = True) -> Callable[[F], F]:
    """Decorator for sync or async functions."""

    def deco(fn: F) -> F:
        label = name or fn.__qualname__

        def _input(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
            if not capture_args:
                return None
            try:
                bound = inspect.signature(fn).bind_partial(*args, **kwargs)
                return {k: v for k, v in bound.arguments.items() if k not in ("self", "cls")}
            except TypeError:
                return None

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrapper(*args: Any, **kwargs: Any) -> Any:
                with observe(label, type=type, input=_input(args, kwargs)) as obs:
                    result = await fn(*args, **kwargs)
                    obs.set_output(result)
                    return result
            return awrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with observe(label, type=type, input=_input(args, kwargs)) as obs:
                result = fn(*args, **kwargs)
                obs.set_output(result)
                return result
        return wrapper  # type: ignore[return-value]
    return deco


async def score(trace_id: str, name: str, value: float | str, *, observation_id: str | None = None,
                comment: str | None = None, source: str = "api") -> None:
    """Send a score after the fact (e.g. user feedback 👍/👎 linked to a trace)."""
    if not _state.endpoint:
        return
    headers = {"Authorization": f"Bearer {_state.api_key}"} if _state.api_key else {}
    body = {"trace_id": trace_id, "observation_id": observation_id, "name": name, "comment": comment, "source": source}
    body["value" if isinstance(value, int | float) else "string_value"] = value
    async with httpx.AsyncClient(timeout=5) as client:
        await client.post(f"{_state.endpoint.rstrip('/')}/api/scores", json=body, headers=headers)
