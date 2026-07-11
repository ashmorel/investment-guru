import time
from collections.abc import Callable

from cryptography.fernet import Fernet
from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from app.core import crypto
from app.core.config import Settings

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
_MAX_FAILURES = 5
_LOCKOUT_SECONDS = 60.0
_MAX_TRACKED = 10_000

_DEFAULT_SECRET = "dev-secret-not-for-production"
_MIN_SECRET_LEN = 32


def validate_production_settings(settings: Settings) -> None:
    if not settings.is_production:
        return
    if settings.secret_key == _DEFAULT_SECRET or len(settings.secret_key) < _MIN_SECRET_LEN:
        raise RuntimeError(
            "SECRET_KEY must be set to a strong value (>=32 chars) in production"
        )
    keys = crypto.split_keys(settings.data_encryption_key)
    if not keys:
        raise RuntimeError("DATA_ENCRYPTION_KEY must be set in production")
    # A comma-separated value enables key rotation (new,old); every key in the
    # list must be a valid Fernet key and none may be the committed dev key.
    for key in keys:
        try:
            Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise RuntimeError("DATA_ENCRYPTION_KEY must be a valid Fernet key") from exc
        if crypto.is_dev_key(key):
            raise RuntimeError(
                "DATA_ENCRYPTION_KEY must not be the committed dev key in production"
            )


class LoginThrottle:
    """Per-email consecutive-failure lockout. Single-process state (single replica).

    Tracked state is capped at `_MAX_TRACKED` combined entries to bound memory
    under a bogus-email spray: when a new email would push tracked state over
    the cap, expired lockouts are swept first, and if that isn't enough the
    oldest failure entry is evicted to make room.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        max_failures: int = _MAX_FAILURES,
        lockout_seconds: float = _LOCKOUT_SECONDS,
    ):
        self._clock = clock
        self._max_failures = max_failures
        self._lockout_seconds = lockout_seconds
        self._failures: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}

    def _evict_expired_lockouts(self) -> None:
        now = self._clock()
        expired = [email for email, until in self._locked_until.items() if until <= now]
        for email in expired:
            del self._locked_until[email]
            self._failures.pop(email, None)

    def _evict_oldest_failure(self) -> None:
        if not self._failures:
            return
        oldest = next(iter(self._failures))
        del self._failures[oldest]
        self._locked_until.pop(oldest, None)

    def check(self, email: str) -> None:
        until = self._locked_until.get(email, 0.0)
        if self._clock() < until:
            raise HTTPException(status_code=429, detail="too_many_attempts")
        if email in self._locked_until:
            # Lockout has expired (checked above) — sweep it instead of
            # leaving stale state around.
            del self._locked_until[email]
            self._failures.pop(email, None)

    def record_failure(self, email: str) -> None:
        if (
            email not in self._failures
            and len(self._failures) + len(self._locked_until) >= _MAX_TRACKED
        ):
            self._evict_expired_lockouts()
            if len(self._failures) + len(self._locked_until) >= _MAX_TRACKED:
                self._evict_oldest_failure()
        n = self._failures.get(email, 0) + 1
        self._failures[email] = n
        if n >= self._max_failures:
            self._locked_until[email] = self._clock() + self._lockout_seconds
            self._failures[email] = 0

    def record_success(self, email: str) -> None:
        self._failures.pop(email, None)
        self._locked_until.pop(email, None)


login_throttle = LoginThrottle()

# Separate from login_throttle: keyed by client IP (not email) since
# registration has no account identity yet. Looser threshold (10 vs 5) since
# a shared IP (office/NAT) may have several people signing up legitimately.
register_throttle = LoginThrottle(max_failures=10, lockout_seconds=60.0)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response
