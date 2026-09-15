# -*- coding: utf-8 -*-
"""B站协议层 —— 所有"怎么跟 B站 API 打交道"的东西。

跟 xhs/快手最大的不同:**B站写接口没有逐请求 JS 签名**,鉴权=cookie(SESSDATA)+ csrf(bili_jct)。
所以这层全程 curl_cffi 直连(带 Chrome TLS 指纹),不开浏览器:
  · 身份:从 storage_state 抽 SESSDATA/bili_jct/DedeUserID,发布前 fail-fast;
  · 视频:preupload(申请 upos 上传点) → upos 分片直传 → 封面上传 → add/v3 提交;
  · 图文:动态图上传 → create_draw 发图文动态([待校准] 门控);
  · 校验:GET nav(cookie 即可)。

铁律(跟 kuaishou/protocol.py 一致):
  · filename/cid/cover_url 这类每请求真值必须来自上传回执,缺了报错,不编造;
  · 风控码(-352/-509/412…)落冷却,别立即重试撞封;
  · [待校准] 项(图文动态、分区 tid)对应处 fail-loud。
参考同构已落地:collectors/kuaishou_publisher/kuaishou/protocol.py。
"""
import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from . import config, note, signer
from shared.http import request_retry
from storage.session_store import AccountStore

logger = logging.getLogger(config.PLATFORM)


# ════════════════════════════════════════════════════════════════════════════
# 异常
# ════════════════════════════════════════════════════════════════════════════
class AccountContractError(Exception):
    """账号态不满足直发契约(缺 SESSDATA/bili_jct)。发布前 fail-fast。"""


class PublishError(Exception):
    """发布链路任何一步失败统一用它,CLI 一 catch 就能干净报错。"""
    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


# B站业务错误码 → 冷却秒数(风控/频繁/被拦,别立即重试)
_COOLDOWN = {-352: 3600, -509: 1800, -412: 3600, 412: 3600, -799: 1800}


# ════════════════════════════════════════════════════════════════════════════
# 账号身份:storage_state → cookie / SESSDATA / bili_jct / uid
# ════════════════════════════════════════════════════════════════════════════
def _as_dict(cookie_data: Any) -> Dict[str, Any]:
    if not cookie_data:
        return {}
    if isinstance(cookie_data, str):
        try:
            return json.loads(cookie_data) or {}
        except Exception:
            return {}
    return cookie_data if isinstance(cookie_data, dict) else {}


def extract_bili_cookies(cookie_data: Any) -> Dict[str, str]:
    """从 storage_state 抽 .bilibili.com 域的 cookie → {name: value}。"""
    data = _as_dict(cookie_data)
    out: Dict[str, str] = {}
    for c in (data.get('cookies') or []):
        if 'bilibili.com' in (c.get('domain') or ''):
            name = c.get('name')
            if name:
                out[name] = c.get('value', '')
    return out


def extract_identity(cookie_data: Any) -> Dict[str, Any]:
    cookies = extract_bili_cookies(cookie_data)
    return {'cookies': cookies,
            'sessdata': cookies.get(config.CK_SESSDATA, '') or '',
            'csrf': cookies.get(config.CK_CSRF, '') or '',
            'uid': cookies.get(config.CK_UID, '') or ''}


def assert_account_ready(cookie_data: Any) -> Dict[str, Any]:
    """发布前 fail-fast:缺 bilibili.com cookie / SESSDATA / bili_jct 立刻报清晰错误,别裸发被判 -101。"""
    ident = extract_identity(cookie_data)
    if not ident['cookies']:
        raise AccountContractError('账号态无 bilibili.com 域 cookie:未登录或 storage_state 为空,请重新登录。')
    if not ident['sessdata']:
        raise AccountContractError(f'账号 cookie 缺 {config.CK_SESSDATA}(主登录态):失效或没拿全,请重登。')
    if not ident['csrf']:
        raise AccountContractError(
            f'账号 cookie 缺 {config.CK_CSRF}(csrf token):所有写接口都要它,缺了必挂,请重登。')
    return ident


