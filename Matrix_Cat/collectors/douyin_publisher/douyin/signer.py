# -*- coding: utf-8 -*-
"""抖音 bd-ticket-guard 客户端签名(蚁小二 clientSign 的 Python 端口)。

回源码实证(2026-07-13,三路对抗验证 CONFIRMED,见 [[project_yixiaoer_analysis]]):
create_v2 发布请求 URL 上的 a_bogus **留空**,真正把门的是请求头
`bd-ticket-guard-client-data` —— 客户端用 secsdk 种在 cookie 里的 EC 私钥,对
`ticket=..&path=/web/api/media/aweme/create_v2/&timestamp=..` 做 ECDSA-SHA256。

原 JS(蚁小二 reptile,逐字):
    const p = JSON.parse(JSON.parse(decodeURIComponent(cookie['security-sdk/s_sdk_crypt_sdk'])).data).ec_privateKey
    const g = JSON.parse(JSON.parse(decodeURIComponent(cookie['security-sdk/s_sdk_sign_data_key/web_protect'])).data)
    const k = `ticket=${g.ticket}&path=/web/api/media/aweme/create_v2/&timestamp=${ts}`
    const req_sign = createSign('SHA256').update(k).sign(createPrivateKey(p), 'base64')   // DER
    return base64(JSON.stringify({ts_sign:g.ts_sign, req_content:'ticket,path,timestamp', req_sign, timestamp:ts}))

ECDSA 原语在 shared.signing.ecdsa_sign_der(两边默认都出 DER,逐字节一致)。
本模块只读账号真实 cookie 里已有的键,绝不生成/编造——取不到就 raise,让上层去修登录。
"""
import base64
import json
import logging
import time
from urllib.parse import unquote

from . import config
from shared import signing

logger = logging.getLogger(config.PLATFORM)

# 固定头(照抄源码)
_BD_TICKET_STATIC = {
    "bd-ticket-guard-version": "2",
    "bd-ticket-guard-iteration-version": "1",
    "bd-ticket-guard-web-sign-type": "0",
}


class SecsdkCookieError(Exception):
    """secsdk 关键 cookie 缺失/无法解析 —— 登录态没拿全 bd-ticket-guard 需要的键。
    发布前 fail-fast,别带空指纹裸发。"""


