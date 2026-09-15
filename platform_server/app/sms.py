import secrets

from .config import get_settings
from .store import BaseStore

settings = get_settings()


class SmsError(Exception):
    """频控/发送失败,路由里转成 429"""


class ConsoleSms:
    async def send(self, phone: str, code: str):
        print(f"[SMS] -> {phone}  验证码 {code}  (开发模式,仅打印到控制台)")


class AliyunSms:
    # TODO 上线接阿里云 dysmsapi:填 AccessKey / 短信签名 / 模板 CODE,调 SendSms
    async def send(self, phone: str, code: str):
        raise SmsError("阿里云短信还没配好")


def make_provider():
    return AliyunSms() if settings.sms_provider == "aliyun" else ConsoleSms()


def _gen_code(n: int) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(n))


async def _as_int(store: BaseStore, key: str) -> int:
    v = await store.get(key)
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


async def send_code(phone: str, store: BaseStore, provider, ip: str | None = None) -> str:
    # 先做频控检查(只读,不 incr)——发送失败就不占配额、不留"幽灵码"
    if await store.get(f"sms:cd:{phone}"):
        raise SmsError("发送太频繁,请稍后再试")
    if await _as_int(store, f"sms:day:{phone}") >= settings.sms_daily_limit:
        raise SmsError("今日验证码次数已达上限")
    if ip and await _as_int(store, f"sms:ipday:{ip}") >= settings.sms_ip_daily_limit:
        raise SmsError("请求过于频繁,请稍后再试")

    code = _gen_code(settings.sms_code_len)
    await provider.send(phone, code)  # 失败会抛,下面都不执行

    # 发成功了才落库+计数
    await store.setex(f"sms:code:{phone}", settings.sms_code_ttl, code)
    await store.setex(f"sms:cd:{phone}", settings.sms_cooldown, "1")
    await store.delete(f"sms:fail:{phone}")   # 新码,清掉旧的猜错计数
    await store.incr_ttl(f"sms:day:{phone}", 86400)
    if ip:
        await store.incr_ttl(f"sms:ipday:{ip}", 86400)
    return code


async def verify_code(phone: str, code: str, store: BaseStore) -> bool:
    real = await store.get(f"sms:code:{phone}")
    if real is None:
        return False
    if secrets.compare_digest(str(real), str(code)):
        await store.delete(f"sms:code:{phone}")   # 用一次就作废
        await store.delete(f"sms:fail:{phone}")
        return True
    # 猜错:计数,超阈值就把码作废,逼对方重新发(挡爆破)
    fails = await store.incr_ttl(f"sms:fail:{phone}", settings.sms_code_ttl)
    if fails >= settings.sms_verify_max_attempts:
        await store.delete(f"sms:code:{phone}")
        await store.delete(f"sms:fail:{phone}")
    return False
