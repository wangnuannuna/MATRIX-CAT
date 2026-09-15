from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# 源码里的占位密钥——生产若还等于它,启动守卫会拒绝起服务
DEFAULT_SECRET = "dev-only-insecure-secret-change-me-in-prod"


class Settings(BaseSettings):
    # 所有配置都能用 MC_ 前缀的环境变量或 .env 覆盖
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MC_", extra="ignore")

    app_name: str = "Matrix_Cat 云端鉴权"
    debug: bool = True

    # 库:默认 sqlite 开箱即跑;上线换 postgresql+asyncpg://user:pwd@host:5432/matrixcat
    database_url: str = "sqlite+aiosqlite:///./matrixcat.db"
    # redis 连不上会自动退回内存版(仅本地开发,别拿去上线)
    redis_url: str = "redis://127.0.0.1:6379/0"

    # JWT:access 短命、无状态;refresh 是另发的随机串,存库好吊销
    jwt_secret: str = DEFAULT_SECRET            # 上线务必换成长随机串(≥32)
    jwt_alg: str = "HS256"
    access_ttl_min: int = 30                    # access token 活 30 分钟
    refresh_ttl_days: int = 14                  # refresh 活 14 天

    # 短信验证码
    sms_provider: str = "console"               # console=开发打日志; aliyun=上线接阿里云
    sms_code_len: int = 6
    sms_code_ttl: int = 300                     # 验证码 5 分钟有效
    sms_cooldown: int = 60                      # 同号 60s 才能再发一条
    sms_daily_limit: int = 10                   # 每号每天最多几条
    sms_ip_daily_limit: int = 30               # 每 IP 每天最多发几条(防遍历轰炸)
    sms_verify_max_attempts: int = 5           # 一个码最多猜错几次就作废(防爆破)
    sms_debug_echo: bool = True                 # 把验证码回给前端方便自测;只在非生产+console 下才真生效

    # 密码登录
    pwd_min_len: int = 8
    login_fail_limit: int = 10                  # 账号/IP 在窗口内最多错几次就锁
    login_fail_window: int = 900               # 锁定观察窗口(秒)

    # ---- 生产形态判定 & 守卫 ----
    @property
    def is_production(self) -> bool:
        # 非 sqlite 库 或 用真短信 → 视为生产形态
        return (not self.database_url.startswith("sqlite")) or self.sms_provider == "aliyun"

    @property
    def dev_echo_enabled(self) -> bool:
        # 只有"非生产 + console 短信 + 开关开"三者同真才回显验证码
        return self.sms_debug_echo and self.sms_provider == "console" and not self.is_production

    def assert_prod_safe(self) -> None:
        """启动时 fail-closed:生产形态下配置不安全就拒绝起服务"""
        if not self.is_production:
            return
        bad = []
        if self.jwt_secret == DEFAULT_SECRET or len(self.jwt_secret) < 32:
            bad.append("MC_JWT_SECRET 未设或过短,需 ≥32 位随机串")
        if self.sms_provider == "console":
            bad.append("生产不能用 console 短信,请设 MC_SMS_PROVIDER=aliyun")
        if self.sms_debug_echo:
            bad.append("生产必须 MC_SMS_DEBUG_ECHO=false,别回显验证码")
        if bad:
            raise RuntimeError("生产配置不安全,拒绝启动:\n  - " + "\n  - ".join(bad))


@lru_cache
def get_settings() -> Settings:
    return Settings()
