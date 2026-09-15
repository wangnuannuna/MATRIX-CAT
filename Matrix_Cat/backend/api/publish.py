# -*- coding: utf-8 -*-
import logging
import re
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from .. import crud
from ..config import DATA_DIR
from ..db import get_db

router = APIRouter(tags=["publish"])
logger = logging.getLogger("matrixcat")

UPLOAD_DIR = DATA_DIR / "uploads"
_SAFE = re.compile(r"[^A-Za-z0-9_.\-一-鿿]")


async def _save(field: str, f: UploadFile) -> str:
    sub = UPLOAD_DIR / f"{int(time.time() * 1000)}_{field}"
    sub.mkdir(parents=True, exist_ok=True)
    name = _SAFE.sub("_", f.filename or "file")
    path = sub / name
    path.write_bytes(await f.read())
    return str(path)


@router.post("/publish")
async def publish(
    platform: str = Form("xhs"),
    account_id: str = Form(...),
    kind: str = Form("image"),
    title: str = Form(""),
    desc: str = Form(""),
    topics: str = Form(""),
    visibility: str = Form("self_only"),
    is_original: str = Form("0"),
    scheduled_at: int = Form(0),
    tid: int = Form(0),            # B站视频分区 id(其它平台忽略)
    source: str = Form(""),        # B站转载来源 URL(给了即按转载)
    images: List[UploadFile] = File(default=[]),
    video: Optional[UploadFile] = File(default=None),
    cover: Optional[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
):
    """撰写一次 → 建一个发布任务入库。真实发布由采集 worker 消费队列执行(第2步)。"""
    acc = crud.get_account(db, platform, account_id)
    if not acc:
        return {"ok": False, "error": f"账号未登记: {account_id},请先登录/登记"}

    media: list[str] = []
    if kind == "image":
        for f in images:
            if f and f.filename:
                media.append(await _save("img", f))
        if not media:
            return {"ok": False, "error": "图文至少上传一张图"}
    else:
        if not (video and video.filename and cover and cover.filename):
            return {"ok": False, "error": "发视频要同时上传 视频 和 封面"}
        media = [await _save("video", video), await _save("cover", cover)]

    tags = [t for t in re.split(r"[,\s，]+", topics or "") if t]
    # 平台专属参数打包进 options,采集层按平台透传(见 jobs/collector.py 的 _PLATFORM_OPTS)。
    options: dict = {}
    if tid:
        options["tid"] = tid
    if source:
        options["source"] = source
    task = crud.create_task(
        db, acc, kind=kind, title=title, body=desc, topics=tags, visibility=visibility,
        is_original=is_original in ("1", "true", "on"), media=media, scheduled_at=scheduled_at,
        options=options,
    )
    queued = False
    if task.status == "queued":                 # 立即发布 → 进队列;定时任务由 scheduler 到点再入队
        from ..jobs.tasks import enqueue
        queued = enqueue(task.id)
    kind_cn = "视频" if kind == "video" else "图文"
    logger.info("发布 · %s · %s · %s《%s》· task#%s%s",
                platform, (acc.nickname or account_id), kind_cn, (title or "无标题")[:20],
                task.id, "" if queued else "(待定时/未入队)")
    return {"ok": True, "task_id": task.id, "status": task.status, "queued": queued,
            "note": "已入队,worker 会消费执行(dry-run 模拟;设 MATRIXCAT_DRYRUN=0 走真发布)"}
