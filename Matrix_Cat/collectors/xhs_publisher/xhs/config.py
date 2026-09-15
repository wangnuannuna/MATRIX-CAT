# -*- coding: utf-8 -*-
"""小红书平台常量。

**全局可调项(UA / CloakBrowser / curl 指纹 / 代理 / 超时)已挪到项目根的 `config/settings.py`**，
这里只留小红书**专属**的东西(域名/接口路径/签名口径/本地目录)，其余从全局配置 re-export，改一处全项目生效。
"""
import os
import sys

# 让 `import config`(项目根的全局配置) 能找到：本文件在 collectors/xhs_publisher/xhs/，往上四级 = 项目根
_CS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _CS_ROOT not in sys.path:
    sys.path.insert(0, _CS_ROOT)

from config import settings as _S  # noqa: E402  全局配置

# ════════════════════════════════════════════════════════════════════════════
# 小红书专属：域名 / 接口路径 / 固定头 / 签名口径（不往全局挪，别的平台用不上）
# ════════════════════════════════════════════════════════════════════════════
CREATOR_HOST = 'https://creator.xiaohongshu.com'
EDITH_HOST = 'https://edith.xiaohongshu.com'
LOGIN_URL = 'https://creator.xiaohongshu.com/login'
HOME_URL = 'https://creator.xiaohongshu.com/new/home'

PATH_USER_INFO = '/api/galaxy/user/info'                 # 校验登录态
PATH_UPLOAD_PERMIT = '/api/media/v1/upload/web/permit'   # 申请 COS 上传凭证
PATH_CREATE_NOTE = '/web_api/sns/v2/note'                # 创建笔记(写接口,要验签)
PATH_TOPIC_SEARCH = '/web_api/sns/v1/search/topic'
PATH_QR_CODE = '/api/cas/customer/web/qr-code'           # 登录页出二维码的接口

REFERER = 'https://creator.xiaohongshu.com/'
ORIGIN = 'https://creator.xiaohongshu.com'

COOKIE_DOMAIN = 'xiaohongshu.com'


def get_sign_uri_mode() -> str:
    """签名 URI 口径：GET 是否把 ?query 一起喂进签名 uri。默认带 query，真机校准后再定。"""
    v = (os.getenv('XHS_GET_SIGN_URI_MODE', 'path_query') or 'path_query').strip().lower()
    return v if v in ('path_query', 'path_only') else 'path_query'


# ════════════════════════════════════════════════════════════════════════════
# 本地目录（data/ 下 accounts.json + profiles/ + qr/）
# ════════════════════════════════════════════════════════════════════════════
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # xhs_publisher/
DATA_DIR = os.environ.get('XHS_DATA_DIR', os.path.join(BASE_DIR, 'data'))
ACCOUNTS_FILE = os.environ.get('XHS_ACCOUNTS_FILE', os.path.join(DATA_DIR, 'accounts.json'))
PROFILES_DIR = os.environ.get('XHS_PROFILES_DIR', os.path.join(DATA_DIR, 'profiles'))
QR_DIR = os.path.join(DATA_DIR, 'qr')

# ════════════════════════════════════════════════════════════════════════════
# 以下从全局配置 re-export（UA / CloakBrowser / curl / 时区语言 / 超时）
# ════════════════════════════════════════════════════════════════════════════
DEFAULT_UA = _S.BROWSER_UA
CURL_IMPERSONATE = _S.CURL_IMPERSONATE
CURL_UA = _S.CURL_UA
CURL_CH_UA = _S.CURL_CH_UA
CLOAK_LICENSE_KEY = _S.CLOAK_LICENSE_KEY
CLOAK_FINGERPRINT_PLATFORM = _S.CLOAK_FINGERPRINT_PLATFORM
CLOAK_HUMANIZE_LOGIN = _S.CLOAK_HUMANIZE_LOGIN
LOCALE = _S.LOCALE
TIMEZONE = _S.TIMEZONE
headless_for = _S.headless_for
API_TIMEOUT = _S.API_TIMEOUT
UPLOAD_TIMEOUT = _S.UPLOAD_TIMEOUT
LOGIN_TIMEOUT = _S.LOGIN_TIMEOUT

# ════════════════════════════════════════════════════════════════════════════
# 代理：默认走【北京出口】代理（用户定：本机 IP 号扛不住，宁可走代理，只要出口是北京）
# ════════════════════════════════════════════════════════════════════════════
# 显式 XHS_PROXY(给个 URL) 优先；否则按全局模式(CRAWLER_PROXY_MODE，默认 beijing=挑北京出口)。
_XHS_PROXY = os.environ.get('XHS_PROXY', '') or None
DEFAULT_PROXY = _XHS_PROXY  # 兼容旧引用；实际取用一律走 resolve_proxy()

# 拿不到合格北京代理时会 raise，供发布层转成干净报错(不静默用本机 IP 发)。
ProxyUnavailable = _S.ProxyUnavailable


def resolve_proxy():
    """本平台这次该用哪个代理。显式 XHS_PROXY 最优先；否则走全局模式(默认 beijing=挑北京出口)。

    默认会去 KDL 取一批、探出口城市、挑北京最快的那个；拿不到默认 raise ProxyUnavailable
    (宁可中止也别用本机 IP)。要直连设 CRAWLER_PROXY_MODE=direct，或指定住宅代理设 XHS_PROXY。
    注意 KDL getdps 多是机房 IP，比本机 IP 强但非住宅；要更稳可换住宅代理源(改 XHS_PROXY)。
    """
    if _XHS_PROXY:
        return _XHS_PROXY
    return _S.resolve_proxy()
