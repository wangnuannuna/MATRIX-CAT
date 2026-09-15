# -*- coding: utf-8 -*-
"""
crawler_spider 全局配置（可调项集中放这，别再散在各处硬编码）。

分工：
  - 这里放「值 / 开关」——UA、CloakBrowser 参数、curl 指纹、代理、超时默认。
  - common/ 放「代码 / 工具」——http 会话、签名原语、账号存储。
  - 平台专属的域名 / 接口路径仍在各自 <平台>/config.py，不往这挪（那不是全局的）。

各平台/公共层都从这取默认值。改一处，全项目生效。环境变量都能覆盖（前缀 CRAWLER_ / KDL_）。
"""
import logging
import os
import re
import time
from typing import List, Optional

logger = logging.getLogger("config")


def _ensure_ascii_ca() -> None:
    """curl_cffi(libcurl) 在 Windows 打不开带中文的 CA 证书路径(error 77)——本机 conda 装在
    `...\\王丛宇\\...` 下就会踩，导致所有 curl_cffi HTTPS(含 COS 素材上传)全挂。
    若 certifi 路径非 ASCII，复制一份 cacert.pem 到 ASCII 临时路径并指过去，全局修好。"""
    if os.environ.get("CURL_CA_BUNDLE"):
        return
    try:
        import certifi
        src = certifi.where()
        if src.isascii():
            return
        import shutil
        import tempfile
        dst = os.path.join(tempfile.gettempdir(), "crawler_cacert.pem")
        if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
            shutil.copyfile(src, dst)
        os.environ["CURL_CA_BUNDLE"] = dst
        os.environ.setdefault("SSL_CERT_FILE", dst)
        logger.info(f"curl CA 路径含非 ASCII，已改指 {dst}")
    except Exception as e:
        logger.warning(f"CA 路径修正跳过: {e}")


_ensure_ascii_ca()

__all__ = [
    "CHROME_MAJOR", "BROWSER_UA", "CURL_IMPERSONATE", "CURL_UA", "CURL_CH_UA",
    "CLOAK_LICENSE_KEY", "CLOAK_STEALTH", "CLOAK_FINGERPRINT_PLATFORM",
    "LOCALE", "TIMEZONE", "CLOAK_HUMANIZE_LOGIN", "headless_for",
    "API_TIMEOUT", "UPLOAD_TIMEOUT", "LOGIN_TIMEOUT",
    "LOCAL_VPN_PROXY", "KDL_GETDPS_URL", "KDL_PROXY_USERNAME", "KDL_PROXY_PASSWORD",
    "KDL_AREA", "DEFAULT_PROXY_MODE", "ProxyUnavailable",
    "fetch_kdl_proxy", "resolve_beijing_proxy", "resolve_proxy",
]


# ════════════════════════════════════════════════════════════════════════════
# 浏览器 UA / 版本
# ════════════════════════════════════════════════════════════════════════════
# CloakBrowser 内核大版本（本机 chromium-146）。它自带真 UA，下面这个只作参考/兜底。
CHROME_MAJOR = 146
BROWSER_UA = (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              f"(KHTML, like Gecko) Chrome/{CHROME_MAJOR}.0.0.0 Safari/537.36")

# curl_cffi 直传/直连用的 TLS/JA3 模拟目标（0.15 这档最高 chrome131）。
# UA / sec-ch-ua 必须跟它版本自洽——别 TLS 报 131 却 UA 报 146（真不要假）。
CURL_IMPERSONATE = os.getenv("CRAWLER_IMPERSONATE", "chrome131")
_CURL_MAJOR = 131
CURL_UA = (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           f"(KHTML, like Gecko) Chrome/{_CURL_MAJOR}.0.0.0 Safari/537.36")
CURL_CH_UA = (f'"Google Chrome";v="{_CURL_MAJOR}", "Chromium";v="{_CURL_MAJOR}", '
              f'"Not_A Brand";v="24"')


