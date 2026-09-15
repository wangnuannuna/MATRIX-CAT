# -*- coding: utf-8 -*-
"""
快手协议层——所有"怎么跟快手 cp API 打交道"的东西都在这：
  · 账号身份：从 storage_state 抽 cookie(api_ph/api_st/userId)，发布前 fail-fast；
  · 页内加签执行器：在账号自己的 CloakBrowser 发布页里跑 fetch(credentials:include)，
    让站点自己的请求拦截器把 __NS_sig3 追加到 URL(跟 xhs 页内 _webmsxyw 同思路)；
  · 素材上传网关：curl_cffi 直连 upload.kuaishouzt.com(只认 upload_token，不用 sig3)；
  · 发布编排：pre(取 token) → 分片传素材 → finish(拿 fileId/coverKey) → submit。

铁律/诚实标注(见 config 的 [确认]/[推断]/[待校准])：
  · cp/rest 接口需 cookie + __NS_sig3 双重鉴权。本层默认走"页内 fetch 让站点拦截器加签"，
    但【裸 fetch 是否被站点拦截器加签】是 [待校准] 头号未知项。第一次 cp/rest 写调用(pre)
    就是 sig3 的试金石：失败按 SigningNotReady 抛清晰诊断，绝不静默当成功。
  · fileId/coverKey/token 这类真值必须来自上传/finish 回执，缺了报错，不编造。

参考同构已落地：collectors/xhs_publisher/xhs/protocol.py。
"""
import asyncio
import json
import logging
import mimetypes
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import config, note
from storage.session_store import AccountStore

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# 账号身份：storage_state → cookie / api_ph / api_st
# ════════════════════════════════════════════════════════════════════════════
class AccountContractError(Exception):
    """账号态不满足直发契约(缺 api_ph/api_st/cookie)。发布前 fail-fast。"""


class PublishError(Exception):
    """发布链路任何一步的失败统一用它，CLI 一 catch 就能干净报错。"""
    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


class SigningNotReady(PublishError):
    """cp/rest 请求没带上 __NS_sig3(页内裸 fetch 可能没被站点拦截器加签)。这是 [待校准] 头号项：
    需真机确认站点拦截器是否对裸 fetch 生效；若只 hook 了 axios，要改成调页面 axios 实例或
    捞 webpack $encode 模块主动出签(见 SignedApiExecutor.sign_via_webpack 钩子)。
    是 PublishError 子类，所以 CLI 的 except PublishError 能统一接住给干净报错。"""


def _as_dict(cookie_data: Any) -> Dict[str, Any]:
    if not cookie_data:
        return {}
    if isinstance(cookie_data, str):
        try:
            return json.loads(cookie_data) or {}
        except Exception:
            return {}
    return cookie_data if isinstance(cookie_data, dict) else {}


def extract_ks_cookies(cookie_data: Any) -> Dict[str, str]:
    data = _as_dict(cookie_data)
    out: Dict[str, str] = {}
    for c in (data.get('cookies') or []):
        if 'kuaishou.com' in (c.get('domain') or ''):
            name = c.get('name')
            if name:
                out[name] = c.get('value', '')
    return out


def extract_identity(cookie_data: Any) -> Dict[str, Any]:
    cookies = extract_ks_cookies(cookie_data)
    return {'cookies': cookies,
            'api_ph': cookies.get(config.CK_LOGIN_PH, '') or '',
            'api_st': cookies.get(config.CK_LOGIN_ST, '') or '',
            'user_id': cookies.get(config.CK_USERID, '') or ''}


def assert_account_ready(cookie_data: Any) -> Dict[str, Any]:
    """发布前 fail-fast：缺 kuaishou.com cookie / api_ph / api_st 立刻报清晰错误，别裸发被判 401。"""
    ident = extract_identity(cookie_data)
    if not ident['cookies']:
        raise AccountContractError('账号态无 kuaishou.com 域 cookie：未登录或 storage_state 为空，请重新登录。')
    if not ident['api_ph']:
        raise AccountContractError(
            f'账号 cookie 缺 {config.CK_LOGIN_PH}(持久登录态)：它既是业务参数又是 __NS_sig3 输入，缺了必挂，请重登。')
    if not ident['api_st']:
        raise AccountContractError(
            f'账号 cookie 缺 {config.CK_LOGIN_ST}(登录 service-ticket)：短效登录态失效，请重登。')
    return ident


