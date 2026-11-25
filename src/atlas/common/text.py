"""Token counting and hashing helpers shared by all modules."""

from __future__ import annotations

import hashlib
import unicodedata
from functools import lru_cache

import orjson


@lru_cache(maxsize=1)
def _encoder():  # type: ignore[no-untyped-def]
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # offline / encoding not cached: fall back to a heuristic
        return None


def count_tokens(text: str) -> int:
    """Approximate token count. Provider tokenizers differ; this is used for *budgeting*,
    and the gateway always reconciles with the provider's reported usage."""
    enc = _encoder()
    if enc is None:
        return max(1, len(text) // 4)
    return len(enc.encode(text, disallowed_special=()))


def truncate_tokens(text: str, max_tokens: int) -> str:
    enc = _encoder()
    if enc is None:
        return text[: max_tokens * 4]
    ids = enc.encode(text, disallowed_special=())
    return text if len(ids) <= max_tokens else enc.decode(ids[:max_tokens])


def normalize_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_hash(obj: object) -> str:
    """Stable hash of a JSON-able object (sorted keys) for cache keys."""
    return hashlib.sha256(orjson.dumps(obj, option=orjson.OPT_SORT_KEYS)).hexdigest()
