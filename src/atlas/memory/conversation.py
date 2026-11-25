"""Tier 1, working memory: a token-bounded buffer of recent turns + a rolling summary.

The buffer keeps the newest turns that fit ``memory_buffer_tokens``; older turns are folded into
``conversations.summary`` by a background job (so the hot path never waits for summarisation).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.common.text import count_tokens
from atlas.db.models import Conversation, Message
from atlas.llm.client import LLMClient

SUMMARY_SYSTEM = """Maintain a running summary of a conversation between a user and a document assistant.
Merge the previous summary with the new turns. Keep facts, decisions, open questions and what the user
is trying to achieve. Max 200 words. No preamble."""


async def load_window(s: AsyncSession, conv: Conversation, budget_tokens: int) -> list[dict[str, str]]:
    rows = (await s.execute(select(Message).where(Message.conversation_id == conv.id,
                                                  Message.seq > conv.summary_upto_seq)
                            .order_by(Message.seq.desc()).limit(40))).scalars().all()
    window: list[dict[str, str]] = []
    spent = 0
    for m in rows:  # newest first
        t = m.token_count or count_tokens(m.content)
        if spent + t > budget_tokens:
            break
        spent += t
        window.append({"role": m.role, "content": m.content})
    return list(reversed(window))


async def append(s: AsyncSession, conv: Conversation, role: str, content: str,
                 citations: dict[str, object] | None = None, verification: dict[str, object] | None = None
                 ) -> Message:
    seq = (await s.execute(select(func.coalesce(func.max(Message.seq), 0))
                           .where(Message.conversation_id == conv.id))).scalar_one() + 1
    msg = Message(id=uuid.uuid4(), tenant_id=conv.tenant_id, conversation_id=conv.id, seq=seq, role=role,
                  content=content, citations=citations or {}, verification=verification or {},
                  token_count=count_tokens(content))
    s.add(msg)
    await s.flush()
    return msg


async def roll_summary(s: AsyncSession, llm: LLMClient, conv_id: uuid.UUID, budget_tokens: int) -> bool:
    """Fold turns that no longer fit the buffer into the summary. Returns True if updated."""
    conv = await s.get(Conversation, conv_id)
    if conv is None:
        return False
    rows = (await s.execute(select(Message).where(Message.conversation_id == conv.id,
                                                  Message.seq > conv.summary_upto_seq)
                            .order_by(Message.seq))).scalars().all()
    total = sum(m.token_count for m in rows)
    if total <= budget_tokens or len(rows) < 4:
        return False
    # summarise the oldest half, keep the newest half verbatim in the buffer
    cut = len(rows) // 2
    old = rows[:cut]
    turns = "\n".join(f"{m.role}: {m.content[:1500]}" for m in old)
    res = await llm.chat("fast", [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user", "content": f"<previous_summary>{conv.summary}</previous_summary>\n<new_turns>\n{turns}\n"
                                    "</new_turns>"}], tenant_id=str(conv.tenant_id), max_tokens=400)
    if res.provider == "mock":
        summary = (conv.summary + " " + " | ".join(m.content[:120] for m in old if m.role == "user"))[-1500:]
    else:
        summary = res.text.strip()
    await s.execute(update(Conversation).where(Conversation.id == conv.id).values(
        summary=summary, summary_upto_seq=old[-1].seq))
    return True
