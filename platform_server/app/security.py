import hashlib
import secrets
from datetime import timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .config import get_settings
from .utils import now_utc

settings = get_settings()
_ph = PasswordHasher()


# ---- 密码 ----
def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw_hash: str | None, pw: str) -> bool:
    if not pw_hash:
        return False
    try:
        return _ph.verify(pw_hash, pw)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


# ---- access token(JWT,无状态) ----
def make_access_token(user_id: str, jti: str) -> str:
    now = now_utc()
    payload = {
        "sub": user_id,
        "jti": jti,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_ttl_min),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def decode_access_token(token: str) -> dict:
    # 坏的/过期的会抛异常,交给调用方转成 401
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg])


# ---- refresh token(随机串,存库存的是它的 sha256) ----
def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
