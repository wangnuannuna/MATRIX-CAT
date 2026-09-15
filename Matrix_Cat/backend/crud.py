# -*- coding: utf-8 -*-
"""数据库操作。所有对 DB 的读写走这,路由层只调这里。"""
import secrets
import time

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from . import auth, security
from .config import DEFAULT_PLANS
from .models import Account, Order, Plan, Task, TaskLog, User

UNSET = object()   # 区分「没传该参数」与「显式传 None」(PATCH 清空人工权重用)


# ---- accounts ----------------------------------------------------------------
def list_accounts(db: Session, platform: str | None = None, owner_id: int | None = None) -> list[Account]:
    stmt = select(Account)
    if platform:
        stmt = stmt.where(Account.platform == platform)
    if owner_id is not None:
        stmt = stmt.where(Account.owner_id == owner_id)
    return list(db.scalars(stmt.order_by(Account.id)))


def get_account(db: Session, platform: str, account_id: str) -> Account | None:
    return db.scalar(select(Account).where(Account.platform == platform, Account.account_id == account_id))


def get_account_by_pk(db: Session, pk: int) -> Account | None:
    return db.get(Account, pk)


def upsert_account(db: Session, platform: str, account_id: str, *, nickname=None, user_id=None,
                   avatar=None, storage_state=None, logged_in=None, cooldown_until=None,
                   owner_id=None, proxy=None, status=None, last_proxy=None) -> Account:
    """登记/更新账号。storage_state 传进来会加密存。只更新给了值的字段。"""
    a = get_account(db, platform, account_id)
    if a is None:
        a = Account(platform=platform, account_id=account_id)
        db.add(a)
    if owner_id is not None:
        a.owner_id = owner_id
    if nickname is not None:
        a.nickname = nickname
    if user_id is not None:
        a.user_id = user_id
    if avatar is not None:
        a.avatar = avatar
    if proxy is not None:
        a.proxy = proxy or None          # 传空串=解绑代理
    if status is not None:
        a.status = status
    if last_proxy is not None:
        a.last_proxy = last_proxy or None
    if storage_state is not None:
        a.storage_state_enc = security.encrypt_json(storage_state)   # 加密落库
        a.logged_in = True
        a.last_login_at = int(time.time())
    if logged_in is not None:
        a.logged_in = logged_in
    if cooldown_until is not None:
        a.cooldown_until = cooldown_until
    db.commit()
    db.refresh(a)
    return a


def update_account(db: Session, a: Account, *, nickname=None, proxy=None, status=None,
                   cooldown_until=None, owner_id=None, weight_manual=UNSET) -> Account:
    """PATCH 单个字段。proxy/nickname 传空串表示清空;不传的字段不动。"""
    if nickname is not None:
        a.nickname = nickname or None
    if proxy is not None:
        a.proxy = proxy or None
    if status is not None:
        a.status = status
    if cooldown_until is not None:
        a.cooldown_until = cooldown_until
    if owner_id is not None:
        a.owner_id = owner_id
    if weight_manual is not UNSET:
        a.weight_manual = weight_manual     # None=恢复自动;int=人工覆盖
    db.commit()
    db.refresh(a)
    return a


def delete_account(db: Session, a: Account) -> None:
    """删号:连带把它的发布任务日志/任务清掉,避免留孤儿外键。"""
    tasks = list(db.scalars(select(Task).where(Task.account_id == a.id)))
    for t in tasks:
        db.execute(TaskLog.__table__.delete().where(TaskLog.task_id == t.id))
        db.delete(t)
    db.delete(a)
    db.commit()


def _mask_proxy(proxy: str | None) -> str | None:
    """脱敏:代理里带 user:pass 的,别把口令原样吐出去(前端展示用)。"""
    if not proxy:
        return None
    if "@" in proxy:
        scheme, _, rest = proxy.partition("://")
        host = rest.split("@", 1)[1] if "://" in proxy else proxy.split("@", 1)[1]
        return f"{scheme}://***@{host}" if "://" in proxy else f"***@{host}"
    return proxy


