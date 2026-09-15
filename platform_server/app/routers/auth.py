import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import audit
from ..config import get_settings
from ..db import get_session
from ..deps import get_current_user, get_membership, get_store
from ..models import AuthSession, Plan, SocialAccount, Subscription, User
from ..schemas import (
    PHONE_RE,
    LogoutReq,
    MeResp,
    MembershipResp,
    PasswordLoginReq,
    RefreshReq,
    RegisterReq,
    SmsLoginReq,
    SmsSendReq,
    TokenResp,
    UserResp,
)
from ..security import (
    hash_password,
    hash_refresh,
    make_access_token,
    new_refresh_token,
    verify_password,
)
from ..sms import SmsError, _as_int, send_code, verify_code
from ..utils import as_utc, now_utc

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["auth"])

# 登录时若用户不存在,也拿它跑一次 argon2,抹平"存在与否"的耗时差(防枚举)
_DUMMY_HASH = hash_password("timing-safe-dummy-password")


def _client(request: Request):
    return request.headers.get("user-agent"), (request.client.host if request.client else None)


# ---- 密码登录失败限流(按账号 + IP) ----
async def _login_blocked(store, account: str, ip: str | None) -> bool:
    if await _as_int(store, f"login:fail:acc:{account}") >= settings.login_fail_limit:
        return True
    if ip and await _as_int(store, f"login:fail:ip:{ip}") >= settings.login_fail_limit:
        return True
    return False


async def _login_fail(store, account: str, ip: str | None):
    await store.incr_ttl(f"login:fail:acc:{account}", settings.login_fail_window)
    if ip:
        await store.incr_ttl(f"login:fail:ip:{ip}", settings.login_fail_window)


async def _login_ok(store, account: str, ip: str | None):
    await store.delete(f"login:fail:acc:{account}")
    if ip:
        await store.delete(f"login:fail:ip:{ip}")


async def _issue_tokens(db: AsyncSession, user: User, max_sessions: int,
                        device: str | None, ip: str | None) -> TokenResp:
    # 锁住该用户行,把"统计会话→踢超额→插新会话"串行化(PG 生效;sqlite 本就串行写)
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())

    now = now_utc()
    rows = (
        await db.execute(
            select(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.revoked.is_(False))
            .order_by(AuthSession.created_at.asc())
        )
    ).scalars().all()
    active = [s for s in rows if as_utc(s.expires_at) > now]
    while len(active) >= max_sessions:  # 同时在线数超了就踢最早的
        active.pop(0).revoked = True

    jti = uuid.uuid4().hex
    refresh = new_refresh_token()
    db.add(AuthSession(
        id=jti, user_id=user.id, refresh_hash=hash_refresh(refresh),
        device=(device or "")[:255], ip=ip,
        expires_at=now + timedelta(days=settings.refresh_ttl_days),
    ))
    access = make_access_token(user.id, jti)
    await db.commit()
    return TokenResp(access_token=access, refresh_token=refresh,
                     expires_in=settings.access_ttl_min * 60)


async def _grant_trial(db: AsyncSession, user: User):
    """注册送体验会员,好让配额那条链路能立刻演示"""
    plan = (await db.execute(select(Plan).where(Plan.code == "trial"))).scalar_one_or_none()
    if not plan:
        return
    db.add(Subscription(
        user_id=user.id, plan_id=plan.id, status="active",
        expires_at=now_utc() + timedelta(days=plan.duration_days),
    ))
    await db.flush()  # 显式落盘,别依赖后续查询的隐式 autoflush


async def _max_sessions(db: AsyncSession, user: User) -> int:
    _sub, plan = await get_membership(user, db)
    return plan.max_sessions if plan else 1


# ---------------- 发验证码 ----------------
@router.post("/sms/send")
async def sms_send(body: SmsSendReq, request: Request, store=Depends(get_store)):
    _ua, ip = _client(request)
    try:
        code = await send_code(body.phone, store, request.app.state.sms, ip=ip)
    except SmsError as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e))
    resp = {"ok": True, "message": "验证码已发送"}
    if settings.dev_echo_enabled:
        resp["dev_code"] = code  # 仅"非生产 + console 短信"时回显,方便自测
    return resp


# ---------------- 注册(手机号+验证码+密码) ----------------
@router.post("/register", response_model=TokenResp)
async def register(body: RegisterReq, request: Request,
                   db: AsyncSession = Depends(get_session), store=Depends(get_store)):
    if not await verify_code(body.phone, body.code, store):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码错误或已过期")
    if (await db.execute(select(User).where(User.phone == body.phone))).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "该手机号已注册")
    if body.username and (await db.execute(
        select(User).where(User.username == body.username))).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名已被占用")

    _ua, ip = _client(request)
    try:
        user = User(phone=body.phone, username=body.username,
                    password_hash=hash_password(body.password))
        db.add(user)
        await db.flush()
        await _grant_trial(db, user)
        tokens = await _issue_tokens(db, user, await _max_sessions(db, user),
                                     request.headers.get("user-agent"), ip)
    except IntegrityError:
        # 唯一约束兜底并发注册竞态,回 409 而不是 500
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "手机号或用户名已被占用")
    await audit("register", user_id=user.id, ip=ip, detail={"phone": body.phone})
    return tokens


