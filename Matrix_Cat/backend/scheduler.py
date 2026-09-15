# -*- coding: utf-8 -*-
"""定时发布调度器:每 5 秒扫一遍到点的定时任务,推进队列。
    python -m backend.scheduler   (项目根,conda313)
"""
import time

from sqlalchemy import select

from . import crud, models
from .db import SessionLocal
from .jobs.tasks import enqueue


def tick() -> int:
    db = SessionLocal()
    now = int(time.time())
    n = 0
    try:
        due = db.scalars(
            select(models.Task).where(
                models.Task.status == "scheduled",
                models.Task.scheduled_at > 0,
                models.Task.scheduled_at <= now,
            ).limit(200)
        )
        for t in due:
            t.status = "queued"
            db.commit()
            if enqueue(t.id):
                n += 1
            crud.add_log(db, t.id, "info", "到点,入队")
        return n
    finally:
        db.close()


def run():
    print("[scheduler] 启动,每 5s 检查到点的定时任务(Ctrl+C 退出)")
    while True:
        try:
            n = tick()
            if n:
                print(f"[scheduler] 入队 {n} 个到点任务")
        except Exception as e:  # noqa: BLE001
            print("[scheduler] 出错:", e)
        time.sleep(5)


if __name__ == "__main__":
    run()