def compute_weight(db: Session, a: Account) -> dict:
    """养号健康分(0-100):综合登录态 / 发布成功率 / 风控冷却 / 号龄自动算;
    a.weight_manual 有值则以人工分覆盖。返回分数 + 因子明细(前端做 tooltip)。"""
    now = int(time.time())
    factors = []
    score = 60.0
    if a.logged_in:
        score += 18; factors.append({"k": "登录态", "v": "已登录", "d": 18})
    else:
        score -= 30; factors.append({"k": "登录态", "v": "未登录", "d": -30})
    if a.cooldown_until > now:
        score -= 22; factors.append({"k": "风控冷却", "v": "冷却中", "d": -22})
    total = db.scalar(select(func.count()).select_from(Task).where(
        Task.account_id == a.id, Task.status.in_(("success", "failed")))) or 0
    succ = db.scalar(select(func.count()).select_from(Task).where(
        Task.account_id == a.id, Task.status == "success")) or 0
    if total > 0:
        rate = succ / total
        delta = round((rate - 0.5) * 40)
        score += delta
        factors.append({"k": "发布成功率", "v": f"{succ}/{total}·{round(rate * 100)}%", "d": delta})
    else:
        factors.append({"k": "发布成功率", "v": "暂无发布", "d": 0})
    days = max(0.0, (now - (a.created_at or now)) / 86400)
    age_delta = round(min(days / 30, 1) * 10)
    score += age_delta
    factors.append({"k": "号龄", "v": f"{int(days)} 天", "d": age_delta})
    auto = int(max(0, min(100, round(score))))
    final = a.weight_manual if a.weight_manual is not None else auto
    level = "good" if final >= 80 else "ok" if final >= 60 else "warn" if final >= 40 else "bad"
    label = {"good": "优", "ok": "良", "warn": "中", "bad": "差"}[level]
    return {"score": final, "auto": auto, "manual": a.weight_manual,
            "level": level, "label": label, "factors": factors}


def account_public(a: Account, db: Session | None = None) -> dict:
    """给前端的账号视图(不含加密凭证;代理做脱敏)。传 db 时附带养号权重。"""
    now = int(time.time())
    d = {
        "id": a.id, "platform": a.platform, "account_id": a.account_id,
        "nickname": a.nickname, "user_id": a.user_id, "avatar": a.avatar,
        "status": a.status, "proxy": _mask_proxy(a.proxy), "proxy_bound": bool(a.proxy),
        "logged_in": a.logged_in, "cooling": max(0, a.cooldown_until - now),
        "last_used_at": a.last_used_at, "created_at": a.created_at,
        "weight_manual": a.weight_manual,
    }
    if db is not None:
        w = compute_weight(db, a)
        d.update({"weight": w["score"], "weight_auto": w["auto"],
                  "weight_level": w["level"], "weight_label": w["label"],
                  "weight_factors": w["factors"]})
    return d


def account_admin_view(db: Session, a: Account) -> dict:
    """管理员账号视图:多带归属用户名 + owner_id(仍不吐凭证)。"""
    owner = db.get(User, a.owner_id) if a.owner_id else None
    return {**account_public(a, db), "owner_id": a.owner_id,
            "owner_username": owner.username if owner else None}


def list_accounts_admin(db: Session, platform: str | None = None, q: str | None = None,
                        limit: int = 500) -> list[Account]:
    """总后台:跨用户列全部号,可按平台 + 关键词(账号名/昵称)过滤。"""
    stmt = select(Account)
    if platform:
        stmt = stmt.where(Account.platform == platform)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Account.account_id.like(like) | Account.nickname.like(like))
    return list(db.scalars(stmt.order_by(Account.id.desc()).limit(limit)))


# ---- tasks -------------------------------------------------------------------
def create_task(db: Session, account: Account, *, kind, title, body, topics, visibility,
                is_original, media, scheduled_at=0, options=None) -> Task:
    status = "scheduled" if scheduled_at and scheduled_at > int(time.time()) else "queued"
    t = Task(platform=account.platform, account_id=account.id, kind=kind, title=title, body=body,
             topics=topics or [], visibility=visibility, is_original=is_original, media=media or [],
             options=options or {}, status=status, scheduled_at=scheduled_at or 0)
    db.add(t)
    db.commit()
    db.refresh(t)
    add_log(db, t.id, "info", f"任务入库 status={status}")
    return t


