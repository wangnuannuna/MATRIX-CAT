# -*- coding: utf-8 -*-
"""凭证加密。账号 storage_state = 登录态 = 凭证,加密落库,绝不明文。

密钥来源:
  1) 环境变量 MATRIXCAT_SECRET_KEY(Fernet key,生产走这个,别进代码库);
  2) 没有就本机生成一把存 storage/data/.secret.key(已被 .gitignore 挡在库外)。
换密钥会导致旧数据解不开——生产环境固定一把、单独保管。
"""
import json
import os

from cryptography.fernet import Fernet

from .config import DATA_DIR

_KEY_FILE = DATA_DIR / ".secret.key"


def _load_key() -> bytes:
    env = os.getenv("MATRIXCAT_SECRET_KEY")
    if env:
        return env.encode()
    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    _KEY_FILE.write_bytes(key)
    try:
        os.chmod(_KEY_FILE, 0o600)
    except Exception:
        pass
    return key


_fernet = Fernet(_load_key())


def encrypt_json(obj) -> bytes | None:
    """dict/list → 加密字节(存 DB)。None 原样返回。"""
    if obj is None:
        return None
    return _fernet.encrypt(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def decrypt_json(blob: bytes | None):
    """加密字节 → dict/list。空返回 None。"""
    if not blob:
        return None
    return json.loads(_fernet.decrypt(blob).decode("utf-8"))
