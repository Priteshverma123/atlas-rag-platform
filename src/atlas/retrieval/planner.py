"""Query planning: routing + history-aware condensation in ONE cheap structured call.

Skipped entirely (zero LLM latency) on the first turn of a short, single-part question: the
common case. Otherwise the ``fast`` route classifies intent and rewrites the question into a
standalone search query using the conversation summary and recent turns.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field

from atlas.common.telemetry import span
from atlas.llm.client import LLMClient


class Intent(StrEnum):
    LOOKUP = "lookup"  # first value = safe default
    MULTI_HOP = "multi_hop"
    GLOBAL = "global"
    CHITCHAT = "chitchat"


class QueryPlan(BaseModel):
    intent: Intent = Field(description="lookup: single fact; multi_hop: needs combining several facts or a "
                                       "comparison; global: themes/summary across many documents; chitchat: "
                                       "greetings or meta questions that need no documents")
    standalone_query: str = Field(description="the question rewritten to be fully self-contained, resolving "
                                              "pronouns and references from the conversation")
    sub_queries: list[str] = Field(description="for multi_hop only: 2-4 simpler search queries; else empty")


SYSTEM = """You plan searches over a document knowledge base. Classify the user's latest message and
rewrite it as a standalone search query. Use the conversation only to resolve references."""

_MULTI = re.compile(r"\b(compare|versus|vs\.?|difference|both|and also|each of|trend|over time)\b", re.I)
_GLOBAL = re.compile(r"\b(summari[sz]e|overview|main (themes|topics|points)|across all|in general)\b", re.I)
_CHAT = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|ok|okay|bye)\b[\s!.]*$", re.I)


def heuristic_plan(question: str, has_history: bool) -> QueryPlan | None:
    if _CHAT.match(question):
        return QueryPlan(intent=Intent.CHITCHAT, standalone_query=question, sub_queries=[])
    if not has_history and len(question.split()) <= 30 and not _MULTI.search(question) \
            and not _GLOBAL.search(question):
        return QueryPlan(intent=Intent.LOOKUP, standalone_query=question, sub_queries=[])
    return None


async def plan_query(llm: LLMClient, tenant_id: str, question: str, history: list[dict[str, str]],
                     summary: str = "") -> tuple[QueryPlan, bool]:
    """Returns (plan, used_llm)."""
    if (p := heuristic_plan(question, bool(history))) is not None:
        return p, False
    convo = "\n".join(f"{m['role']}: {m['content'][:600]}" for m in history[-6:])
    user = (f"<summary>{summary}</summary>\n" if summary else "") + \
        f"<conversation>\n{convo}\n</conversation>\n<question>{question}</question>"
    with span("retrieval.plan"):
        plan, _ = await llm.structured("fast", [{"role": "system", "content": SYSTEM},
                                                {"role": "user", "content": user}], QueryPlan,
                                       tenant_id=tenant_id, max_tokens=300)
    if not plan.standalone_query.strip():
        plan.standalone_query = question
    plan.sub_queries = [q for q in plan.sub_queries if q.strip()][:4]
    return plan, True
