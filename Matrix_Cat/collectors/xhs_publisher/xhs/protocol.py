# -*- coding: utf-8 -*-
"""
小红书协议层——所有"怎么跟小红书 API 打交道"的东西都在这：
  · 账号身份：从 storage_state 抽 cookie/a1/b1，发布前 fail-fast；
  · 页内签名执行器：在账号自己的 CloakBrowser 页里用 window._webmsxyw 出签 + 页内 fetch 直发；
  · 素材直传 COS：curl_cffi 纯协议直连(带 Chrome TLS 指纹)；
  · 发布编排：permit → 传素材 → create_note，含风控冷却。

publish_image.py / publish_video.py 只负责"组织素材 + 拼 note + 调这里的积木"，薄薄一层。
账号存储用框架层 storage.session_store.AccountStore；浏览器会话用 shared.browser。
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
# 账号身份：storage_state → cookie / a1 / b1
# ════════════════════════════════════════════════════════════════════════════
# a1 参与 x-s 计算，必须跟签名同源(账号自己的 cookie)；b1 参与 x-s-common。
# 这里只读真实账号已有的指纹，绝不生成/编造——取不到就报错，让上层去修登录。

class AccountContractError(Exception):
    """账号态不满足协议直发契约(缺 a1/b1/cookie)。发布前 fail-fast。"""


def _as_dict(cookie_data: Any) -> Dict[str, Any]:
    if not cookie_data:
        return {}
    if isinstance(cookie_data, str):
        try:
            return json.loads(cookie_data) or {}
        except Exception:
            return {}
    return cookie_data if isinstance(cookie_data, dict) else {}


def extract_xhs_cookies(cookie_data: Any) -> Dict[str, str]:
    data = _as_dict(cookie_data)
    out: Dict[str, str] = {}
    for c in (data.get('cookies') or []):
        if 'xiaohongshu.com' in (c.get('domain') or ''):
            name = c.get('name')
            if name:
                out[name] = c.get('value', '')
    return out


def extract_b1(cookie_data: Any) -> str:
    data = _as_dict(cookie_data)
    for origin in (data.get('origins') or []):
        if 'xiaohongshu.com' in (origin.get('origin') or ''):
            for item in (origin.get('localStorage') or []):
                if item.get('name') == 'b1':
                    return item.get('value') or ''
    return ''


def extract_identity(cookie_data: Any) -> Dict[str, Any]:
    cookies = extract_xhs_cookies(cookie_data)
    return {'cookies': cookies, 'a1': cookies.get('a1', '') or '', 'b1': extract_b1(cookie_data)}


def assert_account_ready(cookie_data: Any, *, require_b1: bool = True) -> Dict[str, Any]:
    """发布前 fail-fast：缺 cookie/a1(或 b1) 立刻报清晰错误，别带着空指纹裸发被判 461。"""
    ident = extract_identity(cookie_data)
    if not ident['cookies']:
        raise AccountContractError('账号态无 xiaohongshu.com 域 cookie：未登录或 storage_state 为空，请重新登录。')
    if not ident['a1']:
        raise AccountContractError('账号 cookie 缺 a1(设备指纹)：a1 参与 x-s 签名，缺了必被判 461，请重新登录。')
    if require_b1 and not ident['b1']:
        raise AccountContractError('账号 storage_state 缺 b1(localStorage 设备指纹)：b1 参与 x-s-common，'
                                   '写接口缺它易被判"头被篡改"，请确认登录时已写入 localStorage.b1。')
    return ident


def get_store() -> AccountStore:
    """本平台的账号存储(框架通用 AccountStore + 小红书 cookie 域)。"""
    return AccountStore(config.ACCOUNTS_FILE, cookie_domain=config.COOKIE_DOMAIN,
                        cookie_extractor=extract_xhs_cookies)


# ════════════════════════════════════════════════════════════════════════════
# 页内签名执行器：window._webmsxyw 出签 + 页内 fetch 直发
# ════════════════════════════════════════════════════════════════════════════
# 铁律：喂给 _webmsxyw 的 body 串必须跟 fetch 发出去的一字不差。上层用 note.serialize 出唯一串，
# 这里 JSON.parse 回对象喂签名、再 JSON.stringify 同引擎出串发送，字节严丝合缝(float 0.0→0 也归一)。

SIGN_ERROR = -1     # _webmsxyw 抛错(签名函数异常/页面被风控/登录态失效)
FETCH_ERROR = -2    # fetch 本身抛错(网络/CORS/被拦)
NO_COMMON = -3      # 写接口缺 X-S-Common，裸发必 461，提前 fail-fast


class ApiResp:
    __slots__ = ('status', 'json', 'text', 'has_common')

    def __init__(self, status: int, data: Any, text: str, has_common: bool = True):
        self.status = status
        self.json = data
        self.text = text
        self.has_common = has_common

    @property
    def ok(self) -> bool:
        return self.status == 200

    def success_body(self) -> bool:
        d = self.json
        return isinstance(d, dict) and d.get('success') is True and str(d.get('code', '0')) == '0'


_INPAGE_FETCH_JS = r"""
async ({method, url, signPath, bodyStr, hasBody, injectedCommon, requireCommon}) => {
  const dataForSign = hasBody ? JSON.parse(bodyStr) : {};
  let sign = {};
  try { sign = await window._webmsxyw(signPath, dataForSign); } catch (e) {
    return { status: -1, text: 'sign_error: ' + (e && e.message || e), json: null, hasCommon: false };
  }
  const headers = { 'content-type': 'application/json;charset=UTF-8' };
  if (sign['X-s'] != null) headers['X-s'] = sign['X-s'];
  if (sign['X-t'] != null) headers['X-t'] = String(sign['X-t']);
  let common = sign['X-S-Common'] || sign['X-s-common'] || sign['x-s-common'];
  if (!common && injectedCommon) common = injectedCommon;
  if (common) headers['X-S-Common'] = common;
  if (requireCommon && !common) {
    return { status: -3, text: 'no_x_s_common: 写接口缺 X-S-Common(会 461)', json: null, hasCommon: false };
  }
  const opts = { method, credentials: 'include', headers };
  // 铁律加固：发送体也用页内重序列化的串(JSON.stringify)，与 _webmsxyw 内部序列化同一个 JS 引擎，
  // float 归一(0.0→0)、数字/转义两边恒等，杜绝"Python 出的串"与"JS 签的串"字节不一致。
  if (hasBody) opts.body = JSON.stringify(dataForSign);
  let r;
  try { r = await fetch(url, opts); } catch (e) {
    return { status: -2, text: 'fetch_error: ' + (e && e.message || e), json: null, hasCommon: !!common };
  }
  const text = await r.text();
  let data = null; try { data = JSON.parse(text); } catch (e) {}
  return { status: r.status, text: text.slice(0, 4000), json: data, hasCommon: !!common };
}
"""

_HARVEST_COMMON_JS = r"""
async () => {
  try {
    const g = window.__XHS_LAST_X_S_COMMON__ || (window.__NEXT_DATA__ && window.__NEXT_DATA__.props && window.__NEXT_DATA__.props.xsCommon);
    if (g && typeof g === 'string' && g.length > 16) return g;
  } catch (e) {}
  return await new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v || ''); } };
    try {
      const proto = XMLHttpRequest.prototype;
      const orig = proto.setRequestHeader;
      proto.setRequestHeader = function (k, v) {
        try { if (String(k).toLowerCase() === 'x-s-common' && v) finish(v); } catch (e) {}
        return orig.apply(this, arguments);
      };
      setTimeout(() => { try { proto.setRequestHeader = orig; } catch (e) {} finish(''); }, 5000);
    } catch (e) { finish(''); }
  });
}
"""

_SIGN_PROBE_JS = r"""
async ({signPath, bodyStr, hasBody}) => {
  const dataForSign = hasBody ? JSON.parse(bodyStr) : {};
  try {
    const sign = await window._webmsxyw(signPath, dataForSign);
    const common = sign['X-S-Common'] || sign['X-s-common'] || sign['x-s-common'] || '';
    return { ok: true, xs: String(sign['X-s'] || ''), xt: String(sign['X-t'] || ''),
             hasCommon: !!common, error: '' };
  } catch (e) {
    return { ok: false, xs: '', xt: '', hasCommon: false, error: String(e && e.message || e) };
  }
}
"""


class SignedApiExecutor:
    """page 必须已停在 creator.xiaohongshu.com 且 window._webmsxyw 就绪(带账号 cookie)。"""

    def __init__(self, page: Any):
        self.page = page
        self._harvested_common: str = ''

    async def harvest_common(self) -> str:
        try:
            v = await self.page.evaluate(_HARVEST_COMMON_JS)
            if v and isinstance(v, str):
                self._harvested_common = v
            return self._harvested_common
        except Exception as e:
            logger.warning(f'旁路 harvest X-S-Common 失败: {e}')
            return ''

    async def sign_probe(self, method: str, path: str, *, query: str = '',
                         body_obj: Optional[dict] = None) -> Dict[str, Any]:
        method = method.upper()
        has_body = body_obj is not None and method != 'GET'
        body_str = json.dumps(body_obj, ensure_ascii=False, separators=(',', ':')) if has_body else ''
        sign_path = self._sign_path(method, path, query)
        try:
            res = await self.page.evaluate(_SIGN_PROBE_JS, {
                'signPath': sign_path, 'bodyStr': body_str, 'hasBody': has_body})
        except Exception as e:
            return {'ok': False, 'xs': '', 'xt': '', 'has_common': False, 'error': str(e)}
        return {'ok': bool(res.get('ok')), 'xs': res.get('xs') or '',
                'xt': res.get('xt') or '', 'has_common': bool(res.get('hasCommon')),
                'error': res.get('error') or ''}

    async def reload(self) -> bool:
        try:
            await self.page.reload(wait_until='domcontentloaded')
            for _ in range(40):
                try:
                    if await self.page.evaluate("() => typeof window._webmsxyw === 'function'"):
                        return True
                except Exception:
                    pass
                await self.page.wait_for_timeout(500)
        except Exception as e:
            logger.warning(f'page.reload 失败: {e}')
        return False

    def _sign_path(self, method: str, path: str, query: str) -> str:
        if method == 'GET':
            return path + (query or '') if config.get_sign_uri_mode() == 'path_query' else path
        return path

    async def call(self, method: str, host: str, path: str, *,
                   body_str: Optional[str] = None, query: str = '',
                   require_common: bool = False) -> ApiResp:
        method = method.upper()
        has_body = body_str is not None and method != 'GET'
        sign_path = self._sign_path(method, path, query)
        url = host + path + (query or '')
        res = await self.page.evaluate(_INPAGE_FETCH_JS, {
            'method': method, 'url': url, 'signPath': sign_path,
            'bodyStr': body_str or '', 'hasBody': has_body,
            'injectedCommon': self._harvested_common or '',
            'requireCommon': bool(require_common),
        })
        return ApiResp(int(res.get('status', -9)), res.get('json'),
                       res.get('text') or '', has_common=bool(res.get('hasCommon', True)))


# ════════════════════════════════════════════════════════════════════════════
# 素材直传对象存储(腾讯 COS)：curl_cffi 直连，不需要小红书验签，只要 permit 的 token
# ════════════════════════════════════════════════════════════════════════════

PutFn = Callable[[str, str, Dict[str, str], Optional[bytes], int], Tuple[int, Dict[str, str], str]]


def parse_permit(permit: Dict[str, Any]) -> Dict[str, str]:
    """从 permit 响应/单节点取 {file_id, token, upload_host}。upload_host 归一成裸 host。"""
    if not permit:
        raise RuntimeError('permit 为空')
    node = permit
    data = permit.get('data', permit) if isinstance(permit, dict) else permit
    if isinstance(data, dict):
        permits = (data.get('uploadTempPermits') or data.get('upload_temp_permits')
                   or data.get('permits'))
        if permits:
            node = permits[0]
        elif 'fileIds' in data or 'file_ids' in data or 'fileId' in data or 'file_id' in data:
            node = data
    file_ids = node.get('fileIds') or node.get('file_ids') or []
    file_id = file_ids[0] if file_ids else (node.get('fileId') or node.get('file_id'))
    if not file_id:
        raise RuntimeError(f'permit 无 fileIds/fileId: {str(node)[:200]}')
    host = (node.get('uploadAddr') or node.get('upload_addr')
            or node.get('uploadHost') or node.get('upload_host') or '')
    host = host.replace('https://', '').replace('http://', '').strip('/')
    token = node.get('token') or node.get('x-cos-security-token') or ''
    return {'file_id': file_id, 'token': token, 'upload_host': host}


def _obj_url(upload_host: str, file_id: str) -> str:
    if upload_host.startswith('http'):
        return f'{upload_host.rstrip("/")}/{file_id}'
    return f'https://{upload_host}/{file_id}'


def _preview_url(resp_headers: Dict[str, str]) -> Optional[str]:
    if not resp_headers:
        return None
    return (resp_headers.get('x-ros-preview-url') or resp_headers.get('X-Ros-Preview-Url')
            or resp_headers.get('X-ROS-Preview-Url'))


def _cos_headers(token: str, content_type: str = '') -> Dict[str, str]:
    return {'x-cos-security-token': token, 'referer': config.REFERER,
            'origin': config.ORIGIN, 'content-type': content_type or ''}


def _browser_header_decorator(ua: str) -> Callable[[Dict[str, str]], Dict[str, str]]:
    """给 COS 请求补真浏览器直传会带的 sec-* 头。UA/sec-ch-ua 跟 curl TLS 模拟目标版本自洽。"""
    def _decorate(h: Dict[str, str]) -> Dict[str, str]:
        h = dict(h)
        h.setdefault('user-agent', ua)
        h.setdefault('accept', '*/*')
        h.setdefault('accept-language', 'zh-CN,zh;q=0.9')
        h.setdefault('sec-ch-ua', config.CURL_CH_UA)
        h.setdefault('sec-ch-ua-mobile', '?0')
        h.setdefault('sec-ch-ua-platform', '"Windows"')
        h.setdefault('sec-fetch-dest', 'empty')
        h.setdefault('sec-fetch-mode', 'cors')
        h.setdefault('sec-fetch-site', 'cross-site')
        return h
    return _decorate


class CosClient:
    def __init__(self, put_fn: PutFn, *,
                 header_decorator: Optional[Callable[[Dict[str, str]], Dict[str, str]]] = None):
        self._put = put_fn
        self._decorate = header_decorator or (lambda h: h)

    def _headers(self, token: str, content_type: str = '') -> Dict[str, str]:
        return self._decorate(_cos_headers(token, content_type))

    async def _do(self, method: str, url: str, headers: Dict[str, str],
                  body: Optional[bytes], timeout: int) -> Tuple[int, Dict[str, str], str]:
        return await asyncio.to_thread(self._put, method, url, headers, body, timeout)

    async def put_object(self, upload_host: str, file_id: str, data: bytes, token: str, *,
                         content_type: str = '', timeout: int = config.UPLOAD_TIMEOUT
                         ) -> Optional[str]:
        url = _obj_url(upload_host, file_id)
        st, rh, text = await self._do('PUT', url, self._headers(token, content_type), data, timeout)
        if st not in (200, 204):
            raise RuntimeError(f'对象存储上传失败 HTTP {st} @ {file_id}: {text[:160]}')
        logger.info(f'素材上传成功 file_id={file_id}')
        return _preview_url(rh)

    async def put_object_multipart(self, upload_host: str, file_id: str, data: bytes, token: str, *,
                                   part_size: int = 4 * 1024 * 1024, concurrency: int = 4,
                                   retries: int = 2, content_type: str = 'video/mp4',
                                   part_timeout: int = config.UPLOAD_TIMEOUT) -> str:
        import base64
        import hashlib
        import re
        base = _obj_url(upload_host, file_id)
        st, _rh, txt = await self._do('POST', f'{base}?uploads', self._headers(token, content_type), b'', 60)
        if st != 200:
            raise RuntimeError(f'视频 init 失败 HTTP {st}: {txt[:160]}')
        m = re.search(r'<UploadId>([^<]+)</UploadId>', txt)
        if not m:
            raise RuntimeError(f'视频 init 无 UploadId: {txt[:160]}')
        upload_id = m.group(1)

        chunks = [(i + 1, data[off:off + part_size])
                  for i, off in enumerate(range(0, len(data), part_size))]
        sem = asyncio.Semaphore(max(1, concurrency))
        hdr = self._headers(token)

        async def _one(part_no: int, chunk: bytes) -> Tuple[int, str]:
            purl = f'{base}?partNumber={part_no}&uploadId={upload_id}'
            last = ''
            for _ in range(max(1, retries)):
                try:
                    st2, rh2, txt2 = await self._do('PUT', purl, hdr, chunk, part_timeout)
                    if st2 == 200:
                        etag = (rh2.get('ETag') or rh2.get('etag') or '').strip()
                        if etag:
                            return part_no, etag
                        last = 'ETag 缺失'
                    else:
                        last = f'HTTP {st2}: {txt2[:120]}'
                except Exception as e:
                    last = str(e)[:120]
            raise RuntimeError(f'视频分片 {part_no} 失败: {last}')

        async def _guarded(pn: int, c: bytes) -> Tuple[int, str]:
            async with sem:
                return await _one(pn, c)

        results = list(await asyncio.gather(*[_guarded(pn, c) for pn, c in chunks]))
        results.sort(key=lambda x: x[0])
        parts_xml = ''.join(f'<Part><PartNumber>{n}</PartNumber><ETag>{e}</ETag></Part>'
                            for n, e in results)
        xml = f'<CompleteMultipartUpload>{parts_xml}</CompleteMultipartUpload>'.encode('utf-8')
        ch = self._headers(token)
        ch['Content-MD5'] = base64.b64encode(hashlib.md5(xml).digest()).decode()
        st3, _rh3, txt3 = await self._do('POST', f'{base}?uploadId={upload_id}', ch, xml, 120)
        if st3 != 200:
            raise RuntimeError(f'视频 complete 失败 HTTP {st3}: {txt3[:160]}')
        logger.info(f'视频分片上传成功 file_id={file_id}')
        return file_id

    async def upload_image(self, permit_node: Dict[str, Any], data: bytes, *,
                           content_type: str = 'image/jpeg',
                           size: Optional[Tuple[int, int]] = None) -> Dict[str, Any]:
        info = parse_permit(permit_node)
        preview = await self.put_object(info['upload_host'], info['file_id'], data,
                                        info['token'], content_type=content_type)
        w, h = size or (0, 0)
        return {'file_id': info['file_id'], 'width': w, 'height': h, 'preview_url': preview}

    async def upload_video(self, permit_node: Dict[str, Any], video_bytes: bytes, *,
                           part_size: int = 4 * 1024 * 1024, concurrency: int = 4) -> str:
        info = parse_permit(permit_node)
        return await self.put_object_multipart(
            info['upload_host'], info['file_id'], video_bytes, info['token'],
            part_size=part_size, concurrency=concurrency)


def _put_fn_from_curl_cffi(session: Any) -> PutFn:
    def _put(method: str, url: str, headers: Dict[str, str],
             body: Optional[bytes], timeout: int) -> Tuple[int, Dict[str, str], str]:
        r = session.request(method, url, headers=headers, data=body, timeout=timeout)
        return r.status_code, dict(r.headers), r.text
    return _put


def build_cos_client(proxy: Optional[str] = None) -> CosClient:
    """起一个带 Chrome TLS 指纹的 curl_cffi 会话，包成 CosClient。trust_env=False 不吃系统 VPN。"""
    from curl_cffi import requests as cffi
    sess = cffi.Session(impersonate=config.CURL_IMPERSONATE, trust_env=False,
                        proxies=({'http': proxy, 'https': proxy} if proxy else None))
    return CosClient(_put_fn_from_curl_cffi(sess), header_decorator=_browser_header_decorator(config.CURL_UA))


# ════════════════════════════════════════════════════════════════════════════
# 发布编排积木(给 publish_image / publish_video 调)
# ════════════════════════════════════════════════════════════════════════════

_COOLDOWN = {461: 3600, 412: 3600, 429: 1800}   # 风控状态码 → 冷却秒数


class PublishError(Exception):
    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


def _raise_special(resp: ApiResp, where: str) -> None:
    if resp.status == SIGN_ERROR:
        raise PublishError(f'{where}: 签名函数 _webmsxyw 异常(页面可能被风控/登录态失效): {resp.text[:160]}',
                           resp.status, resp.text)
    if resp.status == FETCH_ERROR:
        raise PublishError(f'{where}: 页内 fetch 异常(网络/被拦): {resp.text[:160]}', resp.status, resp.text)
    if resp.status == NO_COMMON:
        raise PublishError(f'{where}: 写接口缺 X-S-Common(会 461)，请重登该账号或更新签名页。',
                           resp.status, resp.text)


def image_meta(path: str) -> Dict[str, Any]:
    """量真正上传那份字节的宽高 + extra_info_json，对齐真机 note 体。探测失败退空(占位兜底)。"""
    meta: Dict[str, Any] = {}
    try:
        size_bytes = os.path.getsize(path)
        mime = mimetypes.guess_type(path)[0] or 'image/jpeg'
        try:
            from PIL import Image
            with Image.open(path) as im:
                meta['width'], meta['height'] = int(im.width), int(im.height)
        except Exception as e:
            logger.debug(f'读图像素失败(退占位宽高): {e}')
        meta['extra_info_json'] = json.dumps(
            {'mimeType': mime, 'image_metadata': {'bg_color': '', 'origin_size': size_bytes / 1024}},
            separators=(',', ':'))
    except Exception as e:
        logger.debug(f'量图失败(退全占位): {e}')
    return meta


def probe_video_meta(path: str) -> Dict[str, Any]:
    """best-effort 探视频宽高/时长/帧率(有 cv2 就用，没有退空占位，不阻断)。"""
    try:
        import cv2  # type: ignore
        cap = cv2.VideoCapture(path)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.release()
        out: Dict[str, Any] = {}
        if w:
            out['width'] = w
        if h:
            out['height'] = h
        if fps:
            out['frame_rate'] = int(round(fps))
        if fps and frames:
            out['duration'] = frames / fps
        return out
    except Exception as e:
        logger.debug(f'视频探测跳过(无 cv2 或读失败): {e}')
        return {}


def _content_type(path: str, default: str) -> str:
    return mimetypes.guess_type(path)[0] or default


def _read_bytes(path: str) -> bytes:
    with open(path, 'rb') as f:
        return f.read()


def precheck_visibility(visibility: str) -> None:
    """发布前先校验可见性档位(mutual_friends 未校准要 fail-fast)，避免白传素材再崩。"""
    try:
        note._resolve_visibility(None, visibility)
    except note.VisibilityCalibrationError as e:
        raise PublishError(str(e))


async def resolve_proxy(explicit) -> Optional[str]:
    """这次发布走哪个代理：显式优先；否则按全局模式(默认北京出口)现取一个。
    拿不到合格代理转成 PublishError，绝不静默用本机 IP。阻塞网络活丢线程。"""
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
    assert_account_ready(storage_state)   # 缺 a1/b1/cookie 立刻 fail-fast
    cooling = store.is_cooling(account_id)
    if cooling:
        raise PublishError(f'账号 {account_id} 冷却中，还需 {cooling}s')
    return acc, storage_state


def _ua_fingerprint(ua: str):
    """从真实 UA 推导 sec-ch-ua 头 + curl_cffi 的 JA3 impersonate 版本,保证 UA↔sec-ch-ua↔JA3 三者自洽。
    推不出或版本不支持则回退 config 默认。"""
    import re
    m = re.search(r"Chrome/(\d+)", ua or "")
    if not m:
        return config.CURL_CH_UA, config.CURL_IMPERSONATE
    v = m.group(1)
    ch_ua = f'"Google Chrome";v="{v}", "Chromium";v="{v}", "Not_A Brand";v="24"'
    return ch_ua, f"chrome{v}"


class CurlExecutor:
    """纯协议执行器:NodeSigner 出签(X-s/X-t/X-S-Common)+ curl_cffi 直发,全程不开浏览器。
    接口对齐 SignedApiExecutor(call/reload/harvest_common),get_permit/upload_image/submit_note 编排一字不动。
    指纹自洽:ua 用这个号登录时冻结的真实 UA(b1 编码的就是它),据它定 sec-ch-ua + JA3 impersonate。"""

    def __init__(self, a1: str, b1: str, cookie_header: str, proxy: Optional[str] = None, ua: str = None):
        import os
        import re
        from curl_cffi import requests as cffi
        from .signer import get_signer
        self.b1 = b1 or ""
        self.ua = ua or config.CURL_UA
        self.ch_ua, imp = _ua_fingerprint(self.ua)
        self._signer = get_signer()
        self.login_a1 = a1
        self.a1 = a1
        self.cookie_header = cookie_header
        # a1 迁移(蚁小二式:造新 a1 当新设备 + a1old 留原)默认【关】——实测证明:没有"养熟 a1 池"时,
        # 现造的 a1 是零历史新设备,写接口照样 461,反比用登录 a1 更糟(等于每次换新面孔)。
        # 真正的路是把【登录这个 a1】通过住宅 IP 读操作养熟、再用它发。仅在你有养熟 a1 源时才开 XHS_A1_MIGRATE=1。
        if os.getenv("XHS_A1_MIGRATE", "0") not in ("0", "", "false", "False"):
            try:
                self.a1 = self._signer.gen_a1() or a1
                cookie_header = re.sub(r"(^|;\s*)a1=[^;]*", lambda m: f"{m.group(1)}a1old={a1}", cookie_header, count=1)
                if "a1old=" not in cookie_header:
                    cookie_header = (cookie_header + f"; a1old={a1}").strip("; ")
                self.cookie_header = f"{cookie_header}; a1={self.a1}"
            except Exception:  # noqa: BLE001
                self.a1, self.cookie_header = a1, cookie_header
        proxies = {"http": proxy, "https": proxy} if proxy else None
        try:
            self._sess = cffi.Session(impersonate=imp, trust_env=False, proxies=proxies)
        except Exception:  # noqa: BLE001  推导的版本 curl_cffi 不支持 → 回退默认
            self._sess = cffi.Session(impersonate=config.CURL_IMPERSONATE, trust_env=False, proxies=proxies)
        self._harvested_common = ""

    def _sign_path(self, method: str, path: str, query: str) -> str:
        if method == "GET":
            return path + (query or "") if config.get_sign_uri_mode() == "path_query" else path
        return path

    async def call(self, method: str, host: str, path: str, *,
                   body_str: Optional[str] = None, query: str = "",
                   require_common: bool = False) -> "ApiResp":
        import asyncio
        return await asyncio.to_thread(self._call_sync, method, host, path, body_str, query, require_common)

    def _call_sync(self, method, host, path, body_str, query, require_common) -> "ApiResp":
        method = method.upper()
        has_body = body_str is not None and method != "GET"
        sign_path = self._sign_path(method, path, query)
        data = {}
        if has_body:
            try:
                data = json.loads(body_str)
            except Exception:  # noqa: BLE001
                data = {}
        # UA 传给签名器:X-S-Common 会编码 navigator.userAgent,必须与下面请求头 UA(=本号真实 UA)一致。
        sig = self._signer.sign(sign_path, data, self.a1, b1=self.b1, ua=self.ua, common=True)
        common = sig.get("xsc") or self._harvested_common
        if require_common and not common:
            return ApiResp(NO_COMMON, None, "no_x_s_common: 写接口缺 X-S-Common", has_common=False)
        import os
        headers = {
            "user-agent": self.ua, "sec-ch-ua": self.ch_ua,
            "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-site": "same-site", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty",
            "referer": config.REFERER, "origin": config.ORIGIN,
            "accept": "application/json, text/plain, */*", "accept-language": "zh-CN,zh;q=0.9",
            "x-b3-traceid": os.urandom(8).hex(),                 # 真浏览器每请求带的链路 id
            "cookie": self.cookie_header, "x-s": sig["xs"], "x-t": str(sig["xt"]),
        }
        if common:
            headers["x-s-common"] = common
        url = host + path + (query or "")
        try:
            if has_body:
                headers["content-type"] = "application/json;charset=UTF-8"
                send_body = (sig.get("body") or body_str)          # 与签名同源的 JS 序列化串
                r = self._sess.post(url, headers=headers, data=send_body.encode("utf-8"), timeout=30)
            else:
                r = self._sess.get(url, headers=headers, timeout=30)
        except Exception as e:  # noqa: BLE001
            return ApiResp(-2, None, f"fetch_error: {e}", has_common=bool(common))
        text = r.text or ""
        try:
            j = r.json()
        except Exception:  # noqa: BLE001
            j = None
        return ApiResp(r.status_code, j, text[:4000], has_common=bool(common))

    async def reload(self) -> bool:
        return True        # 纯协议无页面可 reload;下次 call 自然重签

    async def harvest_common(self) -> str:
        return self._harvested_common


def curl_executor_from_state(storage_state: Any, proxy: Optional[str] = None) -> CurlExecutor:
    """从登录态(storage_state)直接建纯协议执行器,不开浏览器。缺 a1/b1/cookie fail-fast。
    UA 取登录时冻结的 _ua(与 b1 指纹自洽);老号没冻结过则回退默认,建议重登一次采真 UA。"""
    assert_account_ready(storage_state)
    ident = extract_identity(storage_state)
    cookies = (storage_state or {}).get("cookies") or []
    jar = "; ".join(f"{c['name']}={c['value']}" for c in cookies
                    if "xiaohongshu" in (c.get("domain") or "") and c.get("name"))
    ua = (storage_state or {}).get("_ua") if isinstance(storage_state, dict) else None
    return CurlExecutor(ident["a1"], ident.get("b1") or "", jar, proxy=proxy, ua=ua)


async def prepare_executor(page) -> SignedApiExecutor:
    """页面 _webmsxyw 就绪后：建执行器 + 校验登录 + 预检/兜底 X-S-Common。"""
    ex = SignedApiExecutor(page)
    try:
        r = await ex.call('GET', config.CREATOR_HOST, config.PATH_USER_INFO)
        if not r.ok:
            logger.warning(f'校验登录态非 200(status={r.status})，继续尝试发布')
    except Exception as e:
        logger.warning(f'校验登录态异常(继续): {e}')
    try:
        probe = await ex.sign_probe('POST', config.PATH_CREATE_NOTE, body_obj={'common': {}})
        if not probe.get('has_common'):
            # X-S-Common 常在页面加载后才由站点自身请求写入 window.__XHS_LAST_X_S_COMMON__;
            # 只 harvest 一次抓不到就裸发写接口必 461(实测间歇失败根因)。轮询几秒等它出现——
            # call() 会把 harvested 的 common 兜底注入(见 _INPAGE_FETCH_JS)。只在缺失时轮询,不拖慢成功路径。
            for _ in range(12):
                if await ex.harvest_common():
                    logger.info('X-S-Common 已就绪(旁路 harvest)')
                    break
                await page.wait_for_timeout(500)
            else:
                logger.warning('X-S-Common 轮询 6s 仍未就绪,写接口可能 461(建议重登该账号刷新签名页)')
    except Exception as e:
        logger.debug(f'X-S-Common 预检跳过: {e}')
    return ex


async def get_permit(ex: SignedApiExecutor, scene: str, count: int = 1) -> Dict[str, Any]:
    query = f'?biz_name=spectrum&scene={scene}&file_count={count}&version=1&source=web'
    resp = await ex.call('GET', config.CREATOR_HOST, config.PATH_UPLOAD_PERMIT, query=query)
    _raise_special(resp, f'申请上传凭证 scene={scene}')
    if not resp.ok or not isinstance(resp.json, dict):
        raise PublishError(f'申请上传凭证失败 scene={scene} status={resp.status}: {resp.text[:160]}',
                           resp.status, resp.text)
    return resp.json


async def upload_image(ex, cos_client, path: str) -> Dict[str, Any]:
    """permit → curl 直传 → 返回 {file_id, width, height, extra_info_json}。token 过期薄重试一次。"""
    meta = await asyncio.to_thread(image_meta, path)
    size = (meta.get('width'), meta.get('height')) if meta.get('width') else None
    data = await asyncio.to_thread(_read_bytes, path)
    ct = _content_type(path, 'image/jpeg')
    last: Optional[Exception] = None
    for attempt in range(2):
        try:
            permit = await get_permit(ex, 'image', 1)
            r = await cos_client.upload_image(permit, data, content_type=ct, size=size)
            return {'file_id': r['file_id'],
                    **{k: v for k, v in meta.items() if k not in ('width', 'height')},
                    'width': r.get('width') or meta.get('width'),
                    'height': r.get('height') or meta.get('height')}
        except PublishError:
            raise
        except Exception as e:
            last = e
            if attempt == 0:
                logger.warning(f'图片上传失败，疑似 permit/token 过期，重申重试: {e}')
                continue
    raise PublishError(f'图片上传重试后仍失败: {last}')


async def upload_video(ex, cos_client, path: str) -> str:
    data = await asyncio.to_thread(_read_bytes, path)
    last: Optional[Exception] = None
    for attempt in range(2):
        try:
            permit = await get_permit(ex, 'video', 1)
            return await cos_client.upload_video(permit, data)
        except PublishError:
            raise
        except Exception as e:
            last = e
            if attempt == 0:
                logger.warning(f'视频上传失败，疑似 permit/token 过期，重申重试: {e}')
                continue
    raise PublishError(f'视频上传重试后仍失败: {last}')


async def submit_note(ex: SignedApiExecutor, note_obj: Dict[str, Any],
                      store: AccountStore, account_id: str) -> Dict[str, Any]:
    """create_note + 薄重试(签名异常 reload 重签一次)。风控码落冷却。返回 {note_id,url,raw}。"""
    body_str = note.serialize(note_obj)
    resp = await ex.call('POST', config.EDITH_HOST, config.PATH_CREATE_NOTE,
                         body_str=body_str, require_common=True)
    if resp.status == SIGN_ERROR:
        logger.warning('创建笔记签名异常(-1)，reload 页面后重试一次…')
        await ex.reload()
        resp = await ex.call('POST', config.EDITH_HOST, config.PATH_CREATE_NOTE,
                             body_str=body_str, require_common=True)
    _raise_special(resp, '创建笔记')
    if resp.status in _COOLDOWN:
        store.cool_down(account_id, _COOLDOWN[resp.status], reason=f'create_note http_{resp.status}')
        raise PublishError(f'创建笔记触发风控 http_{resp.status}', resp.status, resp.text)
    if not resp.ok:
        raise PublishError(f'创建笔记失败 status={resp.status}: {resp.text[:200]}', resp.status, resp.text)
    if not resp.success_body():
        raise PublishError(f'创建笔记业务失败: {resp.text[:200]}', resp.status, resp.text)
    data = resp.json.get('data') or {}
    note_id = data.get('id') or data.get('note_id') or data.get('noteId')
    url = f'https://www.xiaohongshu.com/explore/{note_id}' if note_id else None
    return {'note_id': note_id, 'url': url, 'raw': resp.json}


async def human_pause() -> None:
    """素材传完到点"发布"之间拟人停顿，别几百毫秒打完像机器。"""
    await asyncio.sleep(1.5 + (time.time() % 1) * 2)
