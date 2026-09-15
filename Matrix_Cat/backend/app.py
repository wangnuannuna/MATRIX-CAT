# -*- coding: utf-8 -*-
"""Matrix_Cat 后端(FastAPI)。编排层:账号 / 任务 / 发布 API + 服务前端。

跑法(项目根下):
    uvicorn backend.app:app --port 8799 --reload
或经启动器:  python run.py web
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from . import crud
from .config import PLATFORMS, ROOT, settings
from .db import SessionLocal, init_db
from .api import accounts, admin, ai as ai_api, auth as auth_api, membership, platforms, publish, tasks

logger = logging.getLogger("matrixcat")


def _startup_banner() -> None:
    """开机横幅:两个入口链接 + 平台初始化自检(采集层入口在不在)+ 发布模式。"""
    port = os.getenv("MATRIXCAT_PORT", "8799")
    ready, missing = [], []
    for p in PLATFORMS:
        run_py = ROOT / "collectors" / f"{p['name']}_publisher" / "run.py"
        (ready if run_py.exists() else missing).append(p["label"])
    dry = os.getenv("MATRIXCAT_DRYRUN", "1") != "0"
    logger.info("=" * 60)
    logger.info("  矩阵猫 后端就绪")
    logger.info("  正式台  http://127.0.0.1:%s/app", port)
    logger.info("  测试台  http://127.0.0.1:%s/", port)
    line = f"  平台初始化 正常 {len(ready)}/{len(PLATFORMS)}:{' · '.join(ready) or '无'}"
    if missing:
        line += f"   (待接采集层:{' '.join(missing)})"
    logger.info(line)
    logger.info("  发布模式  %s", "真发布(会真发到平台)" if not dry else "模拟 DRY_RUN(不真发)")
    logger.info("=" * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()          # 建表(生产用 Alembic 迁移,这里开箱即用)
    db = SessionLocal()
    try:
        crud.sync_plans(db)   # 会员套餐 upsert 进库
    finally:
        db.close()
    _startup_banner()
    yield


app = FastAPI(title="Matrix_Cat API", version="0.2.0", lifespan=lifespan)


@app.middleware("http")
async def _api_key_guard(request: Request, call_next):
    # 设了 MATRIXCAT_API_KEY 才校验;本机默认空=不校验。health/前端页面放行。
    if settings.api_key and request.url.path.startswith("/api") and request.url.path != "/api/health":
        if request.headers.get("X-API-Key") != settings.api_key:
            return JSONResponse({"ok": False, "error": "无效或缺失 API Key(X-API-Key)"}, status_code=401)
    return await call_next(request)

for r in (platforms.router, accounts.router, tasks.router, publish.router,
          auth_api.router, membership.router, admin.router, ai_api.router):
    app.include_router(r, prefix="/api")


@app.get("/api/health")
def health():
    return {"ok": True, "service": "matrixcat", "db": settings.database_url.split(":")[0], "debug": settings.debug}


@app.get("/")
def index():
    # 测试台(简版,单号快速验证)。
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/app")
def console():
    # 正式台(浅色清爽版,账号矩阵/发布/任务,接 /api/*)。
    return FileResponse(ROOT / "frontend" / "console.html")


@app.get("/account")
def account():
    # 账户中心:平台用户注册/登录、资料、会员购买。
    return FileResponse(ROOT / "frontend" / "account.html")


@app.get("/admin")
def admin_console():
    # 总后台:管理员用自己的号登录后进,用户/订单/套餐管理。
    return FileResponse(ROOT / "frontend" / "admin.html")


@app.get("/app/beacon")
def beacon():
    # 旧的暗色 BEACON 方向,留个备份。
    return FileResponse(ROOT / "frontend" / "beacon.html")


_ASSETS = (ROOT / "frontend" / "assets").resolve()


@app.get("/favicon.ico")
def favicon():
    # 浏览器标签页图标:所有页面共用矩阵猫 logo,取代默认地球。
    return FileResponse(_ASSETS / "favicon.ico")


@app.get("/assets/{fname}")
def asset(fname: str):
    # 静态资源(logo 等)。挡目录穿越:只放行 assets 目录下的真实文件。
    p = (_ASSETS / fname).resolve()
    if p.parent != _ASSETS or not p.is_file():
        raise HTTPException(404, "资源不存在")
    return FileResponse(p)
