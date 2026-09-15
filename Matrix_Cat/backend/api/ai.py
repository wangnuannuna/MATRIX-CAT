# -*- coding: utf-8 -*-
"""AI 文案优化路由:把大白话改写成小红书爆款。需登录(要花 API 额度)。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import ai, auth
from ..models import User

router = APIRouter(tags=["ai"])


class AIOptimizeIn(BaseModel):
    raw: str
    platform: str = "xhs"
    kind: str = "image"


@router.get("/ai/status")
def status():
    """前端据此决定要不要亮 AI 按钮 / 提示未配置。返回供应商/模型/是否已配 key。"""
    return {"ok": True, **ai.info()}


@router.post("/ai/optimize")
def optimize(body: AIOptimizeIn, user: User = Depends(auth.require_user)):
    try:
        r = ai.optimize_content(body.raw, platform=body.platform, kind=body.kind)
    except ai.AIError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, **r}
