# -*- coding: utf-8 -*-
"""后端配置。可调项集中这,环境变量覆盖(前缀 MATRIXCAT_)。

生产切 Postgres 只改一处:
    set MATRIXCAT_DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/matrixcat
开发默认 SQLite,开箱即跑,不用先装数据库。
"""
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent          # 项目根 Matrix_Cat/
DATA_DIR = ROOT / "storage" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_dotenv(path) -> None:
    """把 .env 的 KEY=VALUE 灌进 os.environ(真实环境变量优先)。pydantic 只把 .env 读进 Settings,
    但采集层 collector/login 的 MATRIXCAT_DRYRUN / CRAWLER_*/*_PROXY 都走 os.getenv,不经 pydantic,
    所以这里显式灌一遍,让 .env 对全项目生效。config 被最早 import,worker 进程也会跑到这。"""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k:
                    os.environ.setdefault(k, v)     # 真实环境变量优先,不覆盖
    except OSError:
        pass


_load_dotenv(ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MATRIXCAT_", env_file=str(ROOT / ".env"),
                                      env_file_encoding="utf-8", extra="ignore")

    database_url: str = f"sqlite:///{(DATA_DIR / 'matrixcat.db').as_posix()}"
    api_key: str = ""            # 设了就要求请求带 X-API-Key;本机默认空=不校验
    debug: bool = True
    jwt_secret: str = ""         # 空则用 storage/data/.jwt.key 自动生成一把(别进 git)
    token_ttl_hours: int = 168   # 登录 token 有效期,默认 7 天
    # 本机总控台:True 时不带 token 也能看/管全部号(方便本地调试);
    # 正式 SaaS 部署一定要设 MATRIXCAT_LOCAL_CONSOLE=0,匿名请求一律 401,防跨租户越权。
    local_console: bool = True

    # ---- 支付 ----
    # 收款网关:mock(默认,点确认即视为已付,仅本地/演示)/ alipay / wechat。
    # 换真支付只改这个 + 填下面的商户密钥,membership 路由和激活逻辑都不用动。
    pay_provider: str = "mock"
    # 支付宝(RSA2)。要真收款把这几项填上,并把 pay_provider 设成 alipay。
    alipay_app_id: str = ""
    alipay_private_key: str = ""      # 应用私钥(PKCS8,PEM 一行或多行)
    alipay_public_key: str = ""       # 支付宝公钥(验回调签名用)
    alipay_gateway: str = "https://openapi.alipay.com/gateway.do"
    alipay_notify_url: str = ""       # 异步回调地址(公网可达)
    # 微信 Native 预留位(暂未接实现)
    wechat_mchid: str = ""
    wechat_api_key: str = ""
    wechat_notify_url: str = ""

    # ---- AI 文案优化 ----
    # 供应商:zhipu(默认·国内免费 GLM-4-Flash)/ dashscope / siliconflow / moonshot / deepseek / claude / openai
    ai_provider: str = "zhipu"
    ai_key: str = ""              # 所选供应商的 API key;留空则读各家专属环境变量(如 ZHIPU_API_KEY)
    ai_model: str = ""            # 覆盖默认模型;留空用供应商默认
    ai_base_url: str = ""         # provider=openai 时的自定义兼容地址
    anthropic_api_key: str = ""   # provider=claude 时用


settings = Settings()

# 会员套餐初始数据,启动时 upsert 进库(改价/加档在这改,code 不要动)。
DEFAULT_PLANS = [
    {"code": "free",     "name": "免费版", "price_cents": 0,     "duration_days": 36500, "account_quota": 1,   "sort": 0, "description": "体验:绑 1 个账号"},
    {"code": "basic",    "name": "基础版", "price_cents": 9900,  "duration_days": 30,    "account_quota": 5,   "sort": 1, "description": "5 个账号 / 月"},
    {"code": "pro",      "name": "专业版", "price_cents": 29900, "duration_days": 30,    "account_quota": 20,  "sort": 2, "description": "20 个账号 / 月"},
    {"code": "flagship", "name": "旗舰版", "price_cents": 99900, "duration_days": 30,    "account_quota": 100, "sort": 3, "description": "100 个账号 / 月"},
]

# 平台注册表。新平台:这加一条 + 采集层加 collectors/<平台>_publisher(run.py 需实现 --emit-state/--emit-result 契约)。
PLATFORMS = [
    {"name": "xhs", "label": "小红书"},
    {"name": "kuaishou", "label": "快手"},
    {"name": "bilibili", "label": "哔哩哔哩"},
]