def get_store() -> AccountStore:
    return AccountStore(config.ACCOUNTS_FILE, cookie_domain=config.COOKIE_DOMAIN,
                        cookie_extractor=extract_ks_cookies)


# ════════════════════════════════════════════════════════════════════════════
# 页内加签执行器：在 cp.kuaishou.com 发布页里 fetch(credentials:include)，站点拦截器加 sig3
# ════════════════════════════════════════════════════════════════════════════
class ApiResp:
    __slots__ = ('status', 'json', 'text')

    def __init__(self, status: int, data: Any, text: str):
        self.status = status
        self.json = data
        self.text = text

    @property
    def ok(self) -> bool:
        return self.status == 200

    def result_ok(self) -> bool:
        """快手业务成功约定：result==1。"""
        d = self.json
        return isinstance(d, dict) and str(d.get('result')) == '1'


FETCH_ERROR = -2   # 页内 fetch 本身抛错(网络/CORS/被拦)

_INPAGE_FETCH_JS = r"""
async ({method, url, bodyStr, hasBody}) => {
  const headers = {};
  const opts = { method, credentials: 'include', headers };
  if (hasBody) {
    headers['content-type'] = 'application/json;charset=UTF-8';
    // 站点拦截器把 __NS_sig3 追加到 URL query，body 保持我们喂进去的原串(与签名同串)。
    opts.body = bodyStr;
  }
  let r;
  try { r = await fetch(url, opts); } catch (e) {
    return { status: -2, text: 'fetch_error: ' + (e && e.message || e), json: null };
  }
  const text = await r.text();
  let data = null; try { data = JSON.parse(text); } catch (e) {}
  return { status: r.status, text: text.slice(0, 4000), json: data };
}
"""

# 备胎(未实现)：若真机确认裸 fetch 不被加签，从 webpack require 缓存捞出 $encode 主动出签。
# $encode 是异步回调 s().call('$encode',[i,{suc,err}])，输入 i=m(t,e,n)(t=body串,e=api_ph,n={})；
# 模块号(逆向见到过 7606)会随打包变，必须按 '$encode'/'__NS_sig3' 字符串特征动态定位，别写死。
_SIGN_VIA_WEBPACK_JS = r"""
async ({bodyStr, apiPh}) => {
  return { ok: false, error: 'sign_via_webpack 未实现：需真机确认 $encode 模块定位方式' };
}
"""


class SignedApiExecutor:
    """page 必须已停在 cp.kuaishou.com 且站点 JS(含 sig3 拦截器)已 boot。"""

    def __init__(self, page: Any):
        self.page = page

    async def wait_ready(self, timeout_sec: float = 30.0) -> None:
        """等页面加载完 + 略作沉降，尽量保证站点的请求拦截器已装好。检测不到具体全局就退时间兜底。"""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                st = await self.page.evaluate("() => document.readyState")
                if st == 'complete':
                    break
            except Exception:
                pass
            await asyncio.sleep(0.4)
        await asyncio.sleep(1.2)  # 给拦截器/安全 SDK 一点 boot 时间

    async def call(self, method: str, path: str, *, body_obj: Optional[dict] = None,
                   host: str = config.CP_HOST, query: str = '') -> ApiResp:
        method = method.upper()
        has_body = body_obj is not None and method != 'GET'
        body_str = note.serialize(body_obj) if has_body else ''
        url = host + path + (query or '')
        res = await self.page.evaluate(_INPAGE_FETCH_JS, {
            'method': method, 'url': url, 'bodyStr': body_str, 'hasBody': has_body})
        return ApiResp(int(res.get('status', -9)), res.get('json'), res.get('text') or '')

    async def account_current(self, api_ph: str) -> ApiResp:
        """校验登录态 + 取昵称/uid/头像。body 带 api_ph(既是鉴权也可能是 sig3 输入)。"""
        return await self.call('POST', config.PATH_ACCOUNT,
                               body_obj={config.CK_LOGIN_PH: api_ph})

    async def sign_via_webpack(self, body_str: str, api_ph: str) -> Optional[str]:
        """[待实现钩子] 真机确认后填：捞 webpack $encode 主动出 sig3。现在恒返回 None。"""
        try:
            res = await self.page.evaluate(_SIGN_VIA_WEBPACK_JS, {'bodyStr': body_str, 'apiPh': api_ph})
            if res and res.get('ok'):
                return res.get('sig3')
        except Exception as e:
            logger.debug(f'sign_via_webpack 探测异常: {e}')
        return None