# ════════════════════════════════════════════════════════════════════════════
# CloakBrowser
# ════════════════════════════════════════════════════════════════════════════
CLOAK_LICENSE_KEY = os.getenv("CLOAKBROWSER_LICENSE_KEY", "") or None
CLOAK_STEALTH = True                       # 二进制级反检测(它自带)，别再叠 JS 注入
CLOAK_FINGERPRINT_PLATFORM = "windows"     # 每号指纹里的平台位
LOCALE = "zh-CN"                           # 境内号固定
TIMEZONE = "Asia/Shanghai"
CLOAK_HUMANIZE_LOGIN = True                # 登录开拟人(贝塞尔鼠标/逐字打)；发布走协议不需要


def headless_for(scene: str) -> bool:
    """登录默认有头(看着扫码)，发布默认无头。CRAWLER_HEADLESS=1/0 强制覆盖。"""
    v = (os.getenv("CRAWLER_HEADLESS", "") or "").strip().lower()
    if v in ("1", "true", "yes"):
        return True
    if v in ("0", "false", "no"):
        return False
    return False if scene == "login" else True


# ════════════════════════════════════════════════════════════════════════════
# 超时
# ════════════════════════════════════════════════════════════════════════════
def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name, "") or "").strip() or default)
    except Exception:
        return default


API_TIMEOUT = _env_int("CRAWLER_API_TIMEOUT", 30)
UPLOAD_TIMEOUT = _env_int("CRAWLER_UPLOAD_TIMEOUT", 300)
LOGIN_TIMEOUT = _env_int("CRAWLER_LOGIN_TIMEOUT", 200)


# ════════════════════════════════════════════════════════════════════════════
# 代理
# ════════════════════════════════════════════════════════════════════════════
# 本机 VPN：海外站(TikTok/YT/IG)走它；境内站(抖音/小红书)不走它。
LOCAL_VPN_PROXY = os.getenv("CRAWLER_VPN_PROXY", "http://127.0.0.1:2334")

# ── 快代理 KDL 提取链接（getdps）：GET 它返回 IP:port ──────────────────────────
# ⚠️ KDL getdps 通常是【机房/数据中心 IP】。对小红书这类境内发布，机房 IP 本身有点风控味，
#    但比"用本机真实/VPN 出口 IP 发帖"强（本机 IP 一旦被标记，全矩阵受牵连）。
#    所以策略：默认走代理，且【只收北京出口】——地理跟内容一致，是当前最现实的收口。
KDL_GETDPS_URL = os.getenv(
    "KDL_GETDPS_URL",
    "https://dps.kdlapi.com/api/getdps/"
    "?secret_id=odprs082ta03filsv6zk&signature=gs82uazut4auxo0k371y2q1a0x6605nw"
    "&num=1&format=text&sep=1")
# KDL 私密代理认证：用「用户名+密码」认证就填这俩；后台加了本机公网 IP 白名单则留空。
KDL_PROXY_USERNAME = os.getenv("KDL_PROXY_USERNAME", "d4265483588") or None
KDL_PROXY_PASSWORD = os.getenv("KDL_PROXY_PASSWORD", "t0eslso6") or None
# 让 KDL 直接尽量给这个地区（服务端过滤，配合下面出口城市探测双保险）。
KDL_AREA = os.getenv("KDL_AREA", "北京")

# 默认代理模式：'beijing'(默认·只收北京出口) / 'direct'(直连) / 'vpn' / 'kdl'(不挑地区) / 给 http URL。
DEFAULT_PROXY_MODE = os.getenv("CRAWLER_PROXY_MODE", "beijing")

# 出口城市探测的目标关键词（命中即算北京）。
_BEIJING_ALIASES = ("beijing", "北京")
_IPPORT_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}:\d{1,5}$")


class ProxyUnavailable(Exception):
    """要求走代理但没拿到合格(北京)代理——宁可中止也别用本机 IP 发帖(封号风险)。"""


def _allow_direct_fallback() -> bool:
    return (os.getenv("CRAWLER_ALLOW_DIRECT_FALLBACK", "") or "").strip().lower() in ("1", "true", "yes")


