# -*- coding: utf-8 -*-
"""平台用户鉴权:密码哈希 + 登录 token + 取当前用户。

没装 argon2/PyJWT,这里全用标准库搞定:
  - 密码:hashlib.scrypt(内存硬,抗爆破),跑不动就退回 pbkdf2;哈希串自带算法前缀,校验时认得出。
  - token:自己签的 HS256(hmac-sha256),格式跟 JWT 一样 header.payload.sig,不依赖第三方库。
密钥:settings.jwt_secret,空就在 storage/data/.jwt.key 自动生成一把(已被 .gitignore 挡在库外)。
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .config import DATA_DIR, settings
from .db import get_db
from .models import User

_KEY_FILE = DATA_DIR / ".jwt.key"


# ---- 密码哈希 ----------------------------------------------------------------

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 16384, 8, 1
_PBKDF2_ITERS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    pw = password.encode("utf-8")
    try:
        dk = hashlib.scrypt(pw, salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
                            maxmem=64 * 1024 * 1024, dklen=32)
        return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${dk.hex()}"
    except Exception:
        dk = hashlib.pbkdf2_hmac("sha256", pw, salt, _PBKDF2_ITERS, dklen=32)
        return f"pbkdf2${_PBKDF2_ITERS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        pw = password.encode("utf-8")
        parts = stored.split("$")
        if parts[0] == "scrypt":
            _, n, r, p, salt_hex, hash_hex = parts
            dk = hashlib.scrypt(pw, salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p),
                                maxmem=64 * 1024 * 1024, dklen=len(hash_hex) // 2)
        elif parts[0] == "pbkdf2":
            _, iters, salt_hex, hash_hex = parts
            dk = hashlib.pbkdf2_hmac("sha256", pw, bytes.fromhex(salt_hex), int(iters), dklen=len(hash_hex) // 2)
        else:
            return False
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ---- token(自签 HS256)-------------------------------------------------------

def _secret() -> bytes:
    if settings.jwt_secret:
        return settings.jwt_secret.encode("utf-8")
    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes()
    key = secrets.token_bytes(32)
    _KEY_FILE.write_bytes(key)
    try:
        os.chmod(_KEY_FILE, 0o600)
    except Exception:
        pass
    return key


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(user_id: int, ttl_hours: int | None = None) -> str:
    now = int(time.time())
    ttl = (ttl_hours if ttl_hours is not None else settings.token_ttl_hours) * 3600
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(json.dumps({"sub": str(user_id), "iat": now, "exp": now + ttl},
                              separators=(",", ":")).encode())
    seg = f"{header}.{payload}"
    sig = _b64(hmac.new(_secret(), seg.encode(), hashlib.sha256).digest())
    return f"{seg}.{sig}"


def decode_token(token: str) -> dict:
    """验签 + 验过期,返回 payload;不合法就抛 ValueError。"""
    try:
        header, payload, sig = token.split(".")
    except ValueError:
        raise ValueError("token 格式不对")
    expect = _b64(hmac.new(_secret(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expect):
        raise ValueError("签名不匹配")
    data = json.loads(_unb64(payload))
    if int(data.get("exp", 0)) < int(time.time()):
        raise ValueError("token 已过期")
    return data


# ---- FastAPI 依赖 ------------------------------------------------------------

def _extract(request: Request) -> str | None:
    h = request.headers.get("Authorization", "")
    if h.startswith("Bearer "):
        return h[7:].strip()
    return None


def current_user_optional(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = _extract(request)
    if not token:
        return None
    try:
        data = decode_token(token)
        user = db.get(User, int(data["sub"]))
    except Exception:
        return None
    if user and user.status == "active":
        return user
    return None


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _extract(request)
    if not token:
        raise HTTPException(401, "请先登录")
    try:
        data = decode_token(token)
    except ValueError as e:
        raise HTTPException(401, f"登录态无效:{e}")
    user = db.get(User, int(data["sub"]))
    if not user or user.status != "active":
        raise HTTPException(401, "用户不存在或已停用")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return user