# ---------------- 密码登录 ----------------
@router.post("/login/password", response_model=TokenResp)
async def login_password(body: PasswordLoginReq, request: Request,
                         db: AsyncSession = Depends(get_session), store=Depends(get_store)):
    _ua, ip = _client(request)
    if await _login_blocked(store, body.account, ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "尝试过多,请稍后再试")

    # 按语义分开查:是手机号走 phone,否则走 username(不用 or_,避免命中两行 500)
    if PHONE_RE.match(body.account):
        stmt = select(User).where(User.phone == body.account)
    else:
        stmt = select(User).where(User.username == body.account)
    user = (await db.execute(stmt)).scalar_one_or_none()

    if not user or not verify_password(user.password_hash, body.password):
        if user is None:
            verify_password(_DUMMY_HASH, body.password)  # 抹平时序,别泄露账号是否存在
        await _login_fail(store, body.account, ip)
        await audit("login_fail", user_id=(user.id if user else None), ip=ip,
                    detail={"account": body.account})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号或密码错误")
    if user.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已封禁")

    await _login_ok(store, body.account, ip)
    tokens = await _issue_tokens(db, user, await _max_sessions(db, user),
                                 request.headers.get("user-agent"), ip)
    await audit("login_password", user_id=user.id, ip=ip)
    return tokens


# ---------------- 验证码登录 ----------------
@router.post("/login/sms", response_model=TokenResp)
async def login_sms(body: SmsLoginReq, request: Request,
                    db: AsyncSession = Depends(get_session), store=Depends(get_store)):
    if not await verify_code(body.phone, body.code, store):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码错误或已过期")
    user = (await db.execute(select(User).where(User.phone == body.phone))).scalar_one_or_none()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "该手机号还没注册")
    if user.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已封禁")

    _ua, ip = _client(request)
    tokens = await _issue_tokens(db, user, await _max_sessions(db, user),
                                 request.headers.get("user-agent"), ip)
    await audit("login_sms", user_id=user.id, ip=ip)
    return tokens


# ---------------- 刷新(原子轮换 refresh) ----------------
@router.post("/refresh", response_model=TokenResp)
async def refresh(body: RefreshReq, request: Request,
                  db: AsyncSession = Depends(get_session)):
    _ua, ip = _client(request)
    h = hash_refresh(body.refresh_token)
    sess = (await db.execute(
        select(AuthSession).where(AuthSession.refresh_hash == h))).scalar_one_or_none()
    if not sess:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh 无效")

    # 复用一个已吊销的 refresh = 疑似被盗重放:吊销该用户全部会话并告警
    if sess.revoked:
        await db.execute(update(AuthSession).where(
            AuthSession.user_id == sess.user_id, AuthSession.revoked.is_(False)
        ).values(revoked=True).execution_options(synchronize_session=False))
        await db.commit()
        await audit("refresh_replay", user_id=sess.user_id, ip=ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh 已失效(疑似重放,已下线全部会话)")
    if as_utc(sess.expires_at) < now_utc():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh 已过期")

    # 原子占用:只有把 revoked False→True 成功翻转的那个请求能继续签发(挡并发重放)
    res = await db.execute(update(AuthSession).where(
        AuthSession.id == sess.id, AuthSession.revoked.is_(False)
    ).values(revoked=True).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh 已被使用")

    user = await db.get(User, sess.user_id)
    if not user or user.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号异常")
    return await _issue_tokens(db, user, await _max_sessions(db, user),
                               request.headers.get("user-agent"), ip)


# ---------------- 退出(吊销当前会话) ----------------
@router.post("/logout")
async def logout(body: LogoutReq, user: User = Depends(get_current_user),
                 db: AsyncSession = Depends(get_session)):
    h = hash_refresh(body.refresh_token)
    sess = (await db.execute(select(AuthSession).where(
        AuthSession.refresh_hash == h, AuthSession.user_id == user.id))).scalar_one_or_none()
    if sess:
        sess.revoked = True
        await db.commit()
    return {"ok": True}


# ---------------- 当前用户 + 会员 ----------------
@router.get("/me", response_model=MeResp)
async def me(user: User = Depends(get_current_user),
             db: AsyncSession = Depends(get_session)):
    sub, plan = await get_membership(user, db)
    used = (await db.execute(
        select(func.count()).select_from(SocialAccount).where(
            SocialAccount.user_id == user.id))).scalar_one()
    m = MembershipResp(active=bool(sub), used_accounts=used)
    if plan:
        m.plan_code, m.plan_name, m.max_accounts = plan.code, plan.name, plan.max_accounts
    if sub:
        m.expires_at = as_utc(sub.expires_at)
    return MeResp(user=UserResp.model_validate(user), membership=m)
