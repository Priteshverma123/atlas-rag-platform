"""OpenAI-compatible mock LLM server with runtime-tunable chaos.

    POST /v1/chat/completions      (stream and non-stream)
    POST /admin/chaos              {"fail_rate": 0.3, "status": 503, "latency_ms": 200, "ttft_ms": 0}

Used by: CI eval runs (no API keys), the gateway failover/chaos demo, and k6 overhead benchmarks.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from atlas.mockllm.logic import generate_reply

app = FastAPI(title="mock-llm")


class Chaos(BaseModel):
    fail_rate: float = 0.0
    status: int = 503
    latency_ms: int = 0
    ttft_ms: int = 0


STATE = Chaos()


@app.post("/admin/chaos")
async def set_chaos(c: Chaos) -> Chaos:
    global STATE
    STATE = c
    return STATE


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _usage(messages: list[dict[str, Any]], text: str) -> dict[str, int]:
    p = sum(len(json.dumps(m)) // 4 for m in messages)
    c = max(1, len(text) // 4)
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


@app.post("/v1/chat/completions")
async def chat(request: Request) -> Any:
    body = await request.json()
    if STATE.latency_ms:
        await asyncio.sleep(STATE.latency_ms / 1000)
    if random.random() < STATE.fail_rate:
        headers = {"retry-after": "1"} if STATE.status == 429 else {}
        return JSONResponse({"error": {"message": "chaos: injected failure", "type": "server_error"}},
                            status_code=STATE.status, headers=headers)
    messages = body.get("messages", [])
    text = generate_reply(messages, body.get("response_format"))
    cid, created, model = f"chatcmpl-{uuid.uuid4().hex}", int(time.time()), body.get("model", "mock")

    if not body.get("stream"):
        return {"id": cid, "object": "chat.completion", "created": created, "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": _usage(messages, text)}

    async def events() -> Any:
        if STATE.ttft_ms:
            await asyncio.sleep(STATE.ttft_ms / 1000)
        for i in range(0, len(text), 12):
            chunk = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                     "choices": [{"index": 0, "delta": {"content": text[i:i + 12]}, "finish_reason": None}]}
            yield f"data: {json.dumps(chunk)}\n\n"
            await asyncio.sleep(0.005)
        end = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
               "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        yield f"data: {json.dumps(end)}\n\n"
        usage = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [],
                 "usage": _usage(messages, text)}
        yield f"data: {json.dumps(usage)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
