import time
from collections.abc import Callable

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import Settings

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
_MAX_FAILURES = 5
_LOCKOUT_SECONDS = 60.0

_DEFAULT_SECRET = "dev-secret-not-for-production"
_MIN_SECRET_LEN = 32


def validate_production_settings(settings: Settings) -> None:
    if not settings.is_production:
        return
    if settings.secret_key == _DEFAULT_SECRET or len(settings.secret_key) < _MIN_SECRET_LEN:
        raise RuntimeError(
            "SECRET_KEY must be set to a strong value (>=32 chars) in production"
        )


class LoginThrottle:
    """Per-email consecutive-failure lockout. Single-process state (single replica)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._failures: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}

    def check(self, email: str) -> None:
        until = self._locked_until.get(email, 0.0)
        if self._clock() < until:
            raise HTTPException(status_code=429, detail="too_many_attempts")

    def record_failure(self, email: str) -> None:
        n = self._failures.get(email, 0) + 1
        self._failures[email] = n
        if n >= _MAX_FAILURES:
            self._locked_until[email] = self._clock() + _LOCKOUT_SECONDS
            self._failures[email] = 0

    def record_success(self, email: str) -> None:
        self._failures.pop(email, None)
        self._locked_until.pop(email, None)


login_throttle = LoginThrottle()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response
