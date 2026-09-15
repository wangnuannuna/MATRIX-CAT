# -*- coding: utf-8 -*-
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import crud
from ..db import get_db

router = APIRouter(tags=["tasks"])


@router.get("/tasks")
def list_tasks(status: str | None = None, limit: int = 100, db: Session = Depends(get_db)):
    return {"ok": True, "tasks": [crud.task_public(db, t) for t in crud.list_tasks(db, status, limit)]}


@router.get("/tasks/{tid}")
def get_task(tid: int, db: Session = Depends(get_db)):
    t = crud.get_task(db, tid)
    if not t:
        raise HTTPException(404, "任务不存在")
    return {"ok": True, "task": crud.task_public(db, t)}


@router.get("/stats/overview")
def stats_overview(db: Session = Depends(get_db)):
    """工作台真实数据聚合(账号态 / 今日·本周发布 / 成功率 / 7天趋势 / 平台分布)。"""
    return {"ok": True, **crud.stats_overview(db)}
