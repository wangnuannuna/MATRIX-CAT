# -*- coding: utf-8 -*-
"""发布任务的 worker 逻辑(Dramatiq actor)。

起 worker:  python -m dramatiq backend.jobs.tasks   (项目根,conda313)
它做的事:load 任务 → 幂等去重 → 标 running → 调采集(dry-run/真发)→ 回写 success/failed;
失败按 max_attempts 重试,耗尽标 failed。
"""
import hashlib
import logging
import os

import dramatiq
import redis as _redis

from . import broker  # noqa: F401  import 即设好 broker
from . import login as _login  # noqa: F401  注册 login_task actor,让 worker 一并加载
from .. import crud
from ..db import SessionLocal
from .collector import DRY_RUN, dispatch_publish

_r = _redis.from_url(os.getenv("MATRIXCAT_REDIS_URL", "redis://127.0.0.1:6379/0"), protocol=2)
logger = logging.getLogger("matrixcat.worker")

# worker 一加载就亮明身份:这个 worker 是"真发"还是"模拟"。排"显示成功却没作品"第一眼看这。
logger.info("发布 worker 就绪 · 模式:%s", "模拟 DRY_RUN(不真发)" if DRY_RUN else "真发布(会真发到平台)")


def _content_hash(t, acc) -> str:
    raw = f"{acc.id}|{t.kind}|{t.title}|{t.body}|{sorted(t.topics or [])}|{sorted(t.media or [])}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dramatiq.actor(max_retries=5, min_backoff=1000, max_backoff=30000, time_limit=600000)
def publish_task(task_id: int):
    db = SessionLocal()
    try:
        t = crud.get_task(db, task_id)
        if not t:
            return
        if t.status == "success":                 # 幂等:已成功不重跑(防队列重投)
            return
        acc = crud.get_account_by_pk(db, t.account_id)
        if not acc:
            t.status = "failed"; t.error = "账号不存在"; db.commit()
            return
        if acc.status == "disabled":
            t.status = "canceled"; t.error = "账号已停用,跳过发布"; db.commit()
            crud.add_log(db, t.id, "warn", t.error)
            return

        # 幂等去重:同号同内容 5 分钟内只发一次
        key = f"mc:pub:{_content_hash(t, acc)}"
        if not _r.set(key, task_id, nx=True, ex=300):
            owner = _r.get(key)
            if owner and int(owner) != task_id:
                t.status = "canceled"; t.error = f"与任务#{int(owner)}同号同内容,去重跳过"
                db.commit(); crud.add_log(db, t.id, "warn", t.error)
                return

        t.status = "running"; t.attempt += 1; db.commit()
        crud.add_log(db, t.id, "info", f"worker 执行 attempt={t.attempt}")
        logger.info("发布中 · task#%s · %s · %s", t.id, acc.platform, (acc.nickname or acc.account_id))

        res = dispatch_publish(db, t, acc)         # dry-run 或 真采集发布
        if res.get("ok"):
            t.status = "success"; t.result_url = res.get("url"); t.item_id = res.get("item_id"); t.error = None
            db.commit(); crud.add_log(db, t.id, "info", f"成功 item={t.item_id}")
            logger.info("✔ 发布成功 · task#%s · %s", t.id, t.result_url or t.item_id or "")
            return
        raise RuntimeError(res.get("error", "发布失败"))

    except Exception as e:  # noqa: BLE001
        db.rollback()
        t = crud.get_task(db, task_id)
        if t:
            t.error = str(e)[:500]
            if t.attempt >= t.max_attempts:
                t.status = "failed"; db.commit()
                crud.add_log(db, t.id, "error", f"耗尽重试({t.attempt}/{t.max_attempts}),失败: {e}")
                logger.error("✘ 发布失败 · task#%s · 已耗尽重试:%s", task_id, str(e)[:200])
                return                              # 不再抛 = 停止重试
            t.status = "queued"; db.commit()
            crud.add_log(db, t.id, "warn", f"失败重试({t.attempt}/{t.max_attempts}): {e}")
            logger.warning("↻ 发布失败重试(%s/%s)· task#%s:%s",
                           t.attempt, t.max_attempts, task_id, str(e)[:200])
        raise                                       # 抛出 = Dramatiq 退避重试
    finally:
        db.close()


def enqueue(task_id: int) -> bool:
    """把任务推进队列。broker 不可用也不抛(任务留在 DB,reconcile 会补)。"""
    try:
        publish_task.send(task_id)
        return True
    except Exception:  # noqa: BLE001
        return False


def reconcile_queued() -> int:
    """把 DB 里还是 queued(比如入队时 broker 短暂不可用)的任务补进队列。worker 起时跑一次。"""
    db = SessionLocal()
    try:
        n = 0
        for t in crud.list_tasks(db, status="queued", limit=500):
            if enqueue(t.id):
                n += 1
        return n
    finally:
        db.close()
