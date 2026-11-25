"""Per-request identity resolved by the API auth layer."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(slots=True)
class RequestContext:
    tenant_id: str
    key_id: str
    scopes: list[str] = field(default_factory=list)
    user_id: uuid.UUID | None = None
    user_external_id: str | None = None
    groups: list[str] = field(default_factory=list)

    def has(self, scope: str) -> bool:
        return "admin" in self.scopes or scope in self.scopes
