from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import audit
from ..db import get_session
from ..deps import get_current_user, require_membership
from ..models import SocialAccount, User
from ..schemas import BindAccountReq, SocialAccountResp

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("/bind", response_model=SocialAccountResp)
async def bind(body: BindAccountReq, request: Request, ctx=Depends(require_membership),
               db: AsyncSession = Depends(get_session)):
    """绑一个社媒号——演示"按套餐可绑账号数分档"的配额闸"""
    user, plan = ctx
    # 锁住用户行,把"数配额→插入"串行化(PG 生效),防并发绑号击穿 max_accounts
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())

    used = (await db.execute(
        select(func.count()).select_from(SocialAccount).where(
            SocialAccount.user_id == user.id))).scalar_one()
    if used >= plan.max_accounts:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            f"已达套餐可绑账号上限({plan.max_accounts}),请升级套餐")

    acc = SocialAccount(user_id=user.id, platform=body.platform, account_name=body.account_name)
    db.add(acc)
    try:
        await db.commit()
    except IntegrityError:
        # 撞唯一约束 = 同号重复绑
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "该账号已经绑过了")
    await db.refresh(acc)
    ip = request.client.host if request.client else None
    await audit("bind_account", user_id=user.id, ip=ip,
                detail={"platform": body.platform, "name": body.account_name})
    return acc


@router.get("", response_model=list[SocialAccountResp])
async def list_accounts(user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(
        select(SocialAccount).where(SocialAccount.user_id == user.id)
        .order_by(SocialAccount.created_at.desc()))).scalars().all()
    return rows


@router.delete("/{acc_id}")
async def unbind(acc_id: str, user: User = Depends(get_current_user),
                 db: AsyncSession = Depends(get_session)):
    acc = await db.get(SocialAccount, acc_id)
    # 不属于你就当不存在,防越权(IDOR)
    if not acc or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "账号不存在")
    await db.delete(acc)
    await db.commit()
    return {"ok": True}
