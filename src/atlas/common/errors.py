"""Domain error hierarchy mapped to HTTP problem responses (RFC 9457) by the API layer."""

from __future__ import annotations


class AtlasError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFound(AtlasError):
    status_code = 404
    code = "not_found"


class Conflict(AtlasError):
    status_code = 409
    code = "conflict"


class InvalidInput(AtlasError):
    status_code = 422
    code = "invalid_input"


class Unauthorized(AtlasError):
    status_code = 401
    code = "unauthorized"


class Forbidden(AtlasError):
    status_code = 403
    code = "forbidden"


class RateLimited(AtlasError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message, details={"retry_after": retry_after})
        self.retry_after = retry_after


class BudgetExceeded(AtlasError):
    status_code = 402
    code = "budget_exceeded"


class UpstreamError(AtlasError):
    status_code = 502
    code = "upstream_error"
