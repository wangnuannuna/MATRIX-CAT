# -*- coding: utf-8 -*-
"""把一条发布任务真正发出去 —— 桥到采集层(协议直连 + CloakBrowser)。

默认 **DRY_RUN**(模拟成功),这样队列/worker/回写整条管线能安全验证、不会真往平台发。
真发布:设 MATRIXCAT_DRYRUN=0。前提是账号有真实登录态(storage_state),即第4步把真号
迁进 DB 之后。
"""
import json
import os
import subprocess
import sys
import time

from ..config import ROOT
from .. import security

DRY_RUN = os.getenv("MATRIXCAT_DRYRUN", "1") != "0"


def dispatch_publish(db, task, account) -> dict:
    """返回 {ok, item_id?, url?, error?}。"""
    if DRY_RUN:
        time.sleep(0.3)                                  # 模拟发布耗时
        return {"ok": True, "item_id": f"sim_{task.id}",
                "url": f"https://www.xiaohongshu.com/note/sim_{task.id}"}

    storage_state = security.decrypt_json(account.storage_state_enc)
    if not storage_state:
        return {"ok": False, "error": "账号无登录态(storage_state);先登录或迁入真号"}
    try:
        return _real_publish(account, task, storage_state)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _read_tail(path, n=400):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()[-n:]
    except OSError:
        return None


def _cleanup(*paths):
    for p in paths:
        if not p:
            continue
        try:
            os.remove(p)
        except OSError:
            pass


# 各平台 run.py 认得的"平台专属"参数:task.options 里的 key → CLI flag。
# 只给认得的平台透传(别给 xhs/快手传 --tid 这种它们不认的参数,argparse 会报错)。
_PLATFORM_OPTS = {
    "bilibili": {"tid": "--tid", "source": "--source"},
}


def _build_cmd_env(account, task, run_py: str, result_path: str, ss_path: str = None):
    """拼采集层 publish 子进程的 cmd + env(抽出来便于单测)。

    **登录态单一事实源**:后端 DB 的 storage_state 写成临时 JSON,用 --storage-state 传进去,
    子进程发布前灌进采集层账号库 —— 不再依赖采集层本地 accounts.json 与 DB 同步(修"账号不存在,请先 login")。
    **代理焊死**:号绑了固定代理就用绑定的(粘号,走 <PLATFORM>_PROXY env),否则强制走配置的 KDL 北京出口;
    禁直连回退(CRAWLER_ALLOW_DIRECT_FALLBACK=0),取不到代理就 fail,绝不用本机 IP 发帖。
    平台专属参数(如 B站 tid/source)按 _PLATFORM_OPTS 从 task.options 透传,其它平台忽略。
    """
    plat = account.platform
    cmd = [sys.executable, run_py, "publish", account.account_id, "--headless",
           "--title", task.title or "", "--desc", task.body or "",
           "--visibility", task.visibility or "public", "--emit-result", result_path]
    if ss_path:
        cmd += ["--storage-state", ss_path]                # DB 登录态灌进采集层账号库
        if account.nickname:
            cmd += ["--acct-nickname", account.nickname]
    if task.is_original:
        cmd.append("--original")
    if task.topics:
        cmd += ["--topics", *[str(t) for t in task.topics]]
    if task.kind == "video":
        cmd += ["--video", task.media[0]]
        if len(task.media) > 1 and task.media[1]:
            cmd += ["--cover", task.media[1]]
    else:
        cmd += ["--images", *task.media]
    # 平台专属参数透传(B站 --tid/--source 等)
    for key, flag in _PLATFORM_OPTS.get(plat, {}).items():
        val = (getattr(task, "options", None) or {}).get(key)
        if val not in (None, ""):
            cmd += [flag, str(val)]

    child_env = dict(os.environ)
    child_env["CRAWLER_ALLOW_DIRECT_FALLBACK"] = "0"       # 禁止静默直连:取不到代理就 fail
    proxy_env = f"{plat.upper()}_PROXY"                     # 各平台 config 读各自的 <PLATFORM>_PROXY
    bound = getattr(account, "proxy", None) or getattr(account, "last_proxy", None)
    if bound:
        child_env[proxy_env] = bound                       # 号绑了固定代理:粘号
    else:
        child_env.pop(proxy_env, None)
        child_env["CRAWLER_PROXY_MODE"] = "beijing"        # 否则强制走配置的快代理 KDL 北京出口
    return cmd, child_env


def _real_publish(account, task, storage_state) -> dict:
    """spawn 采集层 run.py publish 独立进程发布(浏览器/curl 在子进程主线程里跑,避开 worker
    线程 asyncio.run 起浏览器在 Windows 上 [Errno 9] Bad file descriptor 的坑,与登录同法)。

    **代理焊死**:号绑了固定代理就用绑定的(粘号),否则强制走配置的快代理 KDL 北京出口;
    禁直连回退(CRAWLER_ALLOW_DIRECT_FALLBACK=0),取不到代理就 fail,绝不用本机 IP 发帖。
    注:采集层 publish 仍按 account_id 从它自己的 accounts.json 读登录态(worker 登录时已同步写入)。
    """
    import tempfile

    plat = account.platform
    plat_dir = ROOT / "collectors" / f"{plat}_publisher"
    run_py = plat_dir / "run.py"
    if not run_py.exists():
        return {"ok": False, "error": f"采集层入口不存在: {run_py}"}

    result_path = os.path.join(tempfile.gettempdir(), f"mc_pub_{task.id}_{int(time.time())}.json")
    log_path = result_path + ".log"

    # DB 登录态(单一事实源)写临时 JSON,--storage-state 传给子进程灌进采集层账号库
    ss_path = os.path.join(tempfile.gettempdir(), f"mc_ss_{task.id}_{int(time.time())}.json")
    try:
        with open(ss_path, "w", encoding="utf-8") as f:
            json.dump(storage_state, f, ensure_ascii=False)
    except OSError:
        ss_path = None

    cmd, child_env = _build_cmd_env(account, task, str(run_py), result_path, ss_path)

    timeout = int(os.getenv("MATRIXCAT_PUBLISH_TIMEOUT", "600"))
    code = None
    with open(log_path, "w", encoding="utf-8", errors="replace") as logf:
        try:
            proc = subprocess.run(cmd, cwd=str(plat_dir), env=child_env,
                                  stdout=logf, stderr=subprocess.STDOUT, timeout=timeout)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            _cleanup(log_path, ss_path)
            return {"ok": False, "error": f"发布子进程超时(>{timeout}s)"}

    result = None
    if os.path.exists(result_path):
        try:
            with open(result_path, "r", encoding="utf-8") as f:
                result = json.load(f)
        except Exception:  # noqa: BLE001
            result = None
    tail = _read_tail(log_path)
    _cleanup(result_path, log_path, ss_path)

    if not result or not result.get("ok"):
        err = (result or {}).get("error") or f"发布子进程异常退出(code={code})"
        if not result and tail:
            err = f"{err} | {tail[-200:]}"
        return {"ok": False, "error": str(err)[:400]}
    # 各平台作品 id 字段名不同:小红书 note_id、快手 work_id。
    return {"ok": True, "item_id": result.get("note_id") or result.get("work_id"),
            "url": result.get("url")}
