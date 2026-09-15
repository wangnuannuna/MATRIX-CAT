# -*- coding: utf-8 -*-
"""视频投稿编排:preupload→upos 分片直传→封面上传→add/v3 提交。全 curl,不开浏览器。

参考:collectors/kuaishou_publisher/kuaishou/publish_video.py。
"""
import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from . import config, note, protocol

logger = logging.getLogger(config.PLATFORM)


async def publish_video(account_id: str, video_path: str, cover_path: Optional[str], *,
                        title: str = '', desc: str = '', topics: Optional[List[Any]] = None,
                        visibility: str = 'public', is_original: bool = False,
                        proxy: Optional[str] = None, headless: Optional[bool] = None,
                        tid: Optional[int] = None, source: str = '') -> Dict[str, Any]:
    if not os.path.isfile(video_path):
        raise protocol.PublishError(f'视频文件不存在: {video_path}')
    if not cover_path or not os.path.isfile(cover_path):
        raise protocol.PublishError('B站投稿必须给封面 --cover(add/v3 的 cover 必填,不可编造)')
    note.assert_public(visibility)   # 提前挡掉私密/好友(B站 web 投稿不支持),别白传素材

    store = protocol.get_store()
    acc, storage_state = protocol.resolve_account(store, account_id)
    eff_proxy = await protocol.resolve_proxy(proxy if proxy is not None else acc.get('last_proxy'))
    client = protocol.build_client(storage_state, eff_proxy)

    # ① 视频上传(preupload→upos 分片→complete)
    up = await asyncio.to_thread(client.upload_video_file, video_path)
    # ② 封面上传
    cover_url = await asyncio.to_thread(client.upload_cover, cover_path)
    # ③ 拟人停顿后提交
    await protocol.human_pause()
    body = note.build_video_add({
        'title': title, 'desc': desc, 'topics': topics, 'visibility': visibility,
        'filename': up['filename'], 'cid': up.get('cid'), 'cover': cover_url,
        'tid': tid, 'source': source,
    })
    res = await asyncio.to_thread(client.video_add, body, store=store, account_id=account_id)
    store.mark_used(account_id, proxy=eff_proxy)
    logger.info(f'投稿成功 account={account_id} bvid={res.get("bvid")}')
    return {'work_id': res.get('bvid') or res.get('aid'), 'url': res.get('url'), 'raw': res.get('raw')}