def _wrap_auth(ipport: str) -> str:
    if KDL_PROXY_USERNAME and KDL_PROXY_PASSWORD:
        return f"http://{KDL_PROXY_USERNAME}:{KDL_PROXY_PASSWORD}@{ipport}"
    return f"http://{ipport}"


def _mask(purl: str) -> str:
    """日志里别把代理账密打全。"""
    return re.sub(r"//[^@/]+@", "//***@", purl or "")


def _kdl_fetch_raw(num: int = 1, area: Optional[str] = None, timeout: int = 8) -> List[str]:
    """调 KDL getdps 取 num 个代理(可带 area 让服务端尽量给该地区)。返回 [http://[user:pass@]ip:port, ...]。

    从配置的 KDL_GETDPS_URL 里拆出 secret_id/signature，自己拼参数(好控制 num/area)。
    trust_env=False：这次提取请求别被本机 VPN 拦。
    """
    from urllib.parse import urlparse, parse_qs
    u = urlparse(KDL_GETDPS_URL)
    q = parse_qs(u.query)
    sid = (q.get("secret_id") or [""])[0]
    sig = (q.get("signature") or [""])[0]
    if not sid or not sig:
        logger.warning("KDL secret_id/signature 缺失（检查 KDL_GETDPS_URL）")
        return []
    endpoint = f"{u.scheme}://{u.netloc}{u.path}"
    params = {"secret_id": sid, "signature": sig, "num": num,
              "format": "text", "sep": 1, "dedup": 1}
    if area:
        params["area"] = area
    try:
        import httpx
        r = httpx.get(endpoint, params=params, timeout=timeout, trust_env=False)
        text = (r.text or "").strip()
    except Exception as e:
        logger.warning(f"KDL 代理提取失败: {e}")
        return []
    out = [_wrap_auth(line.strip()) for line in text.splitlines() if _IPPORT_RE.match(line.strip())]
    if not out and text:
        logger.warning(f"KDL 返回无有效代理（可能库存不足/需白名单）: {text[:120]}")
    return out


def fetch_kdl_proxy(timeout: int = 8) -> Optional[str]:
    """取一个 KDL 代理(不挑地区)，拿不到返回 None。"""
    lst = _kdl_fetch_raw(1, area=None, timeout=timeout)
    return lst[0] if lst else None


def _probe_proxy_city(purl: str, timeout: int = 4):
    """经该代理查出口 IP 的城市/运营商，顺带测速。返回 (ok, city, org, latency_ms)。

    学 HuiMei：一次请求既查地区又测速(请求本身走代理，往返耗时=代理速度)。
    境内 ipip 优先(快、中文城市)，失败回退 ipinfo(英文城市)。

    用 httpx 而非 curl_cffi：本机 conda 路径带中文，libcurl 加载不了 CA 文件(error 77)。
    verify=False：只是查自己的出口 IP，不碰敏感数据，跳过证书校验最省心、也绕开那个坑。
    """
    import httpx
    # ipip：{"data":{"location":[国,省,市,区,运营商]}}
    t0 = time.time()
    try:
        with httpx.Client(proxy=purl, timeout=timeout, verify=False, trust_env=False) as c:
            r = c.get("https://myip.ipip.net/json")
        if r.status_code == 200:
            loc = ((r.json() or {}).get("data") or {}).get("location") or []
            city = (loc[2] if len(loc) > 2 and loc[2] else (loc[1] if len(loc) > 1 else "")) or ""
            org = (loc[4] if len(loc) > 4 else "") or ""
            if city:
                return True, city, org, int((time.time() - t0) * 1000)
    except Exception:
        pass
    t1 = time.time()
    try:
        with httpx.Client(proxy=purl, timeout=timeout, verify=False, trust_env=False) as c:
            r = c.get("https://ipinfo.io/json")
        if r.status_code == 200:
            d = r.json()
            return True, (d.get("city") or ""), (d.get("org") or ""), int((time.time() - t1) * 1000)
    except Exception:
        pass
    return False, "", "", 10 ** 9


