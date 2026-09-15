# -*- coding: utf-8 -*-
"""发【图文】：组织图片素材 → 拼 note 体 → 走协议层发出去。薄薄一层，重活在 protocol.py。
参考 collectors/xhs_publisher/xhs/publish_image.py。"""
import os

from . import config, note, protocol
from shared import browser


async def publish_image(account_id, image_paths, *, title='', desc='', topics=None,
                        visibility='public', proxy=None, headless=None, store=None):
    if not image_paths:
        raise protocol.PublishError('无图片可发布')
    for p in image_paths:
        if not os.path.isfile(p):
            raise protocol.PublishError(f'图片不存在: {p}')
    store = store or protocol.get_store()
    _acc, storage_state = protocol.resolve_account(store, account_id)
    eff_proxy = await protocol.resolve_proxy(proxy)

    sess = await browser.open_session(
        account_id, profiles_dir=config.PROFILES_DIR, goto_url=config.HOME_URL,
        scene='publish', storage_state=storage_state, proxy=eff_proxy, headless=headless)
    try:
        # TODO（参考 xhs/publish_image.py）：
        #   client = protocol.build_client(eff_proxy)
        #   media = [await protocol.upload_media(client, p) for p in image_paths]
        #   body = note.build_image_payload(media, {...})
        #   return await protocol.submit(account_id, storage_state, body, store, eff_proxy)
        raise NotImplementedError("参考 xhs/publish_image.py 组织本平台发布")
    finally:
        await sess.aclose()
