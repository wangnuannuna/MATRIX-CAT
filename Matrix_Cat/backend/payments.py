# -*- coding: utf-8 -*-
"""支付网关抽象。membership 路由只认这个接口,换渠道不用动业务。

  - mock:   默认。下单返回一个本地确认链接,前端 confirm 后调 /membership/pay 即视为已付。
            只给本地/演示用,别拿去真收款。
  - alipay: 支付宝当面付(alipay.trade.precreate)。RSA2 签名/验签是真的,但要真收款
            得先在 config 填 alipay_app_id / 应用私钥 / 支付宝公钥,并做一次真实商户联调。
            没填密钥就直接抛错,绝不假装出二维码。
  - wechat: 预留,还没接,调用即抛错。

激活会员的逻辑在 crud.mark_order_paid,跟渠道无关,这里只管"生成付款方式"和"校验回调"。
"""
from __future__ import annotations

import base64
import json
import time
import urllib.parse
import urllib.request

from .config import settings


class PaymentError(Exception):
    """支付渠道相关的错误(未配置 / 网关拒绝 / 验签失败)。"""


class PaymentProvider:
    name = "base"

    def create_payment(self, order) -> dict:
        """给一张待付订单生成付款入口。返回 {provider, pay_url|qr, ...}。"""
        raise NotImplementedError

    def verify_callback(self, payload: dict) -> str | None:
        """校验网关回调,通过则返回 out_trade_no,否则 None/抛错。"""
        raise NotImplementedError


class MockProvider(PaymentProvider):
    name = "mock"

    def create_payment(self, order) -> dict:
        return {
            "provider": "mock",
            "pay_url": f"/pay/mock?out_trade_no={order.out_trade_no}",
            "note": "mock 支付:调 POST /api/membership/pay 传这个 out_trade_no 即视为已付(仅本地/演示)",
        }

    def verify_callback(self, payload: dict) -> str | None:
        # mock 没有真网关,信任前端回传的订单号(路由层已校验订单归属)。
        return payload.get("out_trade_no")


class AlipayProvider(PaymentProvider):
    """支付宝当面付。真实 RSA2 签名,缺密钥即抛错。未经真实商户联调,上线前务必自测一单。"""
    name = "alipay"

    def __init__(self) -> None:
        if not (settings.alipay_app_id and settings.alipay_private_key and settings.alipay_public_key):
            raise PaymentError("支付宝未配置:需填 alipay_app_id / alipay_private_key / alipay_public_key")
        try:
            from cryptography.hazmat.primitives import hashes, serialization  # noqa: F401
            from cryptography.hazmat.primitives.asymmetric import padding  # noqa: F401
        except Exception as e:  # noqa: BLE001
            raise PaymentError(f"缺 cryptography,无法做 RSA2 签名:{e}")

    # ---- RSA2(SHA256withRSA)签名 / 验签 ----
    def _load_private_key(self):
        from cryptography.hazmat.primitives import serialization
        pem = settings.alipay_private_key.strip()
        if "BEGIN" not in pem:   # 允许只贴 base64 正文,自动补 PEM 头尾
            pem = "-----BEGIN PRIVATE KEY-----\n" + "\n".join(
                pem[i:i + 64] for i in range(0, len(pem), 64)) + "\n-----END PRIVATE KEY-----\n"
        return serialization.load_pem_private_key(pem.encode(), password=None)

    def _load_alipay_public_key(self):
        from cryptography.hazmat.primitives import serialization
        pem = settings.alipay_public_key.strip()
        if "BEGIN" not in pem:
            pem = "-----BEGIN PUBLIC KEY-----\n" + "\n".join(
                pem[i:i + 64] for i in range(0, len(pem), 64)) + "\n-----END PUBLIC KEY-----\n"
        return serialization.load_pem_public_key(pem.encode())

    def _sign(self, params: dict) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        raw = "&".join(f"{k}={params[k]}" for k in sorted(params) if params[k] != "" and k != "sign")
        sig = self._load_private_key().sign(raw.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(sig).decode()

    def _verify(self, params: dict) -> bool:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        sign = params.get("sign", "")
        raw = "&".join(f"{k}={params[k]}" for k in sorted(params)
                       if k not in ("sign", "sign_type") and params[k] != "")
        try:
            self._load_alipay_public_key().verify(
                base64.b64decode(sign), raw.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
            return True
        except InvalidSignature:
            return False

    def create_payment(self, order) -> dict:
        biz = {
            "out_trade_no": order.out_trade_no,
            "total_amount": f"{order.amount_cents / 100:.2f}",
            "subject": f"矩阵猫会员-{order.plan_name}",
        }
        params = {
            "app_id": settings.alipay_app_id,
            "method": "alipay.trade.precreate",
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "version": "1.0",
            "notify_url": settings.alipay_notify_url,
            "biz_content": json.dumps(biz, separators=(",", ":"), ensure_ascii=False),
        }
        params["sign"] = self._sign(params)
        req = urllib.request.Request(
            settings.alipay_gateway,
            data=urllib.parse.urlencode(params).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        node = data.get("alipay_trade_precreate_response", {})
        if node.get("code") != "10000":
            raise PaymentError(f"支付宝下单失败:{node.get('sub_msg') or node.get('msg')}")
        return {"provider": "alipay", "qr_code": node.get("qr_code"), "pay_url": node.get("qr_code")}

    def verify_callback(self, payload: dict) -> str | None:
        if not self._verify(dict(payload)):
            raise PaymentError("支付宝回调验签失败")
        if payload.get("trade_status") not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            return None
        return payload.get("out_trade_no")


class WechatProvider(PaymentProvider):
    name = "wechat"

    def __init__(self) -> None:
        raise PaymentError("微信支付还没接,先用 mock 或 alipay")


_PROVIDERS = {"mock": MockProvider, "alipay": AlipayProvider, "wechat": WechatProvider}


def get_provider(name: str | None = None) -> PaymentProvider:
    name = (name or settings.pay_provider or "mock").lower()
    cls = _PROVIDERS.get(name)
    if not cls:
        raise PaymentError(f"未知支付渠道:{name}")
    return cls()
