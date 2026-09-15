# -*- coding: utf-8 -*-
"""扫码登录作业:后端 API 派活 → worker 起 CloakBrowser 弹二维码 → 手机扫 → 登录态写回 DB。

这条接通了"第2步":真实扫码登录。要点:
  - 进度走 Redis(mc:login:<pk>),API 只读它、不碰浏览器,所以 API 进程不依赖 cloakbrowser。
  - 登录成功后 storage_state 加密写回后端 DB(crud),DB 成为账号登录态的单一事实源(顺带
    统一了采集层各自 AccountStore 的老问题)。
  - 真扫码那步(CloakBrowser 弹窗 + 手机扫)必须真人扫,机器测不了;设 MATRIXCAT_LOGIN_DRYRUN=1
    可跑一遍模拟登录(写入带 _dryrun 标记的占位登录态),用来自测"派活→轮询二维码→写回→刷新"整条管线。

worker 起法(项目根,带 cloakbrowser 的 conda313):
    python -m dramatiq backend.jobs.tasks     # tasks.py 里 import 了本模块,actor 一并注册
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time

import dramatiq
import redis as _redis

from . import broker  # noqa: F401  import 即设好 broker
from .. import crud, security
from ..config import ROOT, PLATFORMS
from ..db import SessionLocal

# 平台 → 中文名(扫码提示用哪个 App)。取自平台注册表,新平台自动带上。
_LABEL = {p["name"]: p["label"] for p in PLATFORMS}


def _app_name(platform: str) -> str:
    return _LABEL.get(platform, platform)

LOGIN_DRYRUN = os.getenv("MATRIXCAT_LOGIN_DRYRUN", "0") != "0"
_TTL = 180                                   # 进度键活 3 分钟,够一次扫码
_r = _redis.from_url(os.getenv("MATRIXCAT_REDIS_URL", "redis://127.0.0.1:6379/0"), protocol=2)


# ---- 进度(Redis)-------------------------------------------------------------
def _key(pk: int) -> str:
    return f"mc:login:{pk}"


def set_progress(pk: int, **fields) -> None:
    fields["ts"] = int(time.time())
    _r.set(_key(pk), json.dumps(fields, ensure_ascii=False), ex=_TTL)


def get_progress(pk: int) -> dict:
    raw = _r.get(_key(pk))
    if not raw:
        return {"status": "idle"}
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return {"status": "idle"}


def enqueue_login(pk: int) -> bool:
    """把登录作业推进队列。broker 不可用返回 False(API 层据此回明确错误)。"""
    try:
        set_progress(pk, status="pending")
        login_task.send(pk)
        return True
    except Exception:  # noqa: BLE001
        return False


# ---- 作业本体 ----------------------------------------------------------------
@dramatiq.actor(max_retries=0, time_limit=600000)
def login_task(account_pk: int):
    db = SessionLocal()
    try:
        acc = crud.get_account_by_pk(db, account_pk)
        if not acc:
            set_progress(account_pk, status="failed", error="账号不存在")
            return
        if acc.status == "disabled":
            set_progress(account_pk, status="failed", error="账号已停用,先启用再登录")
            return
        proxy = acc.proxy or None
        platform, account_id = acc.platform, acc.account_id

        if LOGIN_DRYRUN:
            _dryrun(db, account_pk, platform, account_id, proxy)
            return

        set_progress(account_pk, status="pending", message="正在起浏览器…")
        _real_login_subprocess(db, account_pk, platform, account_id, proxy)
    except Exception as e:  # noqa: BLE001
        set_progress(account_pk, status="failed", error=f"{type(e).__name__}: {e}")
    finally:
        db.close()


def _dryrun(db, pk, platform, account_id, proxy):
    """模拟:推个占位二维码 → 等 1s → 写占位登录态到 DB → success。用来自测管线,不碰真浏览器。"""
    set_progress(pk, status="qr_ready", qr_image=_PLACEHOLDER_QR,
                 message="[DRYRUN] 模拟二维码,真机时这里是小红书登录码")
    time.sleep(1)
    crud.upsert_account(db, platform, account_id,
                        storage_state={"cookies": [], "_dryrun": True},
                        last_proxy=proxy, nickname=f"dryrun_{account_id}")
    set_progress(pk, status="success", nickname=f"dryrun_{account_id}",
                 message="[DRYRUN] 模拟登录成功,登录态已写库")


def _read_tail(path, n=800):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()[-n:]
    except OSError:
        return None


def _cleanup(*paths):
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


def _real_login_subprocess(db, pk, platform, account_id, proxy):
    """spawn 采集层 run.py login 独立进程跑扫码;worker 只监视二维码 + 子进程退出后回写 DB。

    关键:浏览器在子进程的【主线程】里 asyncio.run 起(和真机跑通的 CLI 同一条路),
    避开"在 dramatiq worker 线程里新建事件循环起 CloakBrowser"在 Windows 上
    [Errno 9] Bad file descriptor 的坑(每任务 asyncio.run 建/销事件循环 + Playwright
    持久管道子进程 transport 的 fd 生命周期,在无常驻循环的 worker 线程上收尸即野句柄)。
    """
    import importlib

    plat_dir = ROOT / "collectors" / f"{platform}_publisher"
    run_py = plat_dir / "run.py"
    if not run_py.exists():
        set_progress(pk, status="failed", error=f"采集层入口不存在: {run_py}")
        return

    # 只 import 采集层 config 拿 QR_DIR / 超时(不起浏览器,安全)。子进程继承 env,两边 QR_DIR 一致。
    if str(plat_dir) not in sys.path:
        sys.path.insert(0, str(plat_dir))
    plat_config = importlib.import_module(f"{platform}.config")
    qr_path = os.path.join(plat_config.QR_DIR, f"qr_{account_id}.png")
    _cleanup(qr_path)                        # 清掉上一轮旧码,别把旧图当新码推

    result_path = os.path.join(tempfile.gettempdir(), f"mc_login_{pk}_{int(time.time())}.json")
    log_path = result_path + ".log"

    cmd = [sys.executable, str(run_py), "login", account_id,
           "--headless", "--save-qr", "--emit-state", result_path]
    # 焊死代理:登录一律走配置的快代理(号绑了用绑定的,否则 KDL 北京出口),取不到就 fail,绝不用本机 IP。
    child_env = dict(os.environ)
    child_env["CRAWLER_ALLOW_DIRECT_FALLBACK"] = "0"     # 禁止静默直连回退
    # 代理环境变量按平台走(各平台 config 读各自的 <PLATFORM>_PROXY)。
    proxy_env = f"{platform.upper()}_PROXY"
    if proxy:
        child_env[proxy_env] = proxy                     # 号绑了固定代理:用它(粘号),走 env 不走 argv
    else:
        child_env.pop(proxy_env, None)
        child_env["CRAWLER_PROXY_MODE"] = "beijing"      # 没绑号代理:强制走配置的快代理 KDL 北京出口

    # 硬超时略大于子进程内部登录超时:让子进程先自己超时并写干净结果,worker 只在它卡死时兜底 kill。
    hard_timeout = int(getattr(plat_config, "LOGIN_TIMEOUT", 180)) + 25

    with open(log_path, "w", encoding="utf-8", errors="replace") as logf:
        proc = subprocess.Popen(cmd, cwd=str(plat_dir), env=child_env,
                                stdout=logf, stderr=subprocess.STDOUT)
        pushed = False
        deadline = time.time() + hard_timeout
        while proc.poll() is None:
            if not pushed and os.path.exists(qr_path):
                try:
                    b = base64.b64encode(open(qr_path, "rb").read()).decode()
                    set_progress(pk, status="qr_ready", qr_image=f"data:image/png;base64,{b}",
                                 message=f"用{_app_name(platform)} App 扫码登录")
                    pushed = True
                except Exception:  # noqa: BLE001
                    pass
            if time.time() > deadline:
                proc.kill()
                set_progress(pk, status="failed", error="登录超时(worker 侧硬超时,子进程已结束)")
                _cleanup(result_path, log_path)
                return
            time.sleep(0.5)

    # 子进程退出:优先读结果 JSON,拿不到退回 exit code + 日志尾巴。
    result = None
    if os.path.exists(result_path):
        try:
            with open(result_path, "r", encoding="utf-8") as f:
                result = json.load(f)
        except Exception:  # noqa: BLE001
            result = None
    tail = _read_tail(log_path)
    _cleanup(result_path, log_path)

    if not result or not result.get("success"):
        err = (result or {}).get("error") or f"登录子进程异常退出(code={proc.returncode})"
        set_progress(pk, status="failed", error=str(err)[:300], detail=tail)
        return
    storage_state = result.get("storage_state")
    if not storage_state:
        set_progress(pk, status="failed", error="登录成功但子进程未回传 storage_state")
        return
    # 登录态加密写回后端 DB(单一事实源);nickname/user_id/头像 一并落(头像之前漏传,账号矩阵才只显示默认名)。
    crud.upsert_account(db, platform, account_id, storage_state=storage_state,
                        nickname=result.get("nickname"), user_id=result.get("user_id"),
                        avatar=result.get("avatar"), last_proxy=proxy)
    set_progress(pk, status="success", nickname=result.get("nickname"),
                 avatar=result.get("avatar"), message="登录成功,登录态已写库")


# 1x1 透明 PNG(dryrun 占位,别让前端 <img> 裂图)
_PLACEHOLDER_QR = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE"
    "QVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
