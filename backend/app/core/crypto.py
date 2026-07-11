import json
from decimal import Decimal
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.core.config import settings

_VERSION = "v1"

# Fixed dev/test key so the suite runs without secrets configured. This is a
# real, generated Fernet key but it is non-secret dev/test scaffolding — safe
# to commit. Production must set the real DATA_ENCRYPTION_KEY env var;
# validate_production_settings() fails hard if it is empty or not a valid
# Fernet key, so this constant can never silently back production data.
_DEV_KEY_REAL = "Hxna0PMhnrgwPIr2pXfY4BEJBbiP7unh6iI-dQ9Kz2g="


class DecryptError(Exception):
    pass


def is_dev_key(value: str) -> bool:
    """Check if the given key is the committed dev key."""
    return value == _DEV_KEY_REAL


def split_keys(raw: str) -> list[str]:
    """Parse a DATA_ENCRYPTION_KEY value into an ordered key list.

    Supports rotation: a comma-separated value ``new,old`` puts the primary
    (encrypt) key first and retains old keys for decrypt-only. A single-value
    env var (the common case) parses to a one-element list. Fernet keys are
    urlsafe-base64 (no commas), so splitting on ``,`` is unambiguous.
    """
    return [k.strip() for k in raw.split(",") if k.strip()]


def _active_keys() -> list[str]:
    """The active key list. In production, refuse to fall back to the committed
    dev key: migrations call ``encrypt()`` *before* the app's boot-time
    ``validate_production_settings`` runs, so without this guard a key-less prod
    deploy would silently write dev-key ciphertext (readable by anyone with the
    public repo) to real financial columns."""
    keys = split_keys(settings.data_encryption_key)
    if settings.is_production:
        if not keys:
            raise RuntimeError("DATA_ENCRYPTION_KEY must be set in production")
        if any(is_dev_key(k) for k in keys):
            raise RuntimeError(
                "DATA_ENCRYPTION_KEY must not be the committed dev key in production"
            )
        return keys
    return keys or [_DEV_KEY_REAL]


# Backwards-compatible accessor for the primary (encrypt) key.
def _active_key() -> str:
    return _active_keys()[0]


class Crypto:
    def __init__(self, keys: list[str]):
        self._mf = MultiFernet([Fernet(k.encode()) for k in keys])

    def encrypt(self, plaintext: str) -> str:
        return f"{_VERSION}:{self._mf.encrypt(plaintext.encode()).decode()}"

    def decrypt(self, token: str) -> str:
        if not token.startswith(_VERSION + ":"):
            raise DecryptError("missing version prefix")
        try:
            return self._mf.decrypt(token[len(_VERSION) + 1:].encode()).decode()
        except (InvalidToken, ValueError) as exc:
            raise DecryptError(str(exc)) from exc


@lru_cache(maxsize=1)
def _get_cached_crypto(keys_csv: str) -> Crypto:
    """Cache Crypto instance keyed on the joined key list to avoid rebuilding Fernet."""
    return Crypto(keys_csv.split(","))


def _default() -> "Crypto":
    return _get_cached_crypto(",".join(_active_keys()))


def encrypt(plaintext: str) -> str:
    return _default().encrypt(plaintext)


def decrypt(token: str) -> str:
    return _default().decrypt(token)


class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt(value)

    def process_result_value(self, value, dialect):
        return None if value is None else decrypt(value)


class EncryptedDecimal(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt(str(value))

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(decrypt(value))


class EncryptedJSON(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt(json.dumps(value))

    def process_result_value(self, value, dialect):
        return None if value is None else json.loads(decrypt(value))