# ════════════════════════════════════════════════════════════════════════════
# cookie 取值 & 解码
# ════════════════════════════════════════════════════════════════════════════
def cookie_map(cookies):
    """把 {name:value} dict 或 'a=b; c=d' 原始 Cookie 串统一成 dict。

    AccountStore 存的是 {name:value}(见 storage.session_store.cookies_for_domain),
    直发时既可能传 dict 也可能传拼好的 header 串,这里都吃。
    """
    if isinstance(cookies, dict):
        return dict(cookies)
    out = {}
    for part in str(cookies or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def cookie_header(cookies) -> str:
    """dict → 'a=b; c=d' 发出去用的 Cookie 头。原始串原样返回。"""
    if isinstance(cookies, str):
        return cookies
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def _decode_nested(value):
    """secsdk cookie 值:URL 编码的 JSON,内层 .data 又是一层 JSON 串。返回内层对象。

    对 storage_state 里到底存的是编码前还是编码后的值做了容错:先按 URL 编码解,
    解不出 JSON 再按原值试。
    """
    for candidate in (unquote(value), value):
        try:
            obj = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and isinstance(obj.get("data"), str):
            try:
                return json.loads(obj["data"])
            except ValueError:
                return obj
        return obj
    raise SecsdkCookieError(f"cookie 值解不出 JSON(前 40 字符: {str(value)[:40]!r})")


def _decode_b64_json(value):
    """bd_ticket_guard_client_data 那种:URL 编码的 base64(JSON)。"""
    raw = unquote(value)
    return json.loads(base64.b64decode(raw))


# ════════════════════════════════════════════════════════════════════════════
# 从 cookie 抽 secsdk 三件套
# ════════════════════════════════════════════════════════════════════════════
def get_ec_private_pem(cookies) -> str:
    cm = cookie_map(cookies)
    v = cm.get(config.COOKIE_EC_PRIVATE)
    if not v:
        raise SecsdkCookieError(
            f"缺 cookie {config.COOKIE_EC_PRIVATE!r}(EC 私钥):登录态没拿全,bd-ticket-guard 无法签")
    inner = _decode_nested(v)
    pem = inner.get("ec_privateKey") if isinstance(inner, dict) else None
    if not pem:
        raise SecsdkCookieError(f"{config.COOKIE_EC_PRIVATE!r} 里没有 ec_privateKey 字段")
    return pem


def get_ticket_bundle(cookies) -> dict:
    """返回 {ticket, ts_sign, ...}。"""
    cm = cookie_map(cookies)
    v = cm.get(config.COOKIE_WEB_PROTECT)
    if not v:
        raise SecsdkCookieError(
            f"缺 cookie {config.COOKIE_WEB_PROTECT!r}(ticket/ts_sign):登录态没拿全")
    g = _decode_nested(v)
    if not isinstance(g, dict) or "ticket" not in g:
        raise SecsdkCookieError(f"{config.COOKIE_WEB_PROTECT!r} 里没有 ticket 字段")
    return g


def get_ree_public_key(cookies) -> str:
    """bd-ticket-guard-ree-public-key 头值,从 cookie 取(新旧两种命名)。取不到返回空串。"""
    cm = cookie_map(cookies)
    v = cm.get(config.COOKIE_TICKET_GUARD)
    if v:
        try:
            return _decode_b64_json(v).get("bd-ticket-guard-ree-public-key", "") or ""
        except (ValueError, TypeError):
            pass
    v2 = cm.get(config.COOKIE_TICKET_GUARD_V2)
    if v2:
        try:
            return _decode_b64_json(v2).get("ree_public_key", "") or ""
        except (ValueError, TypeError):
            pass
    return ""


def get_mstoken(cookies) -> str:
    """msToken 取 cookie 里的;没有就用源码那个兜底占位(a12man123masb+毫秒)。"""
    cm = cookie_map(cookies)
    tok = cm.get(config.COOKIE_MSTOKEN)
    if tok:
        return tok
    return f"a12man123masb{int(time.time() * 1000)}"


# ════════════════════════════════════════════════════════════════════════════
# clientSign 端口:算 bd-ticket-guard-client-data
# ════════════════════════════════════════════════════════════════════════════
def client_sign(cookies, *, path: str = config.PATH_CREATE_V2, timestamp: int = None):
    """算一个 bd-ticket-guard-client-data。

    返回 (client_data_b64, meta),meta 含 timestamp/ticket,供上层拼 web-version 头。
    path 默认是 create_v2;删除/评论等其它写接口 path 不同(蚁小二对每个接口用各自 path)。
    """
    pem = get_ec_private_pem(cookies)
    g = get_ticket_bundle(cookies)
    ticket = g.get("ticket", "")
    ts = int(timestamp if timestamp is not None else time.time())

    signed_str = f"ticket={ticket}&path={path}&timestamp={ts}"
    sig_der = signing.ecdsa_sign_der(pem, signed_str)
    req_sign = base64.b64encode(sig_der).decode()

    payload = {
        "ts_sign": g.get("ts_sign", ""),
        "req_content": "ticket,path,timestamp",
        "req_sign": req_sign,
        "timestamp": ts,
    }
    # separators 去空格,贴近 JS JSON.stringify(纯为一致,服务端只 JSON.parse,格式不影响校验)
    client_data = base64.b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).decode()
    return client_data, {"timestamp": ts, "ticket": ticket}


# ════════════════════════════════════════════════════════════════════════════
# create_v2 发布请求头装配
# ════════════════════════════════════════════════════════════════════════════
def build_publish_headers(cookies, csrf_token: str = "", *, path: str = config.PATH_CREATE_V2,
                          is_video: bool = False, timestamp: int = None) -> dict:
    """拼 create_v2 那套请求头(含 bd-ticket-guard 全家 + cookie + Referer/Origin)。

    csrf_token = x-secsdk-csrf-token(从预检 co/settings 拿或 cookie 里带,登录层给)。
    is_video=True 时源码额外带 x-secsdk-csrf-request:1、Content-Type 带 charset。
    """
    client_data, meta = client_sign(cookies, path=path, timestamp=timestamp)
    web_version = "2" if str(meta["ticket"]).startswith("hash") else "1"
    content_type = "application/json;charset=UTF-8" if is_video else "application/json"

    headers = {
        "User-Agent": config.DEFAULT_UA,
        "Content-Type": content_type,
        "Cookie": cookie_header(cookies),
        "x-secsdk-csrf-token": csrf_token or "",
        "bd-ticket-guard-web-version": web_version,
        "bd-ticket-guard-ree-public-key": get_ree_public_key(cookies),
        "bd-ticket-guard-client-data": client_data,
        "Referer": config.REFERER_PUBLISH,
        "Origin": config.ORIGIN,
    }
    headers.update(_BD_TICKET_STATIC)
    if is_video:
        headers["x-secsdk-csrf-request"] = "1"
    return headers


def build_create_v2_url(cookies) -> str:
    """拼 create_v2 完整 URL:固定 query + msToken(cookie) + a_bogus 留空。"""
    from urllib.parse import urlencode
    q = dict(config.CREATE_V2_QUERY)
    q["browser_version"] = config.DEFAULT_UA  # 源码这里放 UA 串
    parts = urlencode(q)
    mstoken = get_mstoken(cookies)
    return f"{config.CREATOR_HOST}{config.PATH_CREATE_V2}?{parts}&msToken={mstoken}&a_bogus="
