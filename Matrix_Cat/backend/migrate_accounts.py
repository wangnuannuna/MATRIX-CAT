# -*- coding: utf-8 -*-
"""把采集层的真账号(collectors/<平台>_publisher/data/accounts.json)迁进后端 DB,
storage_state 加密落库。之后正式台/API 就吃真号,真发布(MATRIXCAT_DRYRUN=0)才有登录态。

    python -m backend.migrate_accounts          # 迁 xhs
    python -m backend.migrate_accounts douyin   # 迁指定平台
只读采集的 accounts.json,不改它。
"""
import json
import sys

from . import crud
from .config import ROOT
from .db import SessionLocal, init_db


def run(platform: str = "xhs") -> int:
    init_db()
    f = ROOT / "collectors" / f"{platform}_publisher" / "data" / "accounts.json"
    if not f.exists():
        print(f"没找到 {f}(该平台还没登录过账号?)")
        return 0
    data = json.loads(f.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        print("accounts.json 格式不对(应是数组)")
        return 0
    db = SessionLocal()
    n = 0
    try:
        for a in data:
            aid = a.get("account_id")
            if not aid:
                continue
            crud.upsert_account(
                db, platform, aid,
                nickname=a.get("nickname"),
                user_id=a.get("xhs_user_id") or a.get("user_id"),
                avatar=a.get("avatar"),
                storage_state=a.get("storage_state"),   # 加密落库
            )
            n += 1
        print(f"迁入 {n} 个 {platform} 账号(storage_state 已加密存 DB)")
        return n
    finally:
        db.close()


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "xhs")