# ════════════════════════════════════════════════════════════════════════════
# 素材上传网关：curl_cffi 直连 upload.kuaishouzt.com(只认 upload_token，不需要 sig3)
# ════════════════════════════════════════════════════════════════════════════
class KsUploader:
    """分片/直传到快手上传网关。协议: resume 查询 / fragment 分片 / complete 合并；
    小文件走 direct。字节层不需要 sig3，鉴权全靠 upload_token。"""

    def __init__(self, session: Any):
        self._s = session

    def _req(self, method: str, url: str, *, data: Optional[bytes] = None,
             headers: Optional[dict] = None, timeout: int = config.UPLOAD_TIMEOUT):
        h = {'referer': config.REFERER, 'origin': config.ORIGIN,
             'user-agent': config.CURL_UA, 'accept': '*/*'}
        if headers:
            h.update(headers)
        return self._s.request(method, url, data=data, headers=h, timeout=timeout)

    async def upload(self, upload_token: str, data: bytes, *, filename: str = 'file') -> None:
        """把 data 传上去(网关按 upload_token 认这次上传)。小文件直传，大文件分片。失败抛。"""
        if len(data) <= config.SMALL_FILE_LIMIT:
            await asyncio.to_thread(self._upload_direct, upload_token, data)
        else:
            await asyncio.to_thread(self._upload_fragments, upload_token, data)
        logger.info(f'素材上传完成 {filename} ({len(data)} bytes)')

    @staticmethod
    def _ok(r, where: str) -> None:
        """判网关这步成没成。先看 HTTP，再看 JSON 业务码——网关返回体形态 [待校准]，
        所以只在【能解出 JSON 且带明确失败信号(result≠1 / error*)】时才判失败，不臆造成功格式、
        也别把 200+业务错误吞成成功(否则真正的错要拖到后面 finish 取不到 fileId 才暴露，定位偏移)。"""
        if r.status_code != 200:
            raise RuntimeError(f'{where}失败 HTTP {r.status_code}: {r.text[:160]}')
        try:
            j = r.json()
        except Exception:
            return  # 非 JSON(空体/纯文本 ok)：只认 HTTP 200
        if isinstance(j, dict):
            res = j.get('result')
            if res is not None and str(res) != '1':
                raise RuntimeError(f'{where}网关业务失败 result={res}: {str(j)[:160]}')
            if j.get('error') or j.get('error_msg') or (j.get('error_code') not in (None, 0, '0')):
                raise RuntimeError(f'{where}网关报错: {str(j)[:160]}')

    def _upload_direct(self, token: str, data: bytes) -> None:
        url = f'{config.UPLOAD_HOST}{config.PATH_UP_DIRECT}?upload_token={token}'
        r = self._req('POST', url, data=data,
                      headers={'content-type': 'application/octet-stream'})
        self._ok(r, '素材直传')

    def _upload_fragments(self, token: str, data: bytes) -> None:
        size = config.FRAGMENT_SIZE
        chunks = [data[off:off + size] for off in range(0, len(data), size)]
        for i, chunk in enumerate(chunks):
            url = (f'{config.UPLOAD_HOST}{config.PATH_UP_FRAGMENT}'
                   f'?upload_token={token}&fragment_id={i}')
            r = self._req('POST', url, data=chunk,
                          headers={'content-type': 'application/octet-stream'})
            self._ok(r, f'分片 {i}/{len(chunks)} 上传')
        curl = (f'{config.UPLOAD_HOST}{config.PATH_UP_COMPLETE}'
                f'?upload_token={token}&fragment_count={len(chunks)}')
        r = self._req('POST', curl, data=b'')
        self._ok(r, '分片合并 complete')


def build_uploader(proxy: Optional[str] = None) -> KsUploader:
    """起一个带 Chrome TLS 指纹的 curl_cffi 会话，包成 KsUploader。trust_env=False 不吃系统 VPN。"""
    from curl_cffi import requests as cffi
    sess = cffi.Session(impersonate=config.CURL_IMPERSONATE, trust_env=False,
                        proxies=({'http': proxy, 'https': proxy} if proxy else None))
    return KsUploader(sess)


