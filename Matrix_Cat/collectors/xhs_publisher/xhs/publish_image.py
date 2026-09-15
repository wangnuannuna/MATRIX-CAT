# -*- coding: utf-8 -*-
"""发小红书【图文】：组织图片素材 → 拼 note 体 → 走协议层发出去。薄薄一层，重活在 protocol.py。"""
import logging
import os
from typing import Any, Dict, List, Optional

from . import config, note, protocol
from shared import browser

logger = logging.getLogger(__name__)


def _pure_proto() -> bool:
    """纯协议开关:XHS_PURE_PROTO=1 → 不开浏览器,NodeSigner 出签 + curl 直发。"""
    return os.getenv('XHS_PURE_PROTO', '0') not in ('0', '', 'false', 'False')


async def _publish_image_proto(account_id, image_paths, storage_state, eff_proxy, store, *,
                               title, desc, topics, mentions, visibility, location, is_original):
    """纯协议发图文:全程不开浏览器。复用 upload_image/build_image_note/submit_note,只把执行器换成 CurlExecutor。"""
    ex = protocol.curl_executor_from_state(storage_state, proxy=eff_proxy)
    cos_client = protocol.build_cos_client(eff_proxy)
    images_meta = []
    for i, p in enumerate(image_paths):
        info = await protocol.upload_image(ex, cos_client, p)
        images_meta.append(info)
        logger.info(f'[纯协议] 图 {i + 1}/{len(image_paths)} 上传完成 file_id={info["file_id"]}')
    note_obj = note.build_image_note({
        'title': title, 'desc': desc, 'topics': topics, 'mentions': mentions,
        'visibility': visibility, 'location': location, 'is_original': is_original,
        'images': images_meta,
    })
    await protocol.human_pause()
    result = await protocol.submit_note(ex, note_obj, store, account_id)
    store.mark_used(account_id, proxy=eff_proxy)
    logger.info(f'✅ [纯协议] 图文发布成功 note_id={result["note_id"]} url={result["url"]}')
    return result


async def publish_image(account_id: str, image_paths: List[str], *, title: str = '',
                        desc: str = '', topics: Optional[List] = None,
                        mentions: Optional[List] = None, visibility: str = 'public',
                        location: Optional[Dict] = None, is_original: bool = False,
                        proxy: Optional[str] = None, headless: Optional[bool] = None,
                        store=None) -> Dict[str, Any]:
    """发一条图文，返回 {note_id, url, raw}；失败抛 protocol.PublishError。"""
    if not image_paths:
        raise protocol.PublishError('无图片可发布')
    for p in image_paths:
        if not os.path.isfile(p):
            raise protocol.PublishError(f'图片不存在: {p}')

    store = store or protocol.get_store()
    protocol.precheck_visibility(visibility)
    _acc, storage_state = protocol.resolve_account(store, account_id)
    eff_proxy = await protocol.resolve_proxy(proxy)

    if _pure_proto():                               # 纯协议:不开浏览器,Node 出签 + curl 直发
        logger.info('[纯协议] 走 NodeSigner + curl_cffi,不开浏览器')
        return await _publish_image_proto(account_id, image_paths, storage_state, eff_proxy, store,
                                          title=title, desc=desc, topics=topics, mentions=mentions,
                                          visibility=visibility, location=location, is_original=is_original)

    sess = await browser.open_session(
        account_id, profiles_dir=config.PROFILES_DIR, goto_url=config.HOME_URL,
        scene='publish', storage_state=storage_state, proxy=eff_proxy, headless=headless)
    try:
        await sess.wait_for("typeof window._webmsxyw === 'function'")
        ex = await protocol.prepare_executor(sess.page)
        cos_client = protocol.build_cos_client(eff_proxy)

        images_meta = []
        for i, p in enumerate(image_paths):
            info = await protocol.upload_image(ex, cos_client, p)
            images_meta.append(info)
            logger.info(f'图 {i + 1}/{len(image_paths)} 上传完成 file_id={info["file_id"]}')

        note_obj = note.build_image_note({
            'title': title, 'desc': desc, 'topics': topics, 'mentions': mentions,
            'visibility': visibility, 'location': location, 'is_original': is_original,
            'images': images_meta,
        })
        await protocol.human_pause()
        result = await protocol.submit_note(ex, note_obj, store, account_id)
        store.mark_used(account_id, proxy=eff_proxy)
        logger.info(f'✅ 图文发布成功 note_id={result["note_id"]} url={result["url"]}')
        return result
    finally:
        await sess.aclose()
