# -*- coding: utf-8 -*-
"""发【视频】：传视频 + 封面 → 拼 note 体 → 走协议层发出去。薄薄一层，重活在 protocol.py。
参考 collectors/xhs_publisher/xhs/publish_video.py。"""
import os

from . import config, note, protocol
from shared import browser


async def publish_video(account_id, video_path, cover_path, *, title='', desc='', topics=None,
                        visibility='public', proxy=None, headless=None, store=None):
    if not video_path or not os.path.isfile(video_path):
        raise protocol.PublishError(f'视频不存在: {video_path}')
    if not cover_path or not os.path.isfile(cover_path):
        raise protocol.PublishError('视频发布需要封面图(cover)')
    store = store or protocol.get_store()
    _acc, storage_state = protocol.resolve_account(store, account_id)
    eff_proxy = await protocol.resolve_proxy(proxy)

    sess = await browser.open_session(
        account_id, profiles_dir=config.PROFILES_DIR, goto_url=config.HOME_URL,
        scene='publish', storage_state=storage_state, proxy=eff_proxy, headless=headless)
    try:
        # TODO（参考 xhs/publish_video.py）：传视频+封面 → note.build_video_payload → protocol.submit
        raise NotImplementedError("参考 xhs/publish_video.py 组织本平台发布")
    finally:
        await sess.aclose()