# ════════════════════════════════════════════════════════════════════════════
# 发布编排积木(给 publish_image / publish_video 调)
# ════════════════════════════════════════════════════════════════════════════
_COOLDOWN = {403: 3600, 429: 1800, 412: 3600}   # 风控/被拦 → 冷却秒数


def _sig3_diagnostic(resp: ApiResp, where: str) -> None:
    """cp/rest 调用失败时，判断是不是 sig3 没加上(页内裸 fetch 未被拦截器加签)，给出可行动诊断。

    注意：风控码(_COOLDOWN 里那几个)另有冷却处理路径，别在这抢先按'缺签名'拦——调用方应先落冷却再调本函数。
    """
    if resp.status == FETCH_ERROR:
        raise PublishError(f'{where}: 页内 fetch 异常(网络/被拦): {resp.text[:160]}', resp.status, resp.text)
    # 常见"缺签名"的迹象：非 200(且非风控码)、文案带 sig/风控/token 字样。
    txt = (resp.text or '').lower()
    looks_sig = any(k in txt for k in ('sig', 'nssig', '__ns', 'forbidden', 'token'))
    if not resp.ok and resp.status not in _COOLDOWN and looks_sig:
        raise SigningNotReady(
            f'{where}: 请求疑似缺 __NS_sig3 被拦(status={resp.status})。页内裸 fetch 可能没被站点拦截器加签，'
            f'这是 [待校准] 头号项——需真机确认签名注入方式(见 SignedApiExecutor.sign_via_webpack)。'
            f'响应: {resp.text[:160]}')


def _content_type(path: str, default: str) -> str:
    return mimetypes.guess_type(path)[0] or default


def _read_bytes(path: str) -> bytes:
    with open(path, 'rb') as f:
        return f.read()


def probe_video_meta(path: str) -> Dict[str, Any]:
    """best-effort 探视频宽高/时长(有 cv2 就用，没有退空占位，不阻断)。"""
    try:
        import cv2  # type: ignore
        cap = cv2.VideoCapture(path)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        cap.release()
        out: Dict[str, Any] = {}
        if w:
            out['width'] = w
        if h:
            out['height'] = h
        return out
    except Exception as e:
        logger.debug(f'视频探测跳过(无 cv2 或读失败): {e}')
        return {}


async def resolve_proxy(explicit) -> Optional[str]:
    """这次发布走哪个代理：显式优先；否则按全局模式(默认北京出口)现取。拿不到转 PublishError。"""
    if explicit is not None:
        return explicit
    try:
        return await asyncio.to_thread(config.resolve_proxy)
    except config.ProxyUnavailable as e:
        raise PublishError(str(e))


def resolve_account(store: AccountStore, account_id: str):
    acc = store.get(account_id)
    if not acc:
        raise PublishError(f'账号 {account_id} 不存在，请先 login')
    storage_state = acc.get('storage_state') or {}
    try:
        assert_account_ready(storage_state)
    except AccountContractError as e:
        raise PublishError(str(e))
    cooling = store.is_cooling(account_id)
    if cooling:
        raise PublishError(f'账号 {account_id} 冷却中，还需 {cooling}s')
    return acc, storage_state


async def get_upload_token(ex: SignedApiExecutor, api_ph: str, *, path: str) -> str:
    """申请上传 token(path=视频 PATH_V_PRE / 图文 PATH_A_PRE)。返回 upload_token。"""
    resp = await ex.call('POST', path, body_obj={'uploadType': 1, config.CK_LOGIN_PH: api_ph})
    _sig3_diagnostic(resp, f'申请上传token {path}')
    if not resp.ok or not isinstance(resp.json, dict):
        raise PublishError(f'申请上传token失败 status={resp.status}: {resp.text[:160]}', resp.status, resp.text)
    data = resp.json.get('data') or resp.json
    token = data.get('token') or data.get('uploadToken') or data.get('upload_token')
    if not token:
        raise PublishError(f'上传token响应无 token 字段: {str(resp.json)[:200]}')
    return token


