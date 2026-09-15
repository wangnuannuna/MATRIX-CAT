# -*- coding: utf-8 -*-
"""发图文:解析账号 → 逐张传 ImageX → 拼图文体 → create_v2。

⚠️ 图文体(image_info.images 字段名 / business_binds)是按记忆结构搭的,真机首发大概率要按
create_v2 报错校准;视频路已做实,图文路先跑通链路。
"""
import logging

from . import config, protocol, publish, signer, uploader
from .publish_video import _resolve_user_id, _vis

logger = logging.getLogger(config.PLATFORM)


def publish_image(account_id, images, *, title="", desc="", visibility="public", proxy=None):
    if not images:
        raise publish.PublishError("images 为空")
    store = protocol.get_store()
    acc = protocol.resolve_account(store, account_id)
    cookies = acc.get("cookies") or {}
    cookie_header = signer.cookie_header(cookies)
    user_id = _resolve_user_id(acc, cookies)
    proxy = proxy or None

    # 1) 逐张传 ImageX,收 uri
    uris = []
    for p in images:
        info = uploader.upload_image(cookie_header, user_id, p, proxy=proxy)
        uri = info.get("uri")
        if not uri:
            raise publish.PublishError(f"图片上传没拿到 uri: {p} -> {info}")
        uris.append(uri)

    # 2) 拼体 + csrf + 提交
    body = publish.build_image_body(image_uris=uris, title=title, desc=desc, visibility=_vis(visibility))
    session = protocol.build_client(proxy=proxy)
    csrf = publish.get_csrf_token(session, cookie_header)
    res = publish.submit_create_v2(session, cookies, body, csrf=csrf, is_video=False)
    store.mark_used(account_id, proxy=proxy)
    return res
