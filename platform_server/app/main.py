from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .routers import account, auth, billing
from .sms import make_provider
from .store import make_store

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.assert_prod_safe()  # 生产形态下配置不安全直接拒绝启动
    app.state.store = await make_store(settings.redis_url)
    app.state.sms = make_provider()
    print(f"[启动] 存储={type(app.state.store).__name__} "
          f"短信={settings.sms_provider} "
          f"库={settings.database_url.split('://', 1)[0]}")
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(auth.router)
app.include_router(account.router)
app.include_router(billing.router)


@app.get("/health")
async def health():
    return {"ok": True, "app": settings.app_name}
