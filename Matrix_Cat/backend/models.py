# -*- coding: utf-8 -*-
"""数据模型:账号 / 任务 / 任务日志。SQLAlchemy 2.0 typed。

账号是矩阵的核心;storage_state(登录态)加密存 storage_state_enc。
发布走任务:一条内容 → 多个任务(每号一个),任务进队列由 worker 执行(第2步)。
"""
import time

from sqlalchemy import BigInteger, ForeignKey, JSON, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now_epoch() -> int:
    return int(time.time())


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("platform", "account_id", name="uq_platform_account"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    account_id: Mapped[str] = mapped_column(String(128), index=True)      # 人给的账号名
    nickname: Mapped[str | None] = mapped_column(String(128), default=None)
    avatar: Mapped[str | None] = mapped_column(Text, default=None)
    user_id: Mapped[str | None] = mapped_column(String(128), default=None)  # 平台侧真实 uid
    storage_state_enc: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)  # Fernet 加密的登录态
    logged_in: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(16), default="active")     # active/disabled(停用后不参与发布/登录)
    cooldown_until: Mapped[int] = mapped_column(BigInteger, default=0)     # 风控冷却到期 epoch
    last_login_at: Mapped[int | None] = mapped_column(BigInteger, default=None)
    last_used_at: Mapped[int | None] = mapped_column(BigInteger, default=None)
    proxy: Mapped[str | None] = mapped_column(String(256), default=None)   # 该号绑定的固定独立代理(纯云 SaaS 命门:一号一 IP)
    last_proxy: Mapped[str | None] = mapped_column(String(256), default=None)  # 上次实际用的代理(登录/发布时回填)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True, default=None)  # SaaS 归属用户
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_epoch)
    weight_manual: Mapped[int | None] = mapped_column(default=None)   # 人工权重覆盖 0-100;None=用自动算的养号健康分


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="image")         # image / video
    title: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    topics: Mapped[list] = mapped_column(JSON, default=list)
    visibility: Mapped[str] = mapped_column(String(32), default="self_only")
    is_original: Mapped[bool] = mapped_column(default=False)
    media: Mapped[list] = mapped_column(JSON, default=list)                # 素材路径
    options: Mapped[dict] = mapped_column(JSON, default=dict)              # 平台专属参数(如 B站 tid 分区 / source 转载来源),透传给采集层 CLI
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued/scheduled/running/success/failed/canceled
    result_url: Mapped[str | None] = mapped_column(Text, default=None)
    item_id: Mapped[str | None] = mapped_column(String(128), default=None)
    attempt: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=3)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    scheduled_at: Mapped[int] = mapped_column(BigInteger, default=0)       # 定时发布 epoch;0=立即
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_epoch)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=now_epoch, onupdate=now_epoch)


class TaskLog(Base):
    __tablename__ = "task_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    ts: Mapped[int] = mapped_column(BigInteger, default=now_epoch)
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text, default="")


# ---- SaaS 侧:平台自己的用户 / 会员套餐 / 订单 --------------------------------

class User(Base):
    """矩阵猫平台的注册用户(买家),跟各平台的社媒账号不是一回事。"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(128), unique=True, default=None)
    phone: Mapped[str | None] = mapped_column(String(32), default=None)
    password_hash: Mapped[str] = mapped_column(String(256))          # scrypt/pbkdf2,不存明文
    nickname: Mapped[str | None] = mapped_column(String(64), default=None)
    avatar: Mapped[str | None] = mapped_column(Text, default=None)
    is_admin: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/disabled
    # 会员态:付款后由订单激活,过期自动降回 free
    plan_code: Mapped[str] = mapped_column(String(32), default="free")
    plan_expires_at: Mapped[int] = mapped_column(BigInteger, default=0)   # 0=不过期(free)
    account_quota: Mapped[int] = mapped_column(default=1)                 # 可绑定的社媒号数
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_epoch)
    last_login_at: Mapped[int | None] = mapped_column(BigInteger, default=None)


class Plan(Base):
    """会员套餐,按能绑的号数分档。开箱 seed 一批,可后台改。"""
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    price_cents: Mapped[int] = mapped_column(default=0)     # 单位:分
    duration_days: Mapped[int] = mapped_column(default=30)
    account_quota: Mapped[int] = mapped_column(default=1)
    description: Mapped[str] = mapped_column(Text, default="")
    sort: Mapped[int] = mapped_column(default=0)
    active: Mapped[bool] = mapped_column(default=True)


class Order(Base):
    """购买会员的订单。付款回调把 status 置 paid,再去激活用户会员。"""
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    out_trade_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)  # 订单号
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan_code: Mapped[str] = mapped_column(String(32))
    plan_name: Mapped[str] = mapped_column(String(64), default="")
    amount_cents: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending/paid/canceled
    pay_method: Mapped[str] = mapped_column(String(16), default="mock")             # mock/alipay/wechat
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_epoch)
    paid_at: Mapped[int | None] = mapped_column(BigInteger, default=None)
