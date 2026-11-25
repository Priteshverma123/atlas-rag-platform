"""OTLP/HTTP trace decoding (protobuf and JSON encodings) into plain span dicts."""

from __future__ import annotations

import gzip
from dataclasses import dataclass, field
from typing import Any

import orjson
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


@dataclass(slots=True)
class RawSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    start_ns: int
    end_ns: int
    status_code: int  # 0 unset, 1 ok, 2 error
    status_message: str
    attributes: dict[str, Any] = field(default_factory=dict)
    resource: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)


def _any_pb(v: Any) -> Any:
    kind = v.WhichOneof("value")
    if kind is None:
        return None
    if kind == "array_value":
        return [_any_pb(x) for x in v.array_value.values]
    if kind == "kvlist_value":
        return {kv.key: _any_pb(kv.value) for kv in v.kvlist_value.values}
    if kind == "bytes_value":
        return v.bytes_value.hex()
    return getattr(v, kind)


def _attrs_pb(kvs: Any) -> dict[str, Any]:
    return {kv.key: _any_pb(kv.value) for kv in kvs}


def _any_json(v: dict[str, Any]) -> Any:
    if not v:
        return None
    if "stringValue" in v:
        return v["stringValue"]
    if "intValue" in v:
        return int(v["intValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "boolValue" in v:
        return bool(v["boolValue"])
    if "arrayValue" in v:
        return [_any_json(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return {kv["key"]: _any_json(kv.get("value", {})) for kv in v["kvlistValue"].get("values", [])}
    return None


def _attrs_json(kvs: list[dict[str, Any]] | None) -> dict[str, Any]:
    return {kv["key"]: _any_json(kv.get("value", {})) for kv in kvs or []}


def decode(body: bytes, content_type: str, content_encoding: str = "") -> list[RawSpan]:
    if content_encoding == "gzip" or body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    out: list[RawSpan] = []
    if "json" in content_type:
        doc = orjson.loads(body)
        for rs in doc.get("resourceSpans", []):
            res = _attrs_json(rs.get("resource", {}).get("attributes"))
            for ss in rs.get("scopeSpans", []):
                for sp in ss.get("spans", []):
                    st = sp.get("status", {})
                    out.append(RawSpan(
                        trace_id=sp["traceId"].lower(), span_id=sp["spanId"].lower(),
                        parent_span_id=(sp.get("parentSpanId") or None), name=sp.get("name", ""),
                        start_ns=int(sp.get("startTimeUnixNano", 0)), end_ns=int(sp.get("endTimeUnixNano", 0)),
                        status_code=int(st.get("code", 0)), status_message=st.get("message", ""),
                        attributes=_attrs_json(sp.get("attributes")), resource=res,
                        events=[{"name": e.get("name"), "attributes": _attrs_json(e.get("attributes"))}
                                for e in sp.get("events", [])]))
        return out
    req = ExportTraceServiceRequest()
    req.ParseFromString(body)
    for rs in req.resource_spans:
        res = _attrs_pb(rs.resource.attributes)
        for ss in rs.scope_spans:
            for sp in ss.spans:
                out.append(RawSpan(
                    trace_id=sp.trace_id.hex(), span_id=sp.span_id.hex(),
                    parent_span_id=sp.parent_span_id.hex() or None, name=sp.name,
                    start_ns=sp.start_time_unix_nano, end_ns=sp.end_time_unix_nano,
                    status_code=int(sp.status.code), status_message=sp.status.message,
                    attributes=_attrs_pb(sp.attributes), resource=res,
                    events=[{"name": e.name, "attributes": _attrs_pb(e.attributes)} for e in sp.events]))
    return out
