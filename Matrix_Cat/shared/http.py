# -*- coding: utf-8 -*-
"""curl_cffi 会话工厂 + 代理路由。协议直连都从这起 session,带 Chrome 的 TLS/JA3 指纹。

为什么不用 requests:requests 的 TLS 指纹一眼假,写接口/上传容易被 JA3 拦。curl_cffi
impersonate 到具体 chrome 版本,指纹跟真浏览器一致。
"""
import logging
import time

from curl_cffi import requests as creq

logger = logging.getLogger(__name__)

# 本机 VPN 代理:海外站(TikTok/YouTube/IG)走它;境内站(抖音/小红书)默认直连——
# 境内号挂代理反而 IP 归属对不上、容易被风控盯。境内要固定出口就在平台 config 里配住宅代理。
LOCAL_VPN_PROXY = "http://127.0.0.1:2334"
_OVERSEAS = {"tiktok", "youtube", "instagram", "ig", "x", "twitter", "facebook"}


def default_proxy_for(platform, override=None):
    """平台没显式给代理时的默认:海外走 VPN,境内直连(None)。override 传空串 '' 可强制直连。"""
    if override is not None:
        return override or None
    return LOCAL_VPN_PROXY if str(platform).lower() in _OVERSEAS else None


def make_session(impersonate="chrome131", proxy=None, timeout=30, headers=None):
    """起一个 curl_cffi 会话。proxy=None 即直连;headers 是每个请求都带的公共头。"""
    s = creq.Session(impersonate=impersonate, timeout=timeout)
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    if headers:
        s.headers.update(headers)
    return s


def request_retry(session, method, url, *, retries=3, backoff=0.8, **kw):
    """带指数退避的重试。只对网络异常 / 5xx 重试;4xx 直接抛——签名错、风控 461 这类
    重试没意义,越重试越可疑。"""
    last = None
    for i in range(retries):
        try:
            r = session.request(method, url, **kw)
            if r.status_code < 500:
                return r
            last = RuntimeError(f"HTTP {r.status_code} {url}")
        except Exception as e:  # noqa: BLE001 网络层什么都可能抛
            last = e
        wait = backoff * (2 ** i)
        logger.warning(f"{method} {url} 第 {i + 1} 次失败: {last};{wait:.1f}s 后重试")
        time.sleep(wait)
    raise last
