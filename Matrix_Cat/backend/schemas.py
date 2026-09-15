# -*- coding: utf-8 -*-
"""请求体校验(Pydantic)。响应用 crud 里的 *_public 直接拼 dict。"""
from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    platform: str = "xhs"
    account_id: str
    proxy: str | None = None          # 扫码登录走这个号绑定的代理(不传则用库里已绑的)
    nickname: str | None = None        # 加号时给个友好默认名;登录成功后会被平台真实昵称覆盖


class AccountIn(BaseModel):
    platform: str = "xhs"
    account_id: str
    nickname: str | None = None
    user_id: str | None = None
    proxy: str | None = None          # 绑定的固定独立代理 http://user:pass@host:port


class AccountUpdateIn(BaseModel):
    nickname: str | None = None
    proxy: str | None = None          # 传空串=解绑
    status: str | None = None         # active / disabled
    cooldown_seconds: int | None = None   # 手动冷却:从现在起冷却多少秒(0=解冷却)
    weight: int | None = None         # 人工设养号权重 0-100
    weight_auto: bool | None = None   # True=清掉人工权重,恢复自动算


class AdminAccountUpdateIn(BaseModel):
    status: str | None = None
    proxy: str | None = None
    owner_id: int | None = None       # 改归属用户
    cooldown_seconds: int | None = None


# ---- SaaS 用户 / 会员 --------------------------------------------------------

class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    email: str | None = None
    phone: str | None = None
    nickname: str | None = None


class UserLoginIn(BaseModel):
    username: str
    password: str


class ProfileIn(BaseModel):
    nickname: str | None = None
    email: str | None = None
    phone: str | None = None
    avatar: str | None = None


class PasswordChangeIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


class PurchaseIn(BaseModel):
    plan_code: str


class PayConfirmIn(BaseModel):
    out_trade_no: str


# ---- 总后台(管理员)--------------------------------------------------------

class AdminUserUpdateIn(BaseModel):
    status: str | None = None        # active / disabled
    is_admin: bool | None = None
    account_quota: int | None = None


class AdminGrantIn(BaseModel):
    plan_code: str
    days: int | None = None          # 不填按套餐默认时长


class AdminPlanUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    price_cents: int | None = None
    duration_days: int | None = None
    account_quota: int | None = None
    active: bool | None = None
    sort: int | None = None
