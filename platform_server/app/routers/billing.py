from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import audit
from ..db import get_session
from ..deps import get_current_user
from ..models import Plan, Subscription, User
from ..utils import as_utc, now_utc

router = APIRouter(prefix="/billing", tags=["billing"])


class SubscribeReq(BaseModel):
    plan_code: str


@router.get("/plans")
async def list_plans(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(
        select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.price_cents))).scalars().all()
    return {"ok": True, "plans": [{
        "code": p.code, "name": p.name,
        "max_accounts": p.max_accounts, "max_sessions": p.max_sessions,
        "duration_days": p.duration_days,
        "price_cents": p.price_cents, "price_yuan": round(p.price_cents / 100, 2),
    } for p in rows]}


@router.post("/subscribe")
async def subscribe(body: SubscribeReq, request: Request,
                    user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_session)):
    """开通/续费会员。这里是 mock:付款即成功。接真支付时改成网关回调里再落订阅,激活逻辑不变。"""
    plan = (await db.execute(
        select(Plan).where(Plan.code == body.plan_code, Plan.is_active.is_(True)))).scalar_one_or_none()
    if not plan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "套餐不存在")

    now = now_utc()
    cur = (await db.execute(
        select(Subscription).where(Subscription.user_id == user.id, Subscription.status == "active")
        .order_by(Subscription.expires_at.desc()))).scalars().first()

    if cur and cur.plan_id == plan.id and as_utc(cur.expires_at) > now:
        cur.expires_at = as_utc(cur.expires_at) + timedelta(days=plan.duration_days)  # 同套餐续期叠加
        sub = cur
    else:
        if cur:
            cur.status = "expired"        # 换套餐:旧订阅下线
        sub = Subscription(user_id=user.id, plan_id=plan.id, status="active",
                           expires_at=now + timedelta(days=plan.duration_days))
        db.add(sub)
    await db.commit()

    ip = request.client.host if request.client else None
    await audit("subscribe", user_id=user.id, ip=ip, detail={"plan": plan.code})
    return {"ok": True, "plan_code": plan.code, "plan_name": plan.name,
            "expires_at": as_utc(sub.expires_at).isoformat()}
