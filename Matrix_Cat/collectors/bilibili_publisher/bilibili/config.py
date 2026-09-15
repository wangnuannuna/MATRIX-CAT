# -*- coding: utf-8 -*-
"""B站(bilibili)创作中心常量。

**全局可调项(UA / CloakBrowser / curl 指纹 / 代理 / 超时)在项目根 `config/settings.py`**,
这里只留 B站**专属**的(域名/接口路径/cookie 口径/上传参数/本地目录),其余从全局配置 re-export。

B站跟 xhs/快手不一样的地方(决定了整套写法):
  · 写接口鉴权 = **cookie(SESSDATA)+ csrf(bili_jct)**,没有逐请求 JS 签名(不用页内加签)。
  · 所以发布**全程 curl_cffi 直连**(preupload→upos 分片→封面→submit),不开浏览器——
    浏览器只在扫码登录时用(见 login.py)。契合协议直连优先的口径。
  · 少数 api.bilibili.com 读接口要 wbi 签名(w_rid+wts),自算,见 signer.py。

置信度标注(照 kuaishou/config.py 的规矩,别当成都验证过了):
  [确认]   B站 web 上传公开逆向多方互证(upos 视频链路属这档)
  [推断]   按同构类推,没逐字段真机核对
  [待校准] 必须真机抓包/发一条确认,代码里对应处 fail-loud,禁止拿占位值静默上线
参考已落地同构实现:collectors/kuaishou_publisher/kuaishou/config.py。
"""
import os
import sys

# 本文件在 collectors/bilibili_publisher/bilibili/,往上四级 = 项目根
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import settings as _S  # noqa: E402  全局配置

PLATFORM = 'bilibili'
COOKIE_DOMAIN = 'bilibili.com'          # [确认] 登录态种在 .bilibili.com 根域

# ════════════════════════════════════════════════════════════════════════════
# 页面 URL(仅登录场景用浏览器;发布不开浏览器)
# ════════════════════════════════════════════════════════════════════════════
# 未登录开创作中心会自动 302 到 passport 扫码页;直接打 passport/login 二维码也直接出。
LOGIN_URL = 'https://passport.bilibili.com/login'                        # [确认] 扫码登录页
HOME_URL = 'https://member.bilibili.com/platform/upload/video/frame'    # [确认] 视频投稿页(登录态校验落点)

REFERER = 'https://member.bilibili.com/'
ORIGIN = 'https://member.bilibili.com'
WEB_REFERER = 'https://www.bilibili.com/'
WEB_ORIGIN = 'https://www.bilibili.com'

# ════════════════════════════════════════════════════════════════════════════
# 登录态 cookie 名
# ════════════════════════════════════════════════════════════════════════════
CK_SESSDATA = 'SESSDATA'          # [确认] 主登录态,出现=登录成功最可靠信号
CK_CSRF = 'bili_jct'              # [确认] csrf token,所有写接口都要带
CK_UID = 'DedeUserID'             # [确认] 数字 UID

# ════════════════════════════════════════════════════════════════════════════
# 接口 HOST / 路径
# ════════════════════════════════════════════════════════════════════════════
MEMBER_HOST = 'https://member.bilibili.com'
API_HOST = 'https://api.bilibili.com'
VC_HOST = 'https://api.vc.bilibili.com'
PASSPORT_HOST = 'https://passport.bilibili.com'

# —— 身份/登录态校验 ——
PATH_NAV = '/x/web-interface/nav'                    # [确认] GET,cookie 即可,回 mid/uname/face(也带 wbi keys)

# —— 视频投稿链路(upos)——
PATH_PREUPLOAD = '/preupload'                        # [确认] GET member,申请 upos 上传点/auth/biz_id
PATH_V_COVER = '/x/vu/web/cover/up'                  # [确认] POST member,传封面拿 url
PATH_V_ADD = '/x/vu/web/add/v3'                      # [确认] POST member,提交投稿(csrf 在 query)

