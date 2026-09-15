# -*- coding: utf-8 -*-
"""塞点演示账号,方便骨架阶段看 /api/accounts 有数据。
    python -m backend.seed
真实账号在第2步由采集登录写入(加密落库),这里只是 demo。
"""
import time

from . import crud
from .db import SessionLocal, init_db

DEMO = [
    # 昵称, 账号名, 平台uid, 是否已登录, 冷却剩余秒(0=不冷却)
    ("探店日记·小美", "xiaomei_20", "u_1001", True, 0),
    ("城市漫游家", "manyou_21", "u_1002", True, 0),
    ("手冲研究室", "shouchong_22", "u_1003", False, 252),
    ("穿搭笔记_Lin", "lin_23", "u_1004", True, 0),
    ("日杂好物铺", "zaji_24", "u_1005", True, 0),
    ("人间烟火气", "yanhuo_25", None, False, 0),
    ("周末去哪儿", "weekend_26", "u_1007", True, 0),
]


def run():
    init_db()
    db = SessionLocal()
    now = int(time.time())
    for nick, aid, uid, li, cd in DEMO:
        crud.upsert_account(db, "xhs", aid, nickname=nick, user_id=uid, logged_in=li,
                            cooldown_until=(now + cd) if cd else 0)
    print(f"seeded {len(DEMO)} accounts")
    db.close()


if __name__ == "__main__":
    run()
