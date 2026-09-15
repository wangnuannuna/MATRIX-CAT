# -*- coding: utf-8 -*-
"""快手创作者平台(cp.kuaishou.com)常量。

**全局可调项(UA / CloakBrowser / curl 指纹 / 代理 / 超时)在项目根 `config/settings.py`**，
这里只留快手**专属**的(域名/接口路径/cookie 口径/本地目录)，其余从全局配置 re-export。

置信度标注(照抄自协议调研，别当成都验证过了)：
  [确认]   开源代码/多篇独立逆向互证
  [推断]   与视频链路同构类推，没见到确切字符串
  [待校准] 必须真机抓包，代码里对应处 fail-loud，禁止拿占位值静默上线
参考已落地同构实现：collectors/xhs_publisher/xhs/config.py。
"""
import os
import sys

# 本文件在 collectors/kuaishou_publisher/kuaishou/，往上四级 = 项目根，让 `from config import settings` 找得到
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import settings as _S  # noqa: E402  全局配置

PLATFORM = 'kuaishou'
COOKIE_DOMAIN = 'kuaishou.com'          # [确认] setRootDomain=true 把登录态种到根域 .kuaishou.com

# ════════════════════════════════════════════════════════════════════════════
# 页面 URL
# ════════════════════════════════════════════════════════════════════════════
# 未登录直接开发布页，cp 会自动 302 到 passport 扫码页(sid=kuaishou.web.cp.api)；
# 已登录(持久 profile 里有态)则停在发布页。所以登录/发布用同一个入口 URL 最省事。
CP_HOST = 'https://cp.kuaishou.com'
LOGIN_URL = 'https://cp.kuaishou.com/article/publish/video'          # [确认] 未登录自动跳 passport
HOME_URL = 'https://cp.kuaishou.com/article/publish/video'           # [确认] 视频发布页(签名环境就绪处)
HOME_URL_PIC = 'https://cp.kuaishou.com/article/publish/video?tabType=1'  # [确认] 图文同页切 tab
MANAGE_OK_URL = 'https://cp.kuaishou.com/article/manage/video?status=2'   # [确认] 发布成功跳转判据

REFERER = 'https://cp.kuaishou.com/'
ORIGIN = 'https://cp.kuaishou.com'

# ════════════════════════════════════════════════════════════════════════════
# 登录态 cookie 名(sid=kuaishou.web.cp.api → 前缀 kuaishou.web.cp.api_)
# ════════════════════════════════════════════════════════════════════════════
CK_LOGIN_ST = 'kuaishou.web.cp.api_st'   # [确认] 短效 service-ticket，出现=登录成功最可靠信号
CK_LOGIN_PH = 'kuaishou.web.cp.api_ph'   # [确认] 持久标识；既是 cookie 又作业务参数回传，还是 sig3 输入
CK_USERID = 'userId'                     # [确认]

# ════════════════════════════════════════════════════════════════════════════
# REST 业务接口(都在 cp.kuaishou.com，都要 __NS_sig3；见 protocol 的页内加签)
# ════════════════════════════════════════════════════════════════════════════
PATH_ACCOUNT = '/rest/pc/authority/account/current'        # [确认] 账号态校验 + 取昵称/uid/头像

# 视频发布链路(全 [确认])
PATH_V_PRE = '/rest/cp/works/v2/video/pc/upload/pre'        # 申请 upload token
PATH_V_FINISH = '/rest/cp/works/v2/video/pc/upload/finish'  # 回传元数据拿 fileId/coverKey
PATH_V_COVER = '/rest/cp/works/v2/video/pc/upload/cover/upload'  # 上传自定义封面
PATH_V_SUBMIT = '/rest/cp/works/v2/video/pc/submit'        # 提交发布
PATH_V_LIST = '/rest/cp/works/v2/video/pc/photo/list'      # 作品列表(带 sig3 样例，用来探签名)

# 图文/图集链路 —— 路径全是 [推断]，开源里只有视频版。默认门控关闭，见 ATLAS_CALIBRATED。
PATH_A_PRE = os.getenv('KS_ATLAS_PRE', '/rest/cp/works/v2/atlas/pc/upload/pre')       # [推断]
PATH_A_FINISH = os.getenv('KS_ATLAS_FINISH', '/rest/cp/works/v2/atlas/pc/upload/finish')  # [推断]
PATH_A_SUBMIT = os.getenv('KS_ATLAS_SUBMIT', '/rest/cp/works/v2/atlas/pc/submit')     # [推断]


def atlas_calibrated() -> bool:
    """图文 atlas 协议路径/字段是否已真机校准。没校准就别拿推断端点裸发(会白传素材或被拦)。
    真机抓包确认 atlas 版 pre/finish/submit 路径 + 图片 key 字段后，设 KS_ATLAS_CALIBRATED=1 放行。"""
    return (os.getenv('KS_ATLAS_CALIBRATED', '') or '').strip().lower() in ('1', 'true', 'yes')


# ════════════════════════════════════════════════════════════════════════════
# 独立上传网关(非 cp 域；分片协议与开放平台一致，只认 upload_token，不需要 sig3)
# ════════════════════════════════════════════════════════════════════════════
UPLOAD_HOST = 'https://upload.kuaishouzt.com'              # [确认] CP web 端固定
PATH_UP_RESUME = '/api/upload/resume'                      # [确认] GET  ?upload_token=
PATH_UP_FRAGMENT = '/api/upload/fragment'                  # [确认] POST ?upload_token=&fragment_id=
PATH_UP_COMPLETE = '/api/upload/complete'                  # [确认] POST ?upload_token=&fragment_count=
PATH_UP_DIRECT = '/api/upload'                             # [确认] POST ?upload_token=  小文件直传
FRAGMENT_SIZE = 4 * 1024 * 1024                            # [确认] 4MB=4194304
SMALL_FILE_LIMIT = 10 * 1024 * 1024                        # <10MB 走直传，否则分片

# ════════════════════════════════════════════════════════════════════════════
# 本地目录(data/ 下 accounts.json + profiles/ + qr/)
# ════════════════════════════════════════════════════════════════════════════
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # kuaishou_publisher/
DATA_DIR = os.environ.get('KUAISHOU_DATA_DIR', os.path.join(BASE_DIR, 'data'))
ACCOUNTS_FILE = os.environ.get('KUAISHOU_ACCOUNTS_FILE', os.path.join(DATA_DIR, 'accounts.json'))
PROFILES_DIR = os.environ.get('KUAISHOU_PROFILES_DIR', os.path.join(DATA_DIR, 'profiles'))
QR_DIR = os.path.join(DATA_DIR, 'qr')

# ════════════════════════════════════════════════════════════════════════════
# 从全局配置 re-export(UA / CloakBrowser / curl / 时区语言 / 超时)
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
# 代理：快手是境内站，默认走【北京出口】代理(跟小红书同策略：本机 IP 号扛不住)
# ════════════════════════════════════════════════════════════════════════════
_KS_PROXY = os.environ.get('KUAISHOU_PROXY', '') or None


def resolve_proxy():
    """本平台这次用哪个代理。显式 KUAISHOU_PROXY 最优先；否则走全局模式(默认 beijing=挑北京出口)。
    拿不到合格代理默认 raise ProxyUnavailable(宁可中止也别用本机 IP)。直连设 CRAWLER_PROXY_MODE=direct。"""
    if _KS_PROXY:
        return _KS_PROXY
    return _S.resolve_proxy()
