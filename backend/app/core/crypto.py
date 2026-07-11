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


def _active_key() -> str:
    return settings.data_encryption_key or _DEV_KEY_REAL


def is_dev_key(value: str) -> bool:
    """Check if the given key is the committed dev key."""
    return value == _DEV_KEY_REAL


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
def _get_cached_crypto(key: str) -> Crypto:
    """Cache Crypto instance based on the active key to avoid rebuilding Fernet."""
    return Crypto([key])


def _default() -> "Crypto":
    return _get_cached_crypto(_active_key())


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
