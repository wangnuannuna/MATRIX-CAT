# -*- coding: utf-8 -*-
"""会员:套餐列表 / 我的会员 / 下单购买 / 支付确认 / 订单记录。

支付走 payments.py 的可插拔渠道(settings.pay_provider):
  - mock:  下单返回本地确认链接,前端调 /membership/pay 即视为已付(仅本地/演示)。
  - alipay:下单调支付宝当面付拿二维码;真付款由支付宝异步回调 /membership/notify/alipay 激活。
激活逻辑(crud.mark_order_paid)与渠道无关,换渠道不用动。
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import auth, crud, payments
from ..config import settings
from ..db import get_db
from ..models import User
from ..schemas import PayConfirmIn, PurchaseIn

router = APIRouter(tags=["membership"])


@router.get("/plans")
def plans(db: Session = Depends(get_db)):
    return {"ok": True, "plans": [crud.plan_public(p) for p in crud.list_plans(db)]}


@router.get("/membership")
def my_membership(user: User = Depends(auth.require_user), db: Session = Depends(get_db)):
    return {"ok": True, "membership": crud.membership_status(db, user)}


@router.post("/membership/purchase")
def purchase(body: PurchaseIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)):
    plan = crud.get_plan(db, body.plan_code)
    if not plan or not plan.active:
        raise HTTPException(404, "套餐不存在")
    if plan.code == "free" or plan.price_cents <= 0:
        raise HTTPException(400, "免费套餐无需购买")
    try:
        provider = payments.get_provider(settings.pay_provider)
        order = crud.create_order(db, user, plan, pay_method=provider.name)
        pay = provider.create_payment(order)
    except payments.PaymentError as e:
        raise HTTPException(400, f"支付渠道异常:{e}")
    return {"ok": True, "order": crud.order_public(order), **pay}


@router.post("/membership/pay")
def pay(body: PayConfirmIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)):
    """手动确认(仅 mock 渠道)。真支付由网关异步回调激活,不走这。"""
    if settings.pay_provider != "mock":
        raise HTTPException(400, "当前为真实支付渠道,请扫码付款后由网关回调激活")
    order = crud.get_order(db, body.out_trade_no)
    if not order or order.user_id != user.id:
        raise HTTPException(404, "订单不存在")
    if order.status == "canceled":
        raise HTTPException(400, "订单已取消")
    crud.mark_order_paid(db, order)
    return {"ok": True, "order": crud.order_public(order),
            "membership": crud.membership_status(db, user)}


@router.post("/membership/notify/alipay")
async def alipay_notify(request: Request, db: Session = Depends(get_db)):
    """支付宝异步回调(服务器到服务器)。验签通过且交易成功才激活会员。"""
    form = dict((await request.form()))
    try:
        provider = payments.get_provider("alipay")
        otn = provider.verify_callback(form)
    except payments.PaymentError as e:
        raise HTTPException(400, str(e))
    if not otn:
        return "success"                       # 非成功状态的通知,照收不激活(支付宝要求回 success)
    order = crud.get_order(db, otn)
    if order and order.status != "paid":
        crud.mark_order_paid(db, order)
    return "success"                           # 必须回字面量 success,否则支付宝会重复通知


@router.get("/membership/orders")
def orders(user: User = Depends(auth.require_user), db: Session = Depends(get_db)):
    return {"ok": True, "orders": [crud.order_public(o) for o in crud.list_user_orders(db, user.id)]}
