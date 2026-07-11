from decimal import Decimal

import pytest

from app.core import crypto
from app.core.config import Settings
from app.core.hardening import validate_production_settings


def test_encrypt_roundtrip_and_versioned_token():
    tok = crypto.encrypt("hello")
    assert tok.startswith("v1:")
    assert tok != "hello"
    assert crypto.decrypt(tok) == "hello"


def test_encrypt_is_nondeterministic_but_decrypts():
    a, b = crypto.encrypt("x"), crypto.encrypt("x")
    assert a != b  # Fernet includes an IV
    assert crypto.decrypt(a) == crypto.decrypt(b) == "x"


def test_decrypt_rejects_garbage():
    with pytest.raises(crypto.DecryptError):
        crypto.decrypt("v1:not-a-real-token")
    with pytest.raises(crypto.DecryptError):
        crypto.decrypt("no-version-prefix")


def test_key_rotation_dispatch():
    # a token made with the current key still decrypts after a new primary key is prepended
    tok = crypto.encrypt("rotate-me")
    from cryptography.fernet import Fernet
    rotated = crypto.Crypto([Fernet.generate_key().decode(), crypto._active_key()])
    assert rotated.decrypt(tok) == "rotate-me"


def test_decimal_typedecorator_bind_and_result():
    ed = crypto.EncryptedDecimal()
    bound = ed.process_bind_param(Decimal("123.4567"), dialect=None)
    assert bound.startswith("v1:")
    assert ed.process_result_value(bound, dialect=None) == Decimal("123.4567")
    assert ed.process_bind_param(None, dialect=None) is None
    assert ed.process_result_value(None, dialect=None) is None


def test_json_typedecorator_roundtrip():
    ej = crypto.EncryptedJSON()
    payload = {"a": [1, 2], "b": "x"}
    bound = ej.process_bind_param(payload, dialect=None)
    assert bound.startswith("v1:")
    assert ej.process_result_value(bound, dialect=None) == payload


def test_production_requires_encryption_key():
    from cryptography.fernet import Fernet

    # Use a generated key (not the dev key) for production tests
    prod_key = Fernet.generate_key().decode()

    ok = Settings(
        env="production", secret_key="x" * 32, data_encryption_key=prod_key
    )
    validate_production_settings(ok)  # no raise
    with pytest.raises(RuntimeError):
        bad = Settings(env="production", secret_key="x" * 32, data_encryption_key="")
        validate_production_settings(bad)


def test_is_dev_key():
    assert crypto.is_dev_key(crypto._DEV_KEY_REAL) is True
    assert crypto.is_dev_key("different-key") is False
    assert crypto.is_dev_key("") is False
