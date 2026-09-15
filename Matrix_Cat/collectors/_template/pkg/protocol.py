# -*- coding: utf-8 -*-
"""
协议层——所有"怎么跟平台 API 打交道"的东西：账号身份 + 签名 + 素材上传 + 提交接口 + 发布编排积木。
publish_image / publish_video 只组织素材 + 拼 note + 调这里的积木，薄薄一层。

签名两条路(按平台选)：
  - 自算签名类(如抖音 bd-ticket-guard / aws4)：用 shared.signing 的原语拼，curl_cffi 直连。
  - 浏览器加签类(如小红书 _webmsxyw)：在账号自己的 CloakBrowser 页里出签 + 页内 fetch 直发。
账号存储用框架层 storage.session_store.AccountStore；浏览器会话用 shared.browser。

已落地参考：collectors/xhs_publisher/xhs/protocol.py。
"""
import logging

from . import config
from storage.session_store import AccountStore

logger = logging.getLogger(config.PLATFORM)


class PublishError(Exception):
    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


class AccountContractError(Exception):
    """账号态不满足直发契约(缺关键 cookie/指纹)。发布前 fail-fast。"""


def get_store() -> AccountStore:
    return AccountStore(config.ACCOUNTS_FILE, cookie_domain=config.COOKIE_DOMAIN)


def assert_account_ready(storage_state):
    """缺关键登录态/设备指纹立刻报错，别带空指纹裸发。平台各自定义要校验什么(参考 xhs 的 a1/b1)。"""
    # TODO 按平台补校验
    if not storage_state or not storage_state.get('cookies'):
        raise AccountContractError('账号态为空/未登录，请先 login')


def resolve_account(store, account_id):
    acc = store.get(account_id)
    if not acc:
        raise PublishError(f'账号 {account_id} 不存在，请先 login')
    storage_state = acc.get('storage_state') or {}
    assert_account_ready(storage_state)
    cooling = store.is_cooling(account_id)
    if cooling:
        raise PublishError(f'账号 {account_id} 冷却中，还需 {cooling}s')
    return acc, storage_state


async def resolve_proxy(explicit):
    """显式优先；否则按全局模式(默认北京出口)现取。拿不到转 PublishError，不静默用本机 IP。"""
    import asyncio
    if explicit is not None:
        return explicit
    try:
        return await asyncio.to_thread(config.resolve_proxy)
    except config.ProxyUnavailable as e:
        raise PublishError(str(e))


# ---- TODO：以下按平台协议实现，参考 xhs/protocol.py --------------------------
#  · 素材上传：申请上传凭证 → curl_cffi 直传(shared.http.make_session) → 拿 media_id
#  · 签名 + 提交：自算签名(shared.signing) 或 页内 _webmsxyw；submit → 读结果 id/url
#  · human_pause / 风控冷却映射
def build_client(proxy=None):
    from shared.http import make_session
    return make_session(impersonate=config.CURL_IMPERSONATE, proxy=proxy,
                        timeout=config.UPLOAD_TIMEOUT)


async def upload_media(client, path, **kw):
    raise NotImplementedError("按平台上传链实现；参考 xhs/protocol.upload_image/upload_video")


async def submit(account_id, storage_state, note_obj, store, proxy=None):
    raise NotImplementedError("按平台提交接口实现；参考 xhs/protocol.submit_note")
