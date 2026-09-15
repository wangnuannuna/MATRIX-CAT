# -*- coding: utf-8 -*-
"""抖音平台常量。

全局可调项(UA / CloakBrowser / curl 指纹 / 代理 / 超时)在项目根 `config/settings.py`,
这里只留抖音**专属**的(域名/接口/aid/ServiceId/cookie 名),其余从全局 re-export。
接口常量都是回蚁小二 reptile 源码抠出来的字面值(见 [[project_yixiaoer_analysis]])。
"""
import os
import sys

# 本文件在 collectors/douyin_publisher/douyin/,往上四级 = 项目根
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import settings as _S  # noqa: E402

PLATFORM = "douyin"
COOKIE_DOMAIN = "douyin.com"

# ════════════════════════════════════════════════════════════════════════════
# 域名 / 接口路径
# ════════════════════════════════════════════════════════════════════════════
CREATOR_HOST = "https://creator.douyin.com"
VOD_HOST = "https://vod.bytedanceapi.com"          # 视频上传(ByteDance VOD)
IMAGEX_HOST = "https://imagex.bytedanceapi.com"    # 图文/封面上传(ByteDance ImageX)

LOGIN_URL = "https://creator.douyin.com/"          # 扫码登录页(创作者中心)
HOME_URL = "https://creator.douyin.com/creator-micro/home"
PUBLISH_URL = "https://creator.douyin.com/creator-micro/content/publish?enter_from=publish_page"

# 发布(写接口,过 bd-ticket-guard);a_bogus 留空
PATH_CREATE_V2 = "/web/api/media/aweme/create_v2/"
PATH_UPLOAD_AUTH = "/web/api/media/upload/auth/v5/"   # 拿 VOD/ImageX 的上传 STS
PATH_USER_INFO = "/web/api/media/aweme/user/info/"    # 校验登录态(占位,登录层真机再定)

# create_v2 URL 上那串固定 query(照抄源码,浏览器环境自述)
CREATE_V2_QUERY = {
    "read_aid": "2906",
    "cookie_enabled": "true",
    "screen_width": "1920",
    "screen_height": "1080",
    "browser_language": "zh-CN",
    "browser_platform": "Win32",
    "browser_name": "Mozilla",
    "browser_online": "true",
    "timezone_name": "Asia/Shanghai",
    "aid": "1128",
    "support_h265": "1",
    # msToken / a_bogus 由签名层拼(a_bogus 留空)
}

# ByteDance 上传服务的固定 id(回源码)
VOD_SPACE_NAME = "aweme"          # ApplyUploadInner SpaceName
VOD_APP_ID = 2906                 # VOD/ImageX 的 app_id / user_id 用抖音号 uid
IMAGEX_SERVICE_ID = "jm8ajry58r"  # ImageX ServiceId
VOD_API_VERSION = "2020-11-19"
IMAGEX_API_VERSION = "2018-08-01"

REFERER_PUBLISH = "https://creator.douyin.com/creator-micro/content/publish?enter_from=publish_page"
ORIGIN = "https://creator.douyin.com/"

# ════════════════════════════════════════════════════════════════════════════
# secsdk 种在 cookie 里的关键键名(bd-ticket-guard 全靠它们,登录必须拿全)
# 注意:cookie 名里带斜杠,是字节自己的命名,不是笔误。
# ════════════════════════════════════════════════════════════════════════════
COOKIE_EC_PRIVATE = "security-sdk/s_sdk_crypt_sdk"                 # 内含 ec_privateKey(PEM)
COOKIE_WEB_PROTECT = "security-sdk/s_sdk_sign_data_key/web_protect"  # 内含 ticket / ts_sign
COOKIE_TICKET_GUARD = "bd_ticket_guard_client_data"               # 内含 ree-public-key
COOKIE_TICKET_GUARD_V2 = "bd_ticket_guard_client_data_v2"         # 新版:ree_public_key
COOKIE_MSTOKEN = "msToken"

# 直发契约:这些 cookie 缺任一就别裸发(probe/签名层 fail-fast)
REQUIRED_SECSDK_COOKIES = (COOKIE_EC_PRIVATE, COOKIE_WEB_PROTECT)

# ════════════════════════════════════════════════════════════════════════════
# 本地目录(data/ 下 accounts.json + profiles/ + qr/)
# ════════════════════════════════════════════════════════════════════════════
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # douyin_publisher/
DATA_DIR = os.environ.get("DOUYIN_DATA_DIR", os.path.join(BASE_DIR, "data"))
ACCOUNTS_FILE = os.environ.get("DOUYIN_ACCOUNTS_FILE", os.path.join(DATA_DIR, "accounts.json"))
PROFILES_DIR = os.environ.get("DOUYIN_PROFILES_DIR", os.path.join(DATA_DIR, "profiles"))
QR_DIR = os.path.join(DATA_DIR, "qr")

# ════════════════════════════════════════════════════════════════════════════
# 从全局配置 re-export
# ════════════════════════════════════════════════════════════════════════════
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
    """抖音是境内站,默认直连(挂代理反而 IP 归属对不上被风控盯,见 shared/http.py)。
    显式 DOUYIN_PROXY 优先,否则走全局模式。"""
    p = os.environ.get("DOUYIN_PROXY") or None
    return p if p else _S.resolve_proxy()
