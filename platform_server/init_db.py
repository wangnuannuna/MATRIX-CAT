"""建表 + 初始化套餐。首次跑或改了套餐后执行:python init_db.py"""
import asyncio

from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import Plan

PLANS = [
    dict(code="trial",    name="体验版", max_accounts=2,    max_sessions=1,  duration_days=3,  price_cents=0),
    dict(code="basic",    name="基础版", max_accounts=20,   max_sessions=1,  duration_days=30, price_cents=9900),
    dict(code="pro",      name="专业版", max_accounts=100,  max_sessions=3,  duration_days=30, price_cents=29900),
    dict(code="flagship", name="旗舰版", max_accounts=1000, max_sessions=10, duration_days=30, price_cents=99900),
]


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as db:
        for p in PLANS:
            exist = (await db.execute(select(Plan).where(Plan.code == p["code"]))).scalar_one_or_none()
            if exist:
                for k, v in p.items():
                    setattr(exist, k, v)   # 有就更新一下参数
            else:
                db.add(Plan(**p))
        await db.commit()
    print("[OK] 建表完成 + 套餐已初始化:", ", ".join(p["code"] for p in PLANS))


if __name__ == "__main__":
    asyncio.run(main())
