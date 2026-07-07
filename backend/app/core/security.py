import bcrypt
from itsdangerous import BadSignature, TimestampSigner

from app.core.config import settings

_signer = TimestampSigner(settings.secret_key)
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    return bcrypt.checkpw(pw.encode(), hashed.encode())


def sign_session(user_id: int) -> str:
    return _signer.sign(str(user_id)).decode()


def read_session(token: str) -> int | None:
    try:
        raw = _signer.unsign(token, max_age=SESSION_MAX_AGE_SECONDS)
        return int(raw)
    except (BadSignature, ValueError):
        return None
