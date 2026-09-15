# -*- coding: utf-8 -*-
"""发小红书【视频】：传视频 + 封面 → 拼 note 体 → 走协议层发出去。薄薄一层，重活在 protocol.py。"""
import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from . import config, note, protocol
from shared import browser

logger = logging.getLogger(__name__)


async def publish_video(account_id: str, video_path: str, cover_path: str, *,
                        title: str = '', desc: str = '', topics: Optional[List] = None,
                        mentions: Optional[List] = None, visibility: str = 'public',
                        location: Optional[Dict] = None, is_original: bool = False,
                        proxy: Optional[str] = None, headless: Optional[bool] = None,
                        store=None) -> Dict[str, Any]:
    """发一条视频(必须带封面)，返回 {note_id, url, raw}；失败抛 protocol.PublishError。"""
    if not video_path or not os.path.isfile(video_path):
        raise protocol.PublishError(f'视频不存在: {video_path}')
    if not cover_path or not os.path.isfile(cover_path):
        raise protocol.PublishError('视频发布需要封面图(cover)；请先截一帧关键帧')

    store = store or protocol.get_store()
    protocol.precheck_visibility(visibility)
    _acc, storage_state = protocol.resolve_account(store, account_id)
    eff_proxy = await protocol.resolve_proxy(proxy)

    sess = await browser.open_session(
        account_id, profiles_dir=config.PROFILES_DIR, goto_url=config.HOME_URL,
        scene='publish', storage_state=storage_state, proxy=eff_proxy, headless=headless)
    try:
        await sess.wait_for("typeof window._webmsxyw === 'function'")
        ex = await protocol.prepare_executor(sess.page)
        cos_client = protocol.build_cos_client(eff_proxy)

        video_meta = await asyncio.to_thread(protocol.probe_video_meta, video_path)
        video_file_id = await protocol.upload_video(ex, cos_client, video_path)
        cover_info = await protocol.upload_image(ex, cos_client, cover_path)

        note_obj = note.build_video_note({
            'title': title, 'desc': desc, 'topics': topics, 'mentions': mentions,
            'visibility': visibility, 'location': location, 'is_original': is_original,
            'video': {'file_id': video_file_id, **video_meta},
            'cover': {'file_id': cover_info['file_id'],
                      'width': cover_info.get('width'), 'height': cover_info.get('height')},
        })
        await protocol.human_pause()
        result = await protocol.submit_note(ex, note_obj, store, account_id)
        store.mark_used(account_id, proxy=eff_proxy)
        logger.info(f'✅ 视频发布成功 note_id={result["note_id"]} url={result["url"]}')
        return result
    finally:
        await sess.aclose()
