# -*- coding: utf-8 -*-
"""社媒账号管理:增删改查 + 绑代理 + 扫码登录。

多租户:登录用户只看/管自己的号(owner_id),管理员在 /admin/accounts 管全部。
本机总控台(MATRIXCAT_LOCAL_CONSOLE=1,默认)不带 token 也能全量操作,方便本地调试;
正式 SaaS 部署设成 0,匿名请求一律挡在门外,杜绝跨租户越权。
"""
import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import auth, crud
from ..config import settings
from ..db import get_db
from ..models import User
from ..schemas import AccountIn, AccountUpdateIn, LoginIn

router = APIRouter(tags=["accounts"])
logger = logging.getLogger("matrixcat")


def _guard_quota(db: Session, user: User, platform: str, account_id: str) -> None:
    """带登录态时:超出会员号数额度就拦下(已绑过的同一个号不重复计)。"""
    existing = crud.get_account(db, platform, account_id)
    is_new_for_user = existing is None or existing.owner_id != user.id
    if is_new_for_user:
        m = crud.membership_status(db, user)
        if m["remaining"] <= 0:
            raise HTTPException(403, f"账号数已达套餐上限({m['quota']} 个),升级会员后可绑更多")


def _resolve_account(pk: int, user: User | None, db: Session):
    """按归属取号:登录用户只能碰自己的(管理员除外);本机总控台匿名放行。

    找不到 / 不归你,都统一回 404(不泄露号是否存在)。
    """
    a = crud.get_account_by_pk(db, pk)
    if not a:
        raise HTTPException(404, "账号不存在")
    if user is None:
        if settings.local_console:
            return a
        raise HTTPException(401, "请先登录")
    if user.is_admin or a.owner_id == user.id:
        return a
    raise HTTPException(404, "账号不存在")


@router.get("/accounts")
def list_accounts(platform: str = "xhs", user: User | None = Depends(auth.current_user_optional),
                  db: Session = Depends(get_db)):
    # 登录了只看自己的号;本机总控台不带 token 时看全部,正式部署匿名被挡。
    if user is None and not settings.local_console:
        raise HTTPException(401, "请先登录")
    owner_id = user.id if user else None
    return {"ok": True, "accounts": [crud.account_public(a, db)
                                     for a in crud.list_accounts(db, platform, owner_id=owner_id)]}


@router.post("/accounts")
def register(body: AccountIn, user: User | None = Depends(auth.current_user_optional),
             db: Session = Depends(get_db)):
    if user is None and not settings.local_console:
        raise HTTPException(401, "请先登录")
    if user:
        _guard_quota(db, user, body.platform, body.account_id)
    a = crud.upsert_account(db, body.platform, body.account_id, nickname=body.nickname,
                            user_id=body.user_id, proxy=body.proxy,
                            owner_id=(user.id if user else None))
    return {"ok": True, "account": crud.account_public(a, db)}


@router.get("/accounts/{pk}")
def get_one(pk: int, user: User | None = Depends(auth.current_user_optional),
            db: Session = Depends(get_db)):
    a = _resolve_account(pk, user, db)
    return {"ok": True, "account": crud.account_public(a, db)}


@router.patch("/accounts/{pk}")
def update_one(pk: int, body: AccountUpdateIn,
               user: User | None = Depends(auth.current_user_optional), db: Session = Depends(get_db)):
    a = _resolve_account(pk, user, db)
    cooldown_until = None
    if body.cooldown_seconds is not None:
        cooldown_until = int(time.time()) + max(0, body.cooldown_seconds)
    weight_manual = crud.UNSET
    if body.weight_auto:
        weight_manual = None                              # 恢复自动算养号分
    elif body.weight is not None:
        weight_manual = max(0, min(100, body.weight))     # 人工覆盖 0-100
    a = crud.update_account(db, a, nickname=body.nickname, proxy=body.proxy,
                            status=body.status, cooldown_until=cooldown_until,
                            weight_manual=weight_manual)
    return {"ok": True, "account": crud.account_public(a, db)}


@router.delete("/accounts/{pk}")
def delete_one(pk: int, user: User | None = Depends(auth.current_user_optional),
               db: Session = Depends(get_db)):
    a = _resolve_account(pk, user, db)
    crud.delete_account(db, a)
    return {"ok": True}


@router.post("/login")
def login(body: LoginIn, user: User | None = Depends(auth.current_user_optional),
          db: Session = Depends(get_db)):
    """真扫码登录:登记账号 → 派活给采集 worker(起 CloakBrowser 弹码)→ 前端轮询 /login/status。"""
    if user is None and not settings.local_console:
        raise HTTPException(401, "请先登录")
    if user:
        _guard_quota(db, user, body.platform, body.account_id)
    # 先把号(连同这次要用的代理)落库,worker 按 pk 取。昵称仅在新号/尚无名时设,别覆盖登录拿到的真实昵称。
    existing = crud.get_account(db, body.platform, body.account_id)
    nick = body.nickname if (existing is None or not existing.nickname) else None
    a = crud.upsert_account(db, body.platform, body.account_id, proxy=body.proxy,
                            nickname=nick, owner_id=(user.id if user else None))
    if a.status == "disabled":
        raise HTTPException(400, "账号已停用,先启用再登录")

    from ..jobs import login as login_jobs   # 延迟 import:API 不硬依赖 redis/dramatiq
    if not login_jobs.enqueue_login(a.id):
        raise HTTPException(503, "登录服务未就绪:请先启动 Redis 和采集 worker(python -m dramatiq backend.jobs.tasks)")
    logger.info("扫码登录 · %s · %s · pk=%s(已派发,等二维码)",
                body.platform, (a.nickname or body.account_id), a.id)
    return {"ok": True, "polling": True, "pk": a.id,
            "message": "已派发扫码登录,轮询 /api/login/status 获取二维码与结果"}


@router.get("/login/status")
def login_status(pk: int, user: User | None = Depends(auth.current_user_optional),
                 db: Session = Depends(get_db)):
    _resolve_account(pk, user, db)              # 复用归属校验:不是你的号不给看进度
    from ..jobs import login as login_jobs
    return {"ok": True, **login_jobs.get_progress(pk)}