def get_store() -> AccountStore:
    return AccountStore(config.ACCOUNTS_FILE, cookie_domain=config.COOKIE_DOMAIN,
                        cookie_extractor=extract_bili_cookies)


# ════════════════════════════════════════════════════════════════════════════
# curl 直连客户端:cookie + csrf 直发,不开浏览器
# ════════════════════════════════════════════════════════════════════════════
def _biz_ok(j: Any) -> bool:
    """member/api 通用成功约定:code==0。"""
    return isinstance(j, dict) and str(j.get('code')) == '0'


def _upos_ok(j: Any) -> bool:
    """preupload / upos 网关成功约定:OK==1。"""
    return isinstance(j, dict) and str(j.get('OK')) == '1'


class BiliClient:
    """一个账号的 curl_cffi 直连会话。trust_env=False 不吃系统 VPN;cookie 每请求带。"""

    def __init__(self, cookies: Dict[str, str], *, proxy: Optional[str] = None):
        self.cookies = dict(cookies)
        self.csrf = self.cookies.get(config.CK_CSRF, '')
        self.proxy = proxy
        from curl_cffi import requests as creq
        self._s = creq.Session(
            impersonate=config.CURL_IMPERSONATE, trust_env=False, timeout=config.API_TIMEOUT,
            proxies=({'http': proxy, 'https': proxy} if proxy else None))

    def _headers(self, *, referer: str = config.REFERER, origin: str = config.ORIGIN,
                 extra: Optional[dict] = None) -> dict:
        h = {'user-agent': config.CURL_UA, 'referer': referer, 'origin': origin, 'accept': '*/*'}
        if extra:
            h.update(extra)
        return h

    def _cool_if_risk(self, code, store, account_id, where):
        if store and account_id and code in _COOLDOWN:
            store.cool_down(account_id, _COOLDOWN[code], reason=f'{where} code_{code}')
            raise PublishError(f'{where}触发风控 code={code}', code)

    # ---- 身份/登录态 ----------------------------------------------------------
    def nav(self) -> Dict[str, Any]:
        """GET nav。cookie 即可,回 {code, data:{mid,uname,face,wbi_img,...}}。"""
        r = request_retry(self._s, 'GET', config.API_HOST + config.PATH_NAV,
                          cookies=self.cookies,
                          headers=self._headers(referer=config.WEB_REFERER, origin=config.WEB_ORIGIN))
        try:
            return r.json()
        except Exception:
            raise PublishError(f'nav 响应非 JSON status={r.status_code}: {r.text[:160]}', r.status_code, r.text)

    # ---- 视频:preupload → upos 分片 → complete ------------------------------
    def preupload(self, filename: str, size: int) -> Dict[str, Any]:
        params = {
            'name': filename, 'size': size, 'r': config.PREUPLOAD_R,
            'profile': config.PREUPLOAD_PROFILE, 'ssl': '0', 'version': config.WEB_VERSION,
            'build': config.WEB_BUILD, 'upcdn': config.UPOS_UPCDN,
            'probe_version': config.UPOS_PROBE_VERSION, 'webVersion': config.WEB_VERSION,
        }
        r = request_retry(self._s, 'GET', config.MEMBER_HOST + config.PATH_PREUPLOAD,
                          params=params, cookies=self.cookies, headers=self._headers())
        try:
            j = r.json()
        except Exception:
            raise PublishError(f'preupload 响应非 JSON status={r.status_code}: {r.text[:160]}',
                               r.status_code, r.text)
        if not _upos_ok(j):
            raise PublishError(f'preupload 失败(OK≠1): {str(j)[:200]}', r.status_code, r.text)
        return j

    def _upos_base(self, pre: Dict[str, Any]) -> Tuple[str, str]:
        """从 preupload 结果拼 upos 上传 URL 与 upos key(去 ugc/ 前缀、去扩展名,给 submit 用)。"""
        upos_uri = pre.get('upos_uri') or ''            # upos://ugc/n<...>.mp4
        endpoint = pre.get('endpoint') or ''            # //upos-sz-upcdnbda2.bilivideo.com
        if not upos_uri or not endpoint:
            raise PublishError(f'preupload 缺 upos_uri/endpoint: {str(pre)[:200]}')
        path = upos_uri.split('://', 1)[-1]             # ugc/n<...>.mp4
        upload_url = f'https:{endpoint}/{path}'
        # submit 用的 filename = key 去掉目录与扩展名(如 ugc/n230101abc.mp4 → n230101abc)
        key = path.split('/', 1)[-1].rsplit('.', 1)[0]
        return upload_url, key

    def upload_video_file(self, path: str) -> Dict[str, Any]:
        """整段视频上传(同步阻塞):preupload → upos init → 分片 PUT → complete。
        返回 {filename, cid(biz_id)}。编排层用 asyncio.to_thread 调,别卡事件循环。"""
        size = os.path.getsize(path)
        fname = os.path.basename(path)
        pre = self.preupload(fname, size)
        upload_url, key = self._upos_base(pre)
        auth = pre.get('auth') or ''
        biz_id = pre.get('biz_id')
        chunk_size = int(pre.get('chunk_size') or config.DEFAULT_CHUNK_SIZE)
        if not auth or not biz_id:
            raise PublishError(f'preupload 缺 auth/biz_id: {str(pre)[:200]}')
        up_headers = {'x-upos-auth': auth}

        # ① init:POST ?uploads&output=json → upload_id
        ri = self._s.request('POST', upload_url, params={'uploads': '', 'output': 'json'},
                             headers=self._headers(extra=up_headers), data=b'')
        try:
            init = ri.json()
        except Exception:
            raise PublishError(f'upos init 非 JSON status={ri.status_code}: {ri.text[:160]}',
                               ri.status_code, ri.text)
        upload_id = init.get('upload_id')
        if not upload_id:
            raise PublishError(f'upos init 无 upload_id: {str(init)[:200]}')

        # ② 分片 PUT
        with open(path, 'rb') as f:
            data = f.read()
        chunks = [data[off:off + chunk_size] for off in range(0, len(data), chunk_size)]
        total = len(data)
        parts = []
        for i, chunk in enumerate(chunks):
            start = i * chunk_size
            end = start + len(chunk)
            q = {'partNumber': i + 1, 'uploadId': upload_id, 'chunk': i, 'chunks': len(chunks),
                 'size': len(chunk), 'start': start, 'end': end, 'total': total}
            rp = request_retry(self._s, 'PUT', upload_url, params=q,
                               headers=self._headers(extra={**up_headers,
                                                            'content-type': 'application/octet-stream'}),
                               data=chunk, timeout=config.UPLOAD_TIMEOUT)
            if rp.status_code != 200:
                raise PublishError(f'upos 分片 {i+1}/{len(chunks)} 失败 HTTP {rp.status_code}: {rp.text[:120]}',
                                   rp.status_code, rp.text)
            parts.append({'partNumber': i + 1, 'eTag': 'etag'})
        logger.info(f'upos 分片上传完成 {fname} {total} bytes / {len(chunks)} 片')

        # ③ complete:POST ?output=json&name=&profile=&uploadId=&biz_id=  body={parts:[...]}
        cq = {'output': 'json', 'name': fname, 'profile': config.PREUPLOAD_PROFILE,
              'uploadId': upload_id, 'biz_id': biz_id}
        rc = self._s.request('POST', upload_url, params=cq,
                             headers=self._headers(extra={**up_headers,
                                                          'content-type': 'application/json'}),
                             data=json.dumps({'parts': parts}))
        try:
            comp = rc.json()
        except Exception:
            comp = {}
        if rc.status_code != 200 or (comp and not _upos_ok(comp)):
            raise PublishError(f'upos complete 失败 status={rc.status_code}: {rc.text[:160]}',
                               rc.status_code, rc.text)
        return {'filename': key, 'cid': biz_id}

    # ---- 视频:封面上传 + 提交 -----------------------------------------------
    def upload_cover(self, cover_path: str) -> str:
        """封面转 data URL 传 cover/up,拿回封面 url。"""
        import base64
        with open(cover_path, 'rb') as f:
            raw = f.read()
        mime = 'image/png' if cover_path.lower().endswith('.png') else 'image/jpeg'
        data_url = f'data:{mime};base64,{base64.b64encode(raw).decode()}'
        r = self._s.request('POST', config.MEMBER_HOST + config.PATH_V_COVER,
                            data={'cover': data_url, 'csrf': self.csrf},
                            cookies=self.cookies, headers=self._headers())
        try:
            j = r.json()
        except Exception:
            raise PublishError(f'封面上传非 JSON status={r.status_code}: {r.text[:160]}', r.status_code, r.text)
        if not _biz_ok(j):
            raise PublishError(f'封面上传失败: {str(j)[:200]}', r.status_code, r.text)
        url = ((j.get('data') or {}).get('url') or '').strip()
        if not url:
            raise PublishError(f'封面上传响应无 url: {str(j)[:200]}')
        return url

    def video_add(self, body: Dict[str, Any], *, store=None, account_id=None) -> Dict[str, Any]:
        """提交投稿 add/v3。csrf 走 query。风控码落冷却。返回 {aid,bvid,url,raw}。"""
        q = {'csrf': self.csrf, 't': int(time.time() * 1000)}
        # 提交是非幂等写:不走 request_retry(避免网络抖动重发成两稿),单发。
        r = self._s.request('POST', config.MEMBER_HOST + config.PATH_V_ADD, params=q,
                            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                            cookies=self.cookies,
                            headers=self._headers(extra={'content-type': 'application/json;charset=UTF-8'}))
        try:
            j = r.json()
        except Exception:
            raise PublishError(f'投稿 add 非 JSON status={r.status_code}: {r.text[:160]}', r.status_code, r.text)
        self._cool_if_risk(_int_code(j), store, account_id, '投稿提交')
        if not _biz_ok(j):
            raise PublishError(f'投稿提交失败: {str(j)[:240]}', r.status_code, r.text)
        data = j.get('data') or {}
        bvid = data.get('bvid') or ''
        return {'aid': data.get('aid'), 'bvid': bvid,
                'url': f'https://www.bilibili.com/video/{bvid}' if bvid else None, 'raw': j}

    # ---- 图文动态 ------------------------------------------------------------
    def dyn_upload_image(self, image_path: str) -> Dict[str, Any]:
        """传一张动态图,拿回 {img_src,img_width,img_height,img_size}。"""
        with open(image_path, 'rb') as f:
            raw = f.read()
        files = {'file_up': (os.path.basename(image_path), raw)}
        r = self._s.request('POST', config.API_HOST + config.PATH_DYN_IMG_UPLOAD,
                            data={'biz': '3', 'category': 'daily', 'csrf': self.csrf},
                            files=files, cookies=self.cookies,
                            headers=self._headers(referer=config.WEB_REFERER, origin=config.WEB_ORIGIN))
        try:
            j = r.json()
        except Exception:
            raise PublishError(f'动态图上传非 JSON status={r.status_code}: {r.text[:160]}', r.status_code, r.text)
        if not _biz_ok(j):
            raise PublishError(f'动态图上传失败: {str(j)[:200]}', r.status_code, r.text)
        d = j.get('data') or {}
        src = d.get('image_url') or d.get('img_src')
        if not src:
            raise PublishError(f'动态图上传响应无 image_url: {str(j)[:200]}')
        return {'img_src': src,
                'img_width': d.get('image_width') or d.get('img_width') or 0,
                'img_height': d.get('image_height') or d.get('img_height') or 0,
                'img_size': d.get('img_size') or round(len(raw) / 1024, 2)}

    def dyn_create_draw(self, form: Dict[str, Any], *, store=None, account_id=None) -> Dict[str, Any]:
        """发图文动态 create_draw。form 由 note.build_dynamic_draw 拼。返回 {dynamic_id,url,raw}。"""
        form = dict(form)
        form['csrf'] = self.csrf
        form['csrf_token'] = self.csrf
        r = self._s.request('POST', config.VC_HOST + config.PATH_DYN_CREATE_DRAW,
                            data=form, cookies=self.cookies,
                            headers=self._headers(referer=config.WEB_REFERER, origin=config.WEB_ORIGIN))
        try:
            j = r.json()
        except Exception:
            raise PublishError(f'发图文动态非 JSON status={r.status_code}: {r.text[:160]}', r.status_code, r.text)
        self._cool_if_risk(_int_code(j), store, account_id, '发图文动态')
        if not _biz_ok(j):
            raise PublishError(f'发图文动态失败: {str(j)[:240]}', r.status_code, r.text)
        d = j.get('data') or {}
        did = d.get('dynamic_id_str') or (str(d.get('dynamic_id')) if d.get('dynamic_id') else '')
        return {'dynamic_id': did, 'url': f'https://t.bilibili.com/{did}' if did else None, 'raw': j}


