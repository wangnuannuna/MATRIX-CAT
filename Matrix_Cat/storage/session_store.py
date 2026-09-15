# -*- coding: utf-8 -*-
"""账号 / 会话存储:一份 accounts.json,每号带 storage_state(CloakBrowser/Playwright 格式)。
登录一次落盘,之后协议直发反复读,直到 cookie 过期再重登。

这是跨平台通用版——xhs_publisher/xhs/accounts.py 是它的 XHS 特化(多了 a1/b1 指纹契约校验)。
新平台直接:
    from storage.session_store import AccountStore
    store = AccountStore(cfg.ACCOUNTS_FILE, cookie_domain="douyin.com")

字段跟 HuiMei accounts.json 对齐,需要的话把 ACCOUNTS_FILE 指到同一份就能共用账号池。
"""
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def cookies_for_domain(storage_state: Any, domain: str) -> Dict[str, str]:
    """从 storage_state.cookies 里挑指定域的 cookie → {name: value}。"""
    if isinstance(storage_state, str):
        try:
            storage_state = json.loads(storage_state)
        except Exception:
            return {}
    out: Dict[str, str] = {}
    for c in ((storage_state or {}).get("cookies") or []):
        if domain and domain in (c.get("domain") or ""):
            name = c.get("name")
            if name:
                out[name] = c.get("value", "")
    return out


class AccountStore:
    """单 JSON 文件,进程内线程安全,原子写。按 user_id 去重(拿不到再退回 account_id)。"""

    def __init__(self, accounts_file: str, *, cookie_domain: str = "",
                 cookie_extractor: Optional[Callable[[Any], Dict[str, str]]] = None):
        self._path = Path(accounts_file)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._domain = cookie_domain
        self._extract = cookie_extractor  # 平台想自定义抽 cookie 就传这个,否则按域名挑

    # ---- 落盘 ----------------------------------------------------------------
    def _load(self) -> List[Dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"accounts.json 解析失败: {e};当空处理")
            return []

    def _save(self, accounts: List[Dict[str, Any]]) -> None:
        # 先写 .tmp 再 rename,防止写一半被并发读到半截 JSON
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def _find(self, accounts: List[Dict[str, Any]], account_id: str) -> Optional[int]:
        for i, a in enumerate(accounts):
            if a.get("account_id") == account_id:
                return i
        return None

    def _attach_cookies(self, acc: Dict[str, Any]) -> Dict[str, Any]:
        ss = acc.get("storage_state") or {}
        if self._extract:
            acc["cookies"] = self._extract(ss)
        elif self._domain:
            acc["cookies"] = cookies_for_domain(ss, self._domain)
        return acc

    # ---- 读 ------------------------------------------------------------------
    def get(self, account_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for a in self._load():
                if a.get("account_id") == account_id:
                    return self._attach_cookies(dict(a))
        return None

    def list_ids(self) -> List[str]:
        with self._lock:
            return [a.get("account_id") for a in self._load() if a.get("account_id")]

    def list_accounts(self) -> List[Dict[str, Any]]:
        with self._lock:
            return self._load()

    # ---- 写 ------------------------------------------------------------------
    def save_login(self, account_id: str, storage_state: Dict[str, Any], *,
                   nickname: Optional[str] = None, user_id: Optional[str] = None,
                   avatar: Optional[str] = None, proxy: Optional[str] = None) -> None:
        """登录成功后写入 storage_state + 昵称/UID/头像。优先按 user_id 去重(同号换机器不新建)。"""
        now = int(time.time())
        with self._lock:
            accounts = self._load()
            idx = None
            if user_id:
                for i, a in enumerate(accounts):
                    if a.get("user_id") == user_id:
                        idx = i
                        break
            if idx is None:
                idx = self._find(accounts, account_id)
            if idx is None:
                accounts.append({"account_id": account_id, "created_at": now})
                idx = len(accounts) - 1
            accounts[idx].update({
                "account_id": accounts[idx].get("account_id") or account_id,
                "nickname": nickname or accounts[idx].get("nickname"),
                "avatar": avatar or accounts[idx].get("avatar"),
                "user_id": user_id or accounts[idx].get("user_id"),
                "storage_state": storage_state,
                "last_proxy": proxy or accounts[idx].get("last_proxy"),
                "last_used_at": now,
                "last_login_at": now,
            })
            self._save(accounts)

    def mark_used(self, account_id: str, *, proxy: Optional[str] = None) -> None:
        with self._lock:
            accounts = self._load()
            idx = self._find(accounts, account_id)
            if idx is None:
                return
            accounts[idx]["last_used_at"] = int(time.time())
            if proxy:
                accounts[idx]["last_proxy"] = proxy
            self._save(accounts)

    def invalidate(self, account_id: str) -> None:
        """删号(通常是 cookie 失效、需要重登)。"""
        with self._lock:
            accounts = [a for a in self._load() if a.get("account_id") != account_id]
            self._save(accounts)

    # ---- 风控冷却 ------------------------------------------------------------
    def cool_down(self, account_id: str, seconds: int, reason: str = "") -> None:
        until = int(time.time()) + max(60, seconds)
        with self._lock:
            accounts = self._load()
            idx = self._find(accounts, account_id)
            if idx is None:
                return
            accounts[idx]["cooldown_until"] = until
            self._save(accounts)
        logger.warning(f"账号 {account_id} 进入冷却 {seconds}s,原因: {reason}")

    def is_cooling(self, account_id: str) -> Optional[int]:
        """还在冷却就返回剩余秒数,否则 None。"""
        a = self.get(account_id)
        if not a:
            return None
        left = (a.get("cooldown_until") or 0) - int(time.time())
        return left if left > 0 else None

    # ---- 统计 ----------------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        now = int(time.time())
        with self._lock:
            accounts = self._load()
            return {
                "total": len(accounts),
                "cooling": sum(1 for a in accounts if (a.get("cooldown_until") or 0) > now),
                "logged_in": sum(1 for a in accounts if a.get("user_id")),
                "file": str(self._path),
            }
