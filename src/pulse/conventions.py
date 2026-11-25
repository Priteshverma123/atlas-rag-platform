"""Attribute contract between the Pulse SDK and the Pulse server.

Pulse reads its own ``pulse.*`` attributes first and falls back to OpenTelemetry GenAI semantic
conventions (``gen_ai.*``, both the 2026 names and legacy ones), so spans from any OTel-instrumented
LLM library are understood without the SDK.
"""

TYPE = "pulse.type"  # span | generation | retriever | tool | guardrail | agent | embedding | evaluator | event
INPUT = "pulse.input"
OUTPUT = "pulse.output"
LEVEL = "pulse.level"  # DEBUG | DEFAULT | WARNING | ERROR
STATUS_MESSAGE = "pulse.status_message"
COST = "pulse.cost_usd"
USER_ID = "pulse.user_id"
SESSION_ID = "pulse.session_id"
TAGS = "pulse.tags"
TRACE_NAME = "pulse.trace.name"
TRACE_INPUT = "pulse.trace.input"
TRACE_OUTPUT = "pulse.trace.output"
METADATA_PREFIX = "pulse.metadata."
SCORE_PREFIX = "pulse.score."  # pulse.score.<name> = number → stored as an eval score
HIDDEN = "pulse.hidden"

TYPES = {"span", "generation", "retriever", "tool", "guardrail", "agent", "embedding", "evaluator", "event", "http"}

# GenAI semconv (new names first, legacy second)
GEN_MODEL = ("gen_ai.response.model", "gen_ai.request.model")
GEN_PROVIDER = ("gen_ai.provider.name", "gen_ai.system")
GEN_INPUT_TOKENS = ("gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens")
GEN_OUTPUT_TOKENS = ("gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens")
GEN_CACHE_READ = ("gen_ai.usage.cache_read.input_tokens", "gen_ai.usage.cache_read_input_tokens")
GEN_OPERATION = "gen_ai.operation.name"
GEN_INPUT_MESSAGES = ("gen_ai.input.messages", "gen_ai.prompt")
GEN_OUTPUT_MESSAGES = ("gen_ai.output.messages", "gen_ai.completion")
COST_FALLBACKS = ("pulse.cost_usd", "lumen.cost_usd", "gen_ai.usage.cost")

MAX_CONTENT_CHARS = 8000