def _int_code(j: Any) -> Optional[int]:
    try:
        return int(j.get('code'))
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════════════════
# 发布编排积木(给 publish_image / publish_video 调)
# ════════════════════════════════════════════════════════════════════════════
async def resolve_proxy(explicit) -> Optional[str]:
    if explicit is not None:
        return explicit
    try:
        return await asyncio.to_thread(config.resolve_proxy)
    except config.ProxyUnavailable as e:
        raise PublishError(str(e))


def resolve_account(store: AccountStore, account_id: str):
    acc = store.get(account_id)
    if not acc:
        raise PublishError(f'账号 {account_id} 不存在,请先 login')
    storage_state = acc.get('storage_state') or {}
    try:
        assert_account_ready(storage_state)
    except AccountContractError as e:
        raise PublishError(str(e))
    cooling = store.is_cooling(account_id)
    if cooling:
        raise PublishError(f'账号 {account_id} 冷却中,还需 {cooling}s')
    return acc, storage_state


def build_client(storage_state: Dict[str, Any], proxy: Optional[str]) -> BiliClient:
    return BiliClient(extract_bili_cookies(storage_state), proxy=proxy)


async def human_pause() -> None:
    """素材传完到点提交之间拟人停顿。"""
    await asyncio.sleep(1.5 + (time.time() % 1) * 2)


