"""Pulse storage (Postgres, SQLAlchemy Core).

Observations are upserted by (project, span_id) so OTLP retries are idempotent. Trace rows are
*derived* aggregates, recomputed for affected trace ids after each batch, so spans can arrive in
any order and from any number of services. (At very high volume, swap this module for ClickHouse
as Lumen does; the API contract stays the same.)
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

metadata = MetaData()

projects = Table(
    "pulse_projects", metadata,
    Column("id", String(100), primary_key=True),
    Column("key_hash", String(64), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

observations = Table(
    "pulse_observations", metadata,
    Column("project_id", String(100), primary_key=True),
    Column("span_id", String(16), primary_key=True),
    Column("trace_id", String(32), nullable=False),
    Column("parent_span_id", String(16)),
    Column("name", Text, nullable=False),
    Column("type", String(20), nullable=False),
    Column("start_time", DateTime(timezone=True), nullable=False),
    Column("end_time", DateTime(timezone=True)),
    Column("latency_ms", Float),
    Column("level", String(10), nullable=False, server_default="DEFAULT"),
    Column("status_message", Text),
    Column("model", Text),
    Column("provider", Text),
    Column("input_tokens", Integer, server_default="0"),
    Column("output_tokens", Integer, server_default="0"),
    Column("cache_read_tokens", Integer, server_default="0"),
    Column("total_tokens", Integer, server_default="0"),
    Column("cost", Float),
    Column("input", Text),
    Column("output", Text),
    Column("metadata", JSONB, server_default="{}"),
    Column("service", Text),
    Column("environment", Text),
    Column("release", Text),
    Column("trace_name", Text),
    Column("user_id", Text),
    Column("session_id", Text),
    Column("tags", ARRAY(Text)),
    Column("trace_input", Text),
    Column("trace_output", Text),
    Index("ix_obs_trace", "project_id", "trace_id"),
    Index("ix_obs_time", "project_id", "start_time"),
)

traces = Table(
    "pulse_traces", metadata,
    Column("project_id", String(100), primary_key=True),
    Column("trace_id", String(32), primary_key=True),
    Column("name", Text),
    Column("user_id", Text),
    Column("session_id", Text),
    Column("tags", ARRAY(Text)),
    Column("input", Text),
    Column("output", Text),
    Column("start_time", DateTime(timezone=True)),
    Column("end_time", DateTime(timezone=True)),
    Column("latency_ms", Float),
    Column("total_cost", Float),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("observation_count", Integer),
    Column("level", String(10)),
    Column("environment", Text),
    Column("release", Text),
    Column("services", ARRAY(Text)),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
    Index("ix_traces_time", "project_id", "start_time"),
    Index("ix_traces_session", "project_id", "session_id"),
    Index("ix_traces_user", "project_id", "user_id"),
)

scores = Table(
    "pulse_scores", metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", String(100), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("observation_id", String(16)),
    Column("name", Text, nullable=False),
    Column("value", Float),
    Column("string_value", Text),
    Column("data_type", String(20), server_default="numeric"),
    Column("source", String(20), server_default="api"),  # api | eval | annotation
    Column("comment", Text),
    Column("author", Text),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Index("ix_scores_trace", "project_id", "trace_id"),
    Index("ux_scores_eval", "project_id", "observation_id", "name", "source", unique=True,
          postgresql_where="source = 'eval'"),
)

AGGREGATE_TRACES = """
INSERT INTO pulse_traces (project_id, trace_id, name, user_id, session_id, tags, input, output, start_time, end_time,
                          latency_ms, total_cost, input_tokens, output_tokens, observation_count, level, environment,
                          release, services, updated_at)
SELECT o.project_id, o.trace_id,
       COALESCE(max(o.trace_name),
                (array_agg(o.name ORDER BY o.start_time) FILTER (WHERE o.type <> 'http'))[1],
                (array_agg(o.name ORDER BY o.start_time))[1]),
       max(o.user_id), max(o.session_id),
       max(o.tags),  -- array_agg(text[]) would build a 2-D array; tags are identical across a trace's spans
       COALESCE(max(o.trace_input),
                (array_agg(o.input ORDER BY o.start_time) FILTER (WHERE o.input IS NOT NULL AND o.type <> 'http'))[1]),
       COALESCE(max(o.trace_output),
                (array_agg(o.output ORDER BY o.start_time) FILTER (WHERE o.output IS NOT NULL AND o.type <> 'http'))[1]),
       min(o.start_time), max(o.end_time),
       EXTRACT(EPOCH FROM (max(o.end_time) - min(o.start_time))) * 1000,
       sum(o.cost), sum(o.input_tokens + o.cache_read_tokens), sum(o.output_tokens),
       count(*) FILTER (WHERE o.type <> 'http'),
       CASE WHEN bool_or(o.level = 'ERROR') THEN 'ERROR' WHEN bool_or(o.level = 'WARNING') THEN 'WARNING'
            ELSE 'DEFAULT' END,
       max(o.environment), max(o.release), array_agg(DISTINCT o.service) FILTER (WHERE o.service IS NOT NULL), now()
FROM pulse_observations o
WHERE o.project_id = :project_id AND o.trace_id = ANY(:trace_ids)
GROUP BY o.project_id, o.trace_id
ON CONFLICT (project_id, trace_id) DO UPDATE SET
  name = EXCLUDED.name, user_id = EXCLUDED.user_id, session_id = EXCLUDED.session_id, tags = EXCLUDED.tags,
  input = EXCLUDED.input, output = EXCLUDED.output, start_time = EXCLUDED.start_time, end_time = EXCLUDED.end_time,
  latency_ms = EXCLUDED.latency_ms, total_cost = EXCLUDED.total_cost, input_tokens = EXCLUDED.input_tokens,
  output_tokens = EXCLUDED.output_tokens, observation_count = EXCLUDED.observation_count, level = EXCLUDED.level,
  environment = EXCLUDED.environment, release = EXCLUDED.release, services = EXCLUDED.services, updated_at = now()
"""
