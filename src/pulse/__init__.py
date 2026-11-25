"""Pulse: self-hosted LLM tracing & observability (SDK + server).

SDK (any Python project):

    import pulse
    pulse.init("my-service", endpoint="http://pulse:8100", api_key="pk-...")
    with pulse.trace_context(user_id="u1", session_id="chat-42", tags=["prod"]):
        with pulse.observe("ask", type="span", input=question) as obs:
            ...
            obs.set_output(answer)

Anything that speaks OTLP/HTTP (any language) can also send traces to the Pulse server.
"""

from pulse.sdk import Observation, init, observe, observed, score, trace_context

__all__ = ["Observation", "init", "observe", "observed", "score", "trace_context"]