async def finish_media(ex: SignedApiExecutor, api_ph: str, token: str, path: str, *,
                       finish_path: str = config.PATH_V_FINISH,
                       file_type: str = 'video') -> Dict[str, Any]:
    """finish 回传元数据，拿 fileId/coverKey/mediaId。finish_path 按视频/图文各传各的(别写死)。"""
    body = {
        'fileLength': os.path.getsize(path),
        'fileName': os.path.basename(path),
        'fileType': file_type,
        'token': token,
        config.CK_LOGIN_PH: api_ph,
    }
    resp = await ex.call('POST', finish_path, body_obj=body)
    _sig3_diagnostic(resp, 'finish 回传元数据')
    if not resp.ok or not isinstance(resp.json, dict):
        raise PublishError(f'finish 失败 status={resp.status}: {resp.text[:160]}', resp.status, resp.text)
    data = resp.json.get('data') or resp.json
    file_id = data.get('fileId') or data.get('file_id')
    if not file_id:
        raise PublishError(f'finish 响应无 fileId: {str(resp.json)[:200]}')
    return {'file_id': file_id,
            'cover_key': data.get('coverKey') or data.get('cover_key') or '',
            'media_id': data.get('mediaId') or data.get('media_id') or ''}


async def submit_work(ex: SignedApiExecutor, submit_path: str, body: Dict[str, Any],
                      api_ph: str, store: AccountStore, account_id: str) -> Dict[str, Any]:
    """提交发布(视频 PATH_V_SUBMIT / 图文 PATH_A_SUBMIT)。风控码落冷却。返回 {work_id,url,raw}。"""
    body = dict(body)
    body[config.CK_LOGIN_PH] = api_ph      # 登录态既是 cookie 又作 body 参数回传
    resp = await ex.call('POST', submit_path, body_obj=body)
    # 风控码优先落冷却(别被 sig3 启发式抢先误判成'签名未就绪'而跳过冷却→立即重试撞封)。
    if resp.status in _COOLDOWN:
        store.cool_down(account_id, _COOLDOWN[resp.status], reason=f'submit http_{resp.status}')
        raise PublishError(f'提交发布触发风控 http_{resp.status}', resp.status, resp.text)
    _sig3_diagnostic(resp, '提交发布')
    if not resp.ok:
        raise PublishError(f'提交发布失败 status={resp.status}: {resp.text[:200]}', resp.status, resp.text)
    if not resp.result_ok():
        raise PublishError(f'提交发布业务失败(result≠1): {resp.text[:200]}', resp.status, resp.text)
    data = resp.json.get('data') or {}
    work_id = (data.get('photoId') or data.get('photo_id') or data.get('workId')
               or data.get('id') or '')
    url = f'https://www.kuaishou.com/short-video/{work_id}' if work_id else None
    return {'work_id': work_id, 'url': url, 'raw': resp.json}


async def human_pause() -> None:
    """素材传完到点"发布"之间拟人停顿，别几百毫秒打完像机器。"""
    await asyncio.sleep(1.5 + (time.time() % 1) * 2)


# ════════════════════════════════════════════════════════════════════════════
# 登录态校验(给 run.py refresh / login 复用)
# ════════════════════════════════════════════════════════════════════════════
async def check_login(account_id: str, storage_state: Dict[str, Any], *,
                      proxy: Optional[str] = None) -> Tuple[bool, Dict[str, Any]]:
    """开个无头会话，用账号态调 account/current，确认登录态还活着并取昵称/uid/头像。"""
    from shared import browser
    ident = extract_identity(storage_state)
    if not ident['api_ph']:
        return False, {'error': f'缺 {config.CK_LOGIN_PH}，请重新登录'}
    sess = await browser.open_session(
        account_id, profiles_dir=config.PROFILES_DIR, goto_url=config.HOME_URL,
        scene='sign', storage_state=storage_state, proxy=proxy, headless=True)
    try:
        ex = SignedApiExecutor(sess.page)
        await ex.wait_ready()
        resp = await ex.account_current(ident['api_ph'])
        if resp.result_ok():
            d = resp.json.get('data') or {}
            return True, {'nickname': d.get('userName'), 'user_id': d.get('userId'),
                          'avatar': d.get('userAvatar')}
        return False, {'error': f'account/current 非 result==1 (status={resp.status}): {resp.text[:120]}'}
    except Exception as e:
        return False, {'error': str(e)}
    finally:
        await sess.aclose()
