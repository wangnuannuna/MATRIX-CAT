import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models import AuthSession, Plan, Subscription, User
from .security import decode_access_token
from .utils import as_utc, now_utc


async def get_store(request: Request):
    return request.app.state.store


async def get_current_user(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_session),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少 access token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已过期,请刷新")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token 无效")
    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token 类型不对")

    # 校验会话没被吊销/踢下线——这样"踢下线、同时在线数限制"对已发出的 access 也能立刻生效
    sess = await db.get(AuthSession, payload.get("jti"))
    if not sess or sess.revoked or as_utc(sess.expires_at) < now_utc():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "会话已失效,请重新登录")
    # jti 必须和 sub 属同一用户——万一密钥泄露被伪造,也挡住"拿自己的会话冒用别人 sub"
    if str(sess.user_id) != str(payload.get("sub")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token 主体不一致")

    user = await db.get(User, payload.get("sub"))
    if not user or user.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号不存在或已封禁")
    return user


async def get_membership(user: User, db: AsyncSession):
    """返回 (有效订阅 or None, 套餐 or None)。过期则订阅返回 None、套餐仍给出。"""
    row = (
        await db.execute(
            select(Subscription, Plan)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(Subscription.user_id == user.id, Subscription.status == "active")
            .order_by(Subscription.expires_at.desc())
        )
    ).first()
    if not row:
        return None, None
    sub, plan = row
    if as_utc(sub.expires_at) < now_utc():
        return None, plan
    return sub, plan


async def require_membership(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """需要有效会员才能进的接口用它。返回 (user, plan)。"""
    sub, plan = await get_membership(user, db)
    if not sub:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "会员已过期或未开通")
    return user, plan
