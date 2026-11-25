"""Input / output guardrails (deterministic, ~0 ms, no LLM cost).

Input:  prompt-injection & jailbreak patterns, destructive-intent requests, oversized input,
        PII detection (for redaction in logs/traces, not blocking).
Output: secret leakage, system-prompt leakage, PII echo.

Decisions are ``allow`` / ``flag`` (proceed, but recorded as WARNING in the trace) / ``block``
(answer with a refusal, no LLM call). Patterns are intentionally conservative: false blocks on a
knowledge-base assistant are worse than a flagged trace a human can review. An LLM classifier
can be layered on top for the ``flag`` band.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_INJECTION = [
    (re.compile(r"\b(ignore|disregard|forget|override)\b.{0,30}\b(all|any|previous|prior|above|earlier)\b.{0,20}"
                r"\b(instructions?|prompts?|rules?|directions?)", re.I), "instruction_override", 0.9),
    (re.compile(r"\b(reveal|show|print|repeat|output)\b.{0,30}\b(system prompt|hidden (prompt|instructions)|"
                r"your (instructions|rules|prompt))", re.I), "prompt_extraction", 0.85),
    (re.compile(r"\byou are now\b|\bact as (an? )?(unrestricted|jailbroken|dan)\b|\bdeveloper mode\b", re.I),
     "role_hijack", 0.8),
    (re.compile(r"<\s*/?\s*(system|assistant)\s*>|\[\s*(system|inst)\s*\]", re.I), "fake_role_tags", 0.7),
]
_DESTRUCTIVE = re.compile(r"\b(delete|drop|wipe|destroy|terminate|truncate|rm -rf)\b.{0,40}\b(all|every|entire|"
                          r"database|instances?|tables?|records?|everything)\b", re.I)
_PII = {
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "phone": re.compile(r"(?<!\d)(\+?\d{1,3}[\s-]?)?(\(?\d{3}\)?[\s-]?)\d{3}[\s-]?\d{4}(?!\d)"),
    "card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "aadhaar": re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b"),
    "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
}
_SECRETS = re.compile(r"(sk-[A-Za-z0-9_\-]{16,}|atl_[A-Za-z0-9_\-]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY"
                      r"|ghp_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{10,})")
MAX_INPUT_CHARS = 8000


@dataclass(slots=True)
class GuardResult:
    decision: str = "allow"  # allow | flag | block
    reasons: list[str] = field(default_factory=list)
    risk: float = 0.0
    pii: list[str] = field(default_factory=list)
    redacted: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"decision": self.decision, "reasons": self.reasons, "risk": round(self.risk, 2), "pii": self.pii}


def _luhn(num: str) -> bool:
    digits = [int(c) for c in num if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def find_pii(text: str) -> list[str]:
    found = []
    for kind, rx in _PII.items():
        for m in rx.finditer(text):
            if kind == "card" and not _luhn(m.group(0)):
                continue
            found.append(kind)
            break
    return found


def redact_pii(text: str) -> str:
    out = _SECRETS.sub("[SECRET]", text)
    for kind, rx in _PII.items():
        if kind == "card":
            out = rx.sub(lambda m: "[CARD]" if _luhn(m.group(0)) else m.group(0), out)
        else:
            out = rx.sub(f"[{kind.upper()}]", out)
    return out


def check_input(text: str) -> GuardResult:
    r = GuardResult()
    if len(text) > MAX_INPUT_CHARS:
        r.reasons.append("input_too_long")
        r.risk = max(r.risk, 0.6)
    for rx, reason, risk in _INJECTION:
        if rx.search(text):
            r.reasons.append(reason)
            r.risk = max(r.risk, risk)
    if _DESTRUCTIVE.search(text):
        r.reasons.append("destructive_intent")
        r.risk = max(r.risk, 0.75)
    r.pii = find_pii(text)
    if r.pii:
        r.redacted = redact_pii(text)
    if r.risk >= 0.85 or (len(r.reasons) >= 2 and r.risk >= 0.75):
        r.decision = "block"
    elif r.reasons or r.pii:
        r.decision = "flag"
    return r


def check_output(text: str, system_prompt: str = "") -> GuardResult:
    r = GuardResult()
    if _SECRETS.search(text):
        r.reasons.append("secret_leak")
        r.risk = 1.0
    if system_prompt:
        probe = system_prompt[:120].strip()
        if probe and probe in text:
            r.reasons.append("system_prompt_leak")
            r.risk = max(r.risk, 0.9)
    r.pii = find_pii(text)
    if r.risk >= 0.9:
        r.decision = "block"
        r.redacted = redact_pii(text)
    elif r.pii:
        r.decision = "flag"
    return r


REFUSAL = ("I can't help with that request. I can answer questions about the documents in this knowledge base. "
           "If you think this was a mistake, rephrase your question.")