def list_tasks(db: Session, status: str | None = None, limit: int = 100) -> list[Task]:
    stmt = select(Task)
    if status:
        stmt = stmt.where(Task.status == status)
    return list(db.scalars(stmt.order_by(Task.id.desc()).limit(limit)))


def get_task(db: Session, tid: int) -> Task | None:
    return db.get(Task, tid)


def task_public(db: Session, t: Task) -> dict:
    acc = db.get(Account, t.account_id)
    return {
        "id": t.id, "platform": t.platform,
        "account": (acc.nickname or acc.account_id) if acc else None,
        "kind": t.kind, "title": t.title, "status": t.status,
        "result_url": t.result_url, "item_id": t.item_id,
        "attempt": t.attempt, "error": t.error,
        "scheduled_at": t.scheduled_at, "created_at": t.created_at, "updated_at": t.updated_at,
    }


def add_log(db: Session, task_id: int, level: str, message: str) -> None:
    db.add(TaskLog(task_id=task_id, level=level, message=message))
    db.commit()


def _err_class(msg: str):
    """把失败 error 文本归一到几类可读根因(供失败归因 Top)。"""
    m = (msg or "").lower()
    if any(k in m for k in ("风控", "captcha", "verify", "验证码", "封", "frequ", "risk")):
        return "risk", "风控/验证"
    if any(k in m for k in ("cooldown", "冷却")):
        return "cool", "冷却限制"
    if any(k in m for k in ("timeout", "超时", "timed out")):
        return "timeout", "超时"
    if any(k in m for k in ("登录", "login", "storage_state", "未登录", "cookie", "登陆")):
        return "login", "登录失效"
    if any(k in m for k in ("代理", "proxy", "kdl", "快代理", "tunnel")):
        return "proxy", "代理不可用"
    if any(k in m for k in ("字段", "field", "参数", "校验", "invalid", "missing")):
        return "field", "字段校验"
    return "other", "其他"