# —— 图文动态链路 ——
PATH_DYN_IMG_UPLOAD = '/x/dynamic/feed/draw/upload_bfs'   # [确认] POST api,传动态图拿 img_src
PATH_DYN_CREATE_DRAW = '/dynamic_svr/v1/dynamic_svr/create_draw'  # [推断/待校准] POST vc,发图文动态

# preupload 固定参数([确认] 值取自 web 端投稿;版本号随前端更新,可 env 覆盖)
PREUPLOAD_PROFILE = 'ugcupos/bup'
PREUPLOAD_R = 'upos'
UPOS_UPCDN = os.getenv('BILI_UPCDN', 'bda2')
UPOS_PROBE_VERSION = os.getenv('BILI_PROBE_VERSION', '20221109')
WEB_VERSION = os.getenv('BILI_WEB_VERSION', '2.14.0.0')
WEB_BUILD = os.getenv('BILI_WEB_BUILD', '2140000')

DEFAULT_CHUNK_SIZE = 10 * 1024 * 1024                # [确认] preupload 未回 chunk_size 时兜底 10MB

# ════════════════════════════════════════════════════════════════════════════
# 本地目录(data/ 下 accounts.json + profiles/ + qr/)
# ════════════════════════════════════════════════════════════════════════════
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # bilibili_publisher/
DATA_DIR = os.environ.get('BILIBILI_DATA_DIR', os.path.join(BASE_DIR, 'data'))
ACCOUNTS_FILE = os.environ.get('BILIBILI_ACCOUNTS_FILE', os.path.join(DATA_DIR, 'accounts.json'))
PROFILES_DIR = os.environ.get('BILIBILI_PROFILES_DIR', os.path.join(DATA_DIR, 'profiles'))
QR_DIR = os.path.join(DATA_DIR, 'qr')

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
API_TIMEOUT = _S.API_TIMEOUT
UPLOAD_TIMEOUT = _S.UPLOAD_TIMEOUT
LOGIN_TIMEOUT = _S.LOGIN_TIMEOUT
ProxyUnavailable = _S.ProxyUnavailable

# ════════════════════════════════════════════════════════════════════════════
# 代理:B站是境内站,默认走【北京出口】代理(跟小红书/快手同策略:本机 IP 号扛不住)
# ════════════════════════════════════════════════════════════════════════════
_BILI_PROXY = os.environ.get('BILIBILI_PROXY', '') or None


def resolve_proxy():
    """本平台这次用哪个代理。显式 BILIBILI_PROXY 最优先;否则走全局模式(默认 beijing=挑北京出口)。
    拿不到合格代理默认 raise ProxyUnavailable(宁可中止也别用本机 IP)。直连设 CRAWLER_PROXY_MODE=direct。"""
    if _BILI_PROXY:
        return _BILI_PROXY
    return _S.resolve_proxy()


# ════════════════════════════════════════════════════════════════════════════
# 分区 tid / 图文动态:未真机核对的取值门控(fail-loud,别拿占位盲发)
# ════════════════════════════════════════════════════════════════════════════
def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name, '') or '').strip() or default)
    except Exception:
        return default


# 视频分区 tid:B站投稿必填且无"安全默认"。给个常见默认(生活·日常 21)但要求显式确认。
# 真机确认想投的分区 tid 后设 --tid 或 BILI_TID=<真值>;分区表见 B站创作中心。
DEFAULT_TID = _env_int('BILI_TID', 21)          # [待校准] 21≈生活·日常(占位默认,建议显式 --tid)


def dynamic_calibrated() -> bool:
    """图文动态 create_draw 的字段/端点是否真机校准过。没校准就别裸发(白传图或被拦)。
    真机发一条确认 vc create_draw 字段后,设 BILI_DYN_CALIBRATED=1 放行。"""
    return (os.getenv('BILI_DYN_CALIBRATED', '') or '').strip().lower() in ('1', 'true', 'yes')