def resolve_beijing_proxy(batch: int = 10, rounds: int = 2,
                          probe_timeout: int = 4, wall_cap: float = 7.0) -> Optional[str]:
    """取一批 KDL 代理 → 并发查出口城市+测速 → 在北京里挑最快的返回。找不到返回 None。

    学 HuiMei `_find_required_city_proxy`：批量并发探测 + 墙钟上限，慢代理直接丢，
    本批挑到就返回、一批全废再取一批。这里【只按城市=北京过滤】(不硬卡住宅，
    因为 KDL getdps 多是机房；是否机房只做日志提示)。
    """
    import concurrent.futures as cf
    for rnd in range(max(1, rounds)):
        cands = _kdl_fetch_raw(batch, area=KDL_AREA)
        if not cands:
            logger.warning(f"代理预检[第{rnd + 1}批]：没取到代理")
            continue
        logger.info(f"代理预检[第{rnd + 1}批]：取到 {len(cands)} 个，并发查城市+测速(墙钟 {wall_cap}s)…")
        survivors = []  # (latency, purl, city, org)
        ex = cf.ThreadPoolExecutor(max_workers=min(len(cands), 12))
        try:
            fut2p = {ex.submit(_probe_proxy_city, p, probe_timeout): p for p in cands}
            done, _pending = cf.wait(list(fut2p), timeout=wall_cap)
            for f in done:
                try:
                    ok, city, org, lat = f.result()
                except Exception:
                    continue
                if not ok:
                    continue
                if not any(a in (city or "").lower() for a in _BEIJING_ALIASES):
                    continue
                survivors.append((lat, fut2p[f], city, org))
        finally:
            ex.shutdown(wait=False)   # 别等慢代理(各自 httpx 超时会自然结束)
        if survivors:
            survivors.sort(key=lambda x: x[0])
            lat, purl, city, org = survivors[0]
            dc = _looks_datacenter(org)
            logger.info(f"✅ 选定北京代理 {_mask(purl)} ({city}/{org}, {lat}ms"
                        f"{' ·机房' if dc else ''})；本批合格 {len(survivors)} 个")
            return purl
        logger.info(f"第{rnd + 1}批无北京代理，再取一批…")
    logger.warning("预检后仍未找到北京代理")
    return None


def _looks_datacenter(org: str) -> bool:
    o = (org or "").lower()
    return any(k in o for k in ("aliyun", "alibaba", "tencent", "阿里", "腾讯", "idc",
                                "data center", "datacenter", "机房", "cloud", "ucloud", "huawei"))


def resolve_proxy(mode: Optional[str] = None) -> Optional[str]:
    """按模式给出一个代理 URL（或 None=直连）。**默认 'beijing'：只收北京出口。**

    mode: 'beijing'(默认,挑北京) | 'direct'/'none'(直连) | 'vpn' | 'kdl'(不挑地区) | 'http://...'。
    要求走代理却没拿到合格代理时，默认 raise ProxyUnavailable（宁可中止也别用本机 IP）；
    设 CRAWLER_ALLOW_DIRECT_FALLBACK=1 可改成回退直连。'kdl' 与 'beijing' 会发网络请求去取。
    """
    mode = (mode or DEFAULT_PROXY_MODE or "beijing").strip().lower()
    if mode in ("direct", "none", ""):
        return None
    if mode == "vpn":
        return LOCAL_VPN_PROXY
    if mode.startswith("http"):
        return mode
    if mode in ("beijing", "bj", "北京"):
        p = resolve_beijing_proxy()
    elif mode == "kdl":
        p = fetch_kdl_proxy()
    else:
        logger.warning(f"未知代理模式 {mode!r}，按直连处理")
        return None
    if p:
        return p
    if _allow_direct_fallback():
        logger.warning("没拿到代理，按 CRAWLER_ALLOW_DIRECT_FALLBACK 回退直连")
        return None
    raise ProxyUnavailable(
        f"代理模式={mode} 但没拿到合格代理(北京)，已中止以免用本机 IP 发帖。"
        "稍后重试 / 检查 KDL 北京库存或白名单；或设 CRAWLER_ALLOW_DIRECT_FALLBACK=1 允许直连回退。")