def stats_overview(db: Session) -> dict:
    """工作台多维真实聚合:账号态/发布量/成功率/状态分布/趋势(7·30天)/24H时段/平台/账号榜/养号分布/失败归因/冷却/代理覆盖。
    一次拉全 tasks/accounts 两表在内存里算,只读、零改表。为将来"账号数据采集"的真实指标预留(前端占位)。"""
    import datetime
    now = int(time.time())
    day0 = int(datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    DAY = 86400

    trows = db.execute(select(Task.created_at, Task.status, Task.account_id,
                              Task.platform, Task.kind, Task.error)).all()
    accounts = list(db.scalars(select(Account)))

    STATUSES = ("queued", "scheduled", "running", "success", "failed", "canceled")
    by_status = {s: 0 for s in STATUSES}
    today = {"total": 0, "success": 0, "failed": 0}
    yesterday = {"total": 0, "success": 0, "failed": 0}
    week_total = prev_week_total = finished = success_all = 0
    trend_map: dict = {}                 # days_ago -> [count, success]
    hour_map = {h: [0, 0] for h in range(24)}
    plat_map: dict = {}                  # platform -> [total, success, failed]
    acct_task: dict = {}                 # account pk -> [total, success, failed]
    err_map: dict = {}                   # class -> [label, count]

    for created, status, acc_id, platform, kind, error in trows:
        created = created or 0
        by_status[status] = by_status.get(status, 0) + 1
        succ, fail = (status == "success"), (status == "failed")
        if succ or fail:
            finished += 1
        success_all += succ
        pp = plat_map.setdefault(platform, [0, 0, 0]); pp[0] += 1; pp[1] += succ; pp[2] += fail
        aa = acct_task.setdefault(acc_id, [0, 0, 0]); aa[0] += 1; aa[1] += succ; aa[2] += fail
        if fail:
            k, label = _err_class(error)
            e = err_map.setdefault(k, [label, 0]); e[1] += 1
        days_ago = max(0, (day0 - created + DAY - 1) // DAY)
        if created >= day0:
            today["total"] += 1; today["success"] += succ; today["failed"] += fail
        elif days_ago == 1:
            yesterday["total"] += 1; yesterday["success"] += succ; yesterday["failed"] += fail
        if days_ago <= 6:
            week_total += 1
        elif days_ago <= 13:
            prev_week_total += 1
        if days_ago <= 29:
            b = trend_map.setdefault(days_ago, [0, 0]); b[0] += 1; b[1] += succ
        if created:
            hm = hour_map[datetime.datetime.fromtimestamp(created).hour]; hm[0] += 1; hm[1] += succ

    def trend_arr(n):
        return [{"ts": day0 - da * DAY, "count": trend_map.get(da, [0, 0])[0],
                 "success": trend_map.get(da, [0, 0])[1]} for da in range(n - 1, -1, -1)]

    by_platform = [{"platform": k, "total": v[0], "success": v[1], "failed": v[2],
                    "success_rate": round(v[1] / (v[1] + v[2]) * 100) if (v[1] + v[2]) else None}
                   for k, v in sorted(plat_map.items(), key=lambda x: -x[1][0])]

    # 账号维度:养号分布 / 主力号榜 / 冷却明细 / 代理覆盖
    wlevels = {"good": 0, "ok": 0, "warn": 0, "bad": 0}
    wscore_sum = online = cool = bound = 0
    by_account, cooling_accounts = [], []
    for a in accounts:
        w = compute_weight(db, a)
        wlevels[w["level"]] = wlevels.get(w["level"], 0) + 1
        wscore_sum += w["score"]
        is_cool = bool(a.logged_in and a.cooldown_until > now)
        if is_cool:
            cool += 1
        elif a.logged_in:
            online += 1
        if a.proxy:
            bound += 1
        t = acct_task.get(a.id, [0, 0, 0]); fin = t[1] + t[2]
        by_account.append({"account_id": a.id, "name": a.nickname or a.account_id, "platform": a.platform,
                           "total": t[0], "success": t[1], "failed": t[2],
                           "success_rate": round(t[1] / fin * 100) if fin else None,
                           "weight": w["score"], "weight_level": w["level"],
                           "cooling": is_cool, "logged_in": bool(a.logged_in), "proxy_bound": bool(a.proxy)})
        if is_cool:
            cooling_accounts.append({"name": a.nickname or a.account_id, "platform": a.platform,
                                     "cooldown_until": a.cooldown_until, "remain_sec": max(0, a.cooldown_until - now)})

    total_acc = len(accounts)
    off = total_acc - online - cool
    naked = total_acc - bound
    by_account.sort(key=lambda x: (-x["total"], -(x["weight"] or 0)))
    cooling_accounts.sort(key=lambda x: x["remain_sec"])
    top_errors = [{"key": k, "label": v[0], "count": v[1]}
                  for k, v in sorted(err_map.items(), key=lambda x: -x[1][1])][:5]

    return {
        "accounts": {"total": total_acc, "online": online, "cooling": cool, "offline": off,
                     "avg_weight": round(wscore_sum / total_acc) if total_acc else 0, "naked": naked},
        "today": today, "yesterday": yesterday,
        "week_total": week_total, "prev_week_total": prev_week_total,
        "finished": finished, "success_all": success_all,
        "by_status": by_status,
        "trend": trend_arr(7), "trend30": trend_arr(30),
        "by_hour": [{"hour": h, "count": hour_map[h][0], "success": hour_map[h][1]} for h in range(24)],
        "by_platform": by_platform,
        "by_account": by_account[:12],
        "weight_distribution": {**wlevels, "avg_score": round(wscore_sum / total_acc) if total_acc else 0},
        "top_errors": top_errors,
        "cooling_accounts": cooling_accounts,
        "proxy_coverage": {"total": total_acc, "bound": bound, "naked": naked,
                           "rate": round(bound / total_acc * 100) if total_acc else 0},
        "server_now": now,
    }


# ---- users -------------------------------------------------------------------
def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def get_user_by_phone(db: Session, phone: str) -> User | None:
    return db.scalar(select(User).where(User.phone == phone))


def get_user_by_login(db: Session, ident: str) -> User | None:
    """登录标识:先按手机号找,再按用户名找(手机号优先,兼容老的用户名登录)。"""
    ident = (ident or "").strip()
    if not ident:
        return None
    return get_user_by_phone(db, ident) or get_user_by_username(db, ident)


def create_user(db: Session, username: str, password: str, *, email=None, phone=None, nickname=None) -> User:
    free = get_plan(db, "free")
    first = db.scalar(select(func.count()).select_from(User)) == 0   # 第一个注册的自动当管理员
    u = User(
        username=username, email=email or None, phone=phone or None,
        nickname=nickname or username, password_hash=auth.hash_password(password),
        account_quota=free.account_quota if free else 1, is_admin=first,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def update_user_profile(db: Session, user: User, *, nickname=None, email=None, phone=None, avatar=None) -> User:
    if nickname is not None:
        user.nickname = nickname
    if email is not None:
        user.email = email or None
    if phone is not None:
        user.phone = phone or None
    if avatar is not None:
        user.avatar = avatar
    db.commit()
    db.refresh(user)
    return user


def change_password(db: Session, user: User, new_password: str) -> None:
    user.password_hash = auth.hash_password(new_password)
    db.commit()


def touch_login(db: Session, user: User) -> None:
    user.last_login_at = int(time.time())
    db.commit()


def user_public(u: User) -> dict:
    """给前端的用户视图,不含密码哈希。"""
    return {
        "id": u.id, "username": u.username, "email": u.email, "phone": u.phone,
        "nickname": u.nickname, "avatar": u.avatar, "is_admin": u.is_admin,
        "plan_code": u.plan_code, "plan_expires_at": u.plan_expires_at,
        "account_quota": u.account_quota, "created_at": u.created_at,
        "last_login_at": u.last_login_at,
    }


# ---- plans -------------------------------------------------------------------
def sync_plans(db: Session) -> None:
    """把 DEFAULT_PLANS upsert 进库(启动时调一次)。按 code 认,只补/更基础字段。"""
    for p in DEFAULT_PLANS:
        row = get_plan(db, p["code"])
        if row is None:
            db.add(Plan(**p))
        else:
            row.name = p["name"]
            row.price_cents = p["price_cents"]
            row.duration_days = p["duration_days"]
            row.account_quota = p["account_quota"]
            row.description = p["description"]
            row.sort = p["sort"]
    db.commit()


def get_plan(db: Session, code: str) -> Plan | None:
    return db.scalar(select(Plan).where(Plan.code == code))


def list_plans(db: Session, only_active: bool = True) -> list[Plan]:
    stmt = select(Plan)
    if only_active:
        stmt = stmt.where(Plan.active == True)  # noqa: E712
    return list(db.scalars(stmt.order_by(Plan.sort)))


def plan_public(p: Plan) -> dict:
    return {
        "code": p.code, "name": p.name, "price_cents": p.price_cents,
        "price_yuan": round(p.price_cents / 100, 2), "duration_days": p.duration_days,
        "account_quota": p.account_quota, "description": p.description, "sort": p.sort,
    }


# ---- membership / orders -----------------------------------------------------
def count_user_accounts(db: Session, user_id: int) -> int:
    return db.scalar(select(func.count()).select_from(Account).where(Account.owner_id == user_id)) or 0


def membership_status(db: Session, user: User) -> dict:
    """算用户当前有效会员:过期就按 free 的号数额度。"""
    now = int(time.time())
    active = user.plan_code != "free" and user.plan_expires_at > now
    code = user.plan_code if active else "free"
    plan = get_plan(db, code)
    quota = plan.account_quota if plan else user.account_quota
    used = count_user_accounts(db, user.id)
    return {
        "plan_code": code, "plan_name": plan.name if plan else code,
        "active": active, "expires_at": user.plan_expires_at if active else 0,
        "quota": quota, "used": used, "remaining": max(0, quota - used),
    }


def create_order(db: Session, user: User, plan: Plan, pay_method: str = "mock") -> Order:
    otn = f"MC{int(time.time())}{secrets.token_hex(3).upper()}"
    o = Order(out_trade_no=otn, user_id=user.id, plan_code=plan.code, plan_name=plan.name,
              amount_cents=plan.price_cents, status="pending", pay_method=pay_method)
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


def get_order(db: Session, out_trade_no: str) -> Order | None:
    return db.scalar(select(Order).where(Order.out_trade_no == out_trade_no))


def list_user_orders(db: Session, user_id: int, limit: int = 50) -> list[Order]:
    return list(db.scalars(
        select(Order).where(Order.user_id == user_id).order_by(Order.id.desc()).limit(limit)))


def mark_order_paid(db: Session, order: Order) -> Order:
    """付款成功回调:置 paid 并激活/续期会员。同套餐没过期就叠加时长。"""
    if order.status == "paid":
        return order
    now = int(time.time())
    order.status = "paid"
    order.paid_at = now
    user = db.get(User, order.user_id)
    plan = get_plan(db, order.plan_code)
    days = plan.duration_days if plan else 30
    base = user.plan_expires_at if (user.plan_code == order.plan_code and user.plan_expires_at > now) else now
    user.plan_code = order.plan_code
    user.plan_expires_at = base + days * 86400
    user.account_quota = plan.account_quota if plan else user.account_quota
    db.commit()
    db.refresh(order)
    return order


def order_public(o: Order) -> dict:
    return {
        "out_trade_no": o.out_trade_no, "plan_code": o.plan_code, "plan_name": o.plan_name,
        "amount_cents": o.amount_cents, "amount_yuan": round(o.amount_cents / 100, 2),
        "status": o.status, "pay_method": o.pay_method,
        "created_at": o.created_at, "paid_at": o.paid_at,
    }


# ---- admin(总后台)----------------------------------------------------------
def admin_stats(db: Session) -> dict:
    now = int(time.time())
    return {
        "total_users": db.scalar(select(func.count()).select_from(User)) or 0,
        "active_members": db.scalar(select(func.count()).select_from(User).where(
            User.plan_code != "free", User.plan_expires_at > now)) or 0,
        "paid_orders": db.scalar(select(func.count()).select_from(Order).where(Order.status == "paid")) or 0,
        "revenue_yuan": round((db.scalar(select(func.coalesce(func.sum(Order.amount_cents), 0)).where(
            Order.status == "paid")) or 0) / 100, 2),
        "total_accounts": db.scalar(select(func.count()).select_from(Account)) or 0,
    }


def list_users(db: Session, q: str | None = None, limit: int = 200) -> list[User]:
    stmt = select(User)
    if q:
        stmt = stmt.where(User.username.like(f"%{q}%"))
    return list(db.scalars(stmt.order_by(User.id).limit(limit)))


def get_user(db: Session, uid: int) -> User | None:
    return db.get(User, uid)


def user_admin_view(db: Session, u: User) -> dict:
    now = int(time.time())
    return {
        **user_public(u), "status": u.status,
        "membership_active": u.plan_code != "free" and u.plan_expires_at > now,
        "account_count": count_user_accounts(db, u.id),
    }


def admin_update_user(db: Session, user: User, *, status=None, is_admin=None, account_quota=None) -> User:
    if status is not None:
        user.status = status
    if is_admin is not None:
        user.is_admin = is_admin
    if account_quota is not None:
        user.account_quota = account_quota
    db.commit()
    db.refresh(user)
    return user


def admin_grant_plan(db: Session, user: User, plan: Plan, days: int | None = None) -> User:
    """后台手动开/续会员(不走订单)。"""
    now = int(time.time())
    d = days if days is not None else plan.duration_days
    base = user.plan_expires_at if (user.plan_code == plan.code and user.plan_expires_at > now) else now
    user.plan_code = plan.code
    user.plan_expires_at = base + d * 86400
    user.account_quota = plan.account_quota
    db.commit()
    db.refresh(user)
    return user


def list_all_orders(db: Session, status: str | None = None, limit: int = 200) -> list[Order]:
    stmt = select(Order)
    if status:
        stmt = stmt.where(Order.status == status)
    return list(db.scalars(stmt.order_by(Order.id.desc()).limit(limit)))


def order_admin_view(db: Session, o: Order) -> dict:
    u = db.get(User, o.user_id)
    return {**order_public(o), "user_id": o.user_id, "username": u.username if u else None}


def update_plan(db: Session, plan: Plan, **fields) -> Plan:
    for k in ("name", "description", "price_cents", "duration_days", "account_quota", "active", "sort"):
        v = fields.get(k)
        if v is not None:
            setattr(plan, k, v)
    db.commit()
    db.refresh(plan)
    return plan
