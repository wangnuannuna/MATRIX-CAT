# -*- coding: utf-8 -*-
"""平台常量。复制模板后改 PLATFORM / COOKIE_DOMAIN / 域名 / PATH_*。

全局可调项(UA / CloakBrowser / curl 指纹 / 代理 / 超时)在项目根 `config/settings.py`，
这里只留本平台专属的，其余 re-export。参考 collectors/xhs_publisher/xhs/config.py。
"""
import os
import sys

# 让 `from config import settings` / `from shared/storage import ...` 找得到：pkg/config.py 往上四级 = 项目根
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import settings as _S  # noqa: E402

PLATFORM = "pkg"          # TODO 平台名，如 'douyin'
COOKIE_DOMAIN = ""        # TODO 该平台 cookie 域，如 'douyin.com'

# ---- 域名 / 接口路径 ----  TODO 按平台填
API_HOST = ""
LOGIN_URL = ""            # 平台登录页
HOME_URL = ""             # 登录后首页(发布会话开好导航到这)
PATH_USER_INFO = ""       # 校验登录态的接口
PATH_PUBLISH = ""         # 发布(写接口，要验签)

# ---- 本地目录(data/ 下 accounts.json + profiles/ + qr/) ----
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # <platform>_publisher/
DATA_DIR = os.environ.get(f'{PLATFORM.upper()}_DATA_DIR', os.path.join(BASE_DIR, 'data'))
ACCOUNTS_FILE = os.path.join(DATA_DIR, 'accounts.json')
PROFILES_DIR = os.path.join(DATA_DIR, 'profiles')   # 一号一份 CloakBrowser profile
QR_DIR = os.path.join(DATA_DIR, 'qr')

# ---- 从全局配置 re-export ----
DEFAULT_UA = _S.BROWSER_UA
CURL_IMPERSONATE = _S.CURL_IMPERSONATE
CURL_UA = _S.CURL_UA
CURL_CH_UA = _S.CURL_CH_UA
LOCALE = _S.LOCALE
TIMEZONE = _S.TIMEZONE
headless_for = _S.headless_for
LOGIN_TIMEOUT = _S.LOGIN_TIMEOUT
UPLOAD_TIMEOUT = _S.UPLOAD_TIMEOUT
ProxyUnavailable = _S.ProxyUnavailable


def resolve_proxy():
    """本平台这次用哪个代理：显式 <PLATFORM>_PROXY 优先，否则走全局模式(默认北京出口)。"""
    p = os.environ.get(f'{PLATFORM.upper()}_PROXY') or None
    return p if p else _S.resolve_proxy()
