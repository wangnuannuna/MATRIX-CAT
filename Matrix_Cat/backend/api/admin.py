# -*- coding: utf-8 -*-
"""总后台(仅管理员):概览 / 用户管理 / 订单 / 套餐。

整个路由都挂 require_admin,所以非管理员连不进来。用户拿自己的号登录,是管理员
就能进 /admin —— 不另开一套后台账号。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import time

from .. import auth, crud
from ..db import get_db
from ..models import User
from ..schemas import AdminAccountUpdateIn, AdminGrantIn, AdminPlanUpdateIn, AdminUserUpdateIn

router = APIRouter(tags=["admin"], prefix="/admin", dependencies=[Depends(auth.require_admin)])


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    return {"ok": True, "stats": crud.admin_stats(db)}


@router.get("/users")
def users(q: str = "", db: Session = Depends(get_db)):
    return {"ok": True, "users": [crud.user_admin_view(db, u) for u in crud.list_users(db, q or None)]}


@router.patch("/users/{uid}")
def update_user(uid: int, body: AdminUserUpdateIn,
                admin: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    u = crud.get_user(db, uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    # 防自锁:别把自己停用、也别撤掉自己的管理员
    if u.id == admin.id and (body.status == "disabled" or body.is_admin is False):
        raise HTTPException(400, "不能停用或撤销自己的管理员权限")
    u = crud.admin_update_user(db, u, status=body.status, is_admin=body.is_admin,
                               account_quota=body.account_quota)
    return {"ok": True, "user": crud.user_admin_view(db, u)}


@router.post("/users/{uid}/grant")
def grant(uid: int, body: AdminGrantIn, db: Session = Depends(get_db)):
    u = crud.get_user(db, uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    plan = crud.get_plan(db, body.plan_code)
    if not plan:
        raise HTTPException(404, "套餐不存在")
    u = crud.admin_grant_plan(db, u, plan, days=body.days)
    return {"ok": True, "user": crud.user_admin_view(db, u)}


@router.get("/accounts")
def accounts(platform: str = "", q: str = "", db: Session = Depends(get_db)):
    rows = crud.list_accounts_admin(db, platform or None, q or None)
    return {"ok": True, "accounts": [crud.account_admin_view(db, a) for a in rows]}


@router.patch("/accounts/{pk}")
def update_account(pk: int, body: AdminAccountUpdateIn, db: Session = Depends(get_db)):
    a = crud.get_account_by_pk(db, pk)
    if not a:
        raise HTTPException(404, "账号不存在")
    # 改归属:传 0 表示解绑(转成本机公用号);传别的先校验用户存在。不传则不动。
    if body.owner_id is not None:
        if body.owner_id == 0:
            a.owner_id = None
        elif crud.get_user(db, body.owner_id):
            a.owner_id = body.owner_id
        else:
            raise HTTPException(404, "归属用户不存在")
    cooldown_until = None
    if body.cooldown_seconds is not None:
        cooldown_until = int(time.time()) + max(0, body.cooldown_seconds)
    a = crud.update_account(db, a, proxy=body.proxy, status=body.status,
                            cooldown_until=cooldown_until)
    return {"ok": True, "account": crud.account_admin_view(db, a)}


@router.delete("/accounts/{pk}")
def delete_account(pk: int, db: Session = Depends(get_db)):
    a = crud.get_account_by_pk(db, pk)
    if not a:
        raise HTTPException(404, "账号不存在")
    crud.delete_account(db, a)
    return {"ok": True}


@router.get("/orders")
def orders(status: str = "", db: Session = Depends(get_db)):
    return {"ok": True, "orders": [crud.order_admin_view(db, o) for o in crud.list_all_orders(db, status or None)]}


@router.get("/plans")
def plans(db: Session = Depends(get_db)):
    return {"ok": True, "plans": [crud.plan_public(p) for p in crud.list_plans(db, only_active=False)]}


@router.patch("/plans/{code}")
def update_plan(code: str, body: AdminPlanUpdateIn, db: Session = Depends(get_db)):
    p = crud.get_plan(db, code)
    if not p:
        raise HTTPException(404, "套餐不存在")
    p = crud.update_plan(db, p, **body.model_dump())
    return {"ok": True, "plan": crud.plan_public(p)}
