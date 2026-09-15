# -*- coding: utf-8 -*-
"""抖音协议层——账号身份 / 就绪校验 / (待补)素材上传 + create_v2 提交。

现阶段只落地"账号存储 + 直发契约校验"这部分,给 login/probe 复用;素材上传(VOD/ImageX)
和 create_v2 提交是 publish 的活,先留 NotImplementedError 占位(参考 xhs/protocol.py)。

直发契约(抖音专属):secsdk 的 EC 私钥 + ticket 必须齐、且能真签出并自洽,否则 bd-ticket-guard
过不了,别裸发。校验委托给 probe.check_cookies。
"""
import logging

from . import config, signer
from storage.session_store import AccountStore

logger = logging.getLogger(config.PLATFORM)


class PublishError(Exception):
    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


class AccountContractError(Exception):
    """账号态不满足直发契约(缺 secsdk 关键 cookie / 签不出)。发布前 fail-fast。"""


def get_store() -> AccountStore:
    return AccountStore(config.ACCOUNTS_FILE, cookie_domain=config.COOKIE_DOMAIN)


def assert_account_ready(cookies) -> dict:
    """缺 secsdk 关键 cookie 或签不出 → 立刻报清晰错误。返回 probe 体检报告。"""
    from . import probe
    rep = probe.check_cookies(cookies)
    if not rep.get("ok"):
        # 拼一句人能看懂的原因
        ec = rep["checks"].get(config.COOKIE_EC_PRIVATE, {})
        wp = rep["checks"].get(config.COOKIE_WEB_PROTECT, {})
        why = []
        if not ec.get("parsed"):
            why.append(f"缺/坏 {config.COOKIE_EC_PRIVATE}({ec.get('detail','')})")
        if not wp.get("parsed"):
            why.append(f"缺/坏 {config.COOKIE_WEB_PROTECT}({wp.get('detail','')})")
        if rep.get("sign", {}).get("error"):
            why.append(f"签名失败:{rep['sign']['error']}")
        if not why:
            why.append("secsdk 体检未通过(签名自校验不过)")
        raise AccountContractError("；".join(why) + " —— 登录态没拿全 secsdk,请重登并确认打开过发布页。")
    return rep


def resolve_account(store, account_id: str):
    acc = store.get(account_id)
    if not acc:
        raise PublishError(f"账号 {account_id} 不存在,请先 login")
    cooling = store.is_cooling(account_id)
    if cooling:
        raise PublishError(f"账号 {account_id} 冷却中,还需 {cooling}s")
    assert_account_ready(acc.get("cookies") or {})
    return acc


# ---- 待补(publish 的活;参考 xhs/protocol.py & 蚁小二 reptile) --------------
def build_client(proxy=None):
    from shared.http import make_session
    return make_session(impersonate=config.CURL_IMPERSONATE, proxy=proxy,
                        timeout=config.UPLOAD_TIMEOUT)


async def upload_media(client, path, **kw):
    raise NotImplementedError("VOD/ImageX 上传链待实现:ApplyUploadInner→PUT→CommitUploadInner")


async def submit(account_id, cookies, note_obj, store, proxy=None):
    raise NotImplementedError("create_v2 提交待实现:signer.build_publish_headers + buildPostData_v2")