# ════════════════════════════════════════════════════════════════════════════
# 登录态校验(给 run.py refresh / login 复用)—— 纯 curl,不开浏览器
# ════════════════════════════════════════════════════════════════════════════
async def check_login(account_id: str, storage_state: Dict[str, Any], *,
                      proxy: Optional[str] = None) -> Tuple[bool, Dict[str, Any]]:
    """用账号 cookie 调 nav,确认登录态还活着并取昵称/mid/头像。全程 curl,无浏览器。"""
    ident = extract_identity(storage_state)
    if not ident['sessdata']:
        return False, {'error': f'缺 {config.CK_SESSDATA},请重新登录'}
    eff_proxy = proxy
    if eff_proxy is None:
        try:
            eff_proxy = await asyncio.to_thread(config.resolve_proxy)
        except config.ProxyUnavailable as e:
            return False, {'error': str(e)}
    try:
        client = build_client(storage_state, eff_proxy)
        j = await asyncio.to_thread(client.nav)
        if _biz_ok(j) and (j.get('data') or {}).get('isLogin'):
            d = j['data']
            return True, {'nickname': d.get('uname'), 'user_id': str(d.get('mid') or ident.get('uid')),
                          'avatar': d.get('face')}
        return False, {'error': f'nav 未登录/异常: {str(j)[:160]}'}
    except Exception as e:
        return False, {'error': str(e)}
