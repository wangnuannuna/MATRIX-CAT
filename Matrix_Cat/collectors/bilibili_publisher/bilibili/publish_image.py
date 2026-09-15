# -*- coding: utf-8 -*-
"""图文发布编排:B站没有独立"图文笔记",对应形态是【图文动态】。
链路:动态图逐张上传(draw/upload_bfs) → create_draw 发动态。全 curl,不开浏览器。

⚠ create_draw 的字段/端点属 [待校准]:由 config.dynamic_calibrated() 门控。没真机校准前直接 fail-loud,
  别拿推断端点裸发(白传图或被拦)。真机发一条确认字段后设 BILI_DYN_CALIBRATED=1 放行。
参考:collectors/kuaishou_publisher/kuaishou/publish_image.py。
"""
import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from . import config, note, protocol

logger = logging.getLogger(config.PLATFORM)


async def publish_image(account_id: str, images: List[str], *,
                        title: str = '', desc: str = '', topics: Optional[List[Any]] = None,
                        visibility: str = 'public', is_original: bool = False,
                        proxy: Optional[str] = None, headless: Optional[bool] = None) -> Dict[str, Any]:
    imgs = [p for p in (images or []) if p]
    if not imgs:
        raise protocol.PublishError('发图文需至少一张图片 --images')
    for p in imgs:
        if not os.path.isfile(p):
            raise protocol.PublishError(f'图片不存在: {p}')
    note.assert_public(visibility)

    if not config.dynamic_calibrated():
        raise protocol.PublishError(
            '图文动态(create_draw)的字段/端点属 [待校准],未真机校准拒绝裸发——避免白传图或被风控。'
            '真机发一条图文动态抓包确认字段后,设 BILI_DYN_CALIBRATED=1 放行。')

    store = protocol.get_store()
    acc, storage_state = protocol.resolve_account(store, account_id)
    eff_proxy = await protocol.resolve_proxy(proxy if proxy is not None else acc.get('last_proxy'))
    client = protocol.build_client(storage_state, eff_proxy)

    # ① 逐张传动态图,收 img_src/宽高/大小
    pics: List[Dict[str, Any]] = []
    for p in imgs[:9]:      # B站动态最多 9 图
        pics.append(await asyncio.to_thread(client.dyn_upload_image, p))
    # ② 拟人停顿后发动态
    await protocol.human_pause()
    form = note.build_dynamic_draw({'title': title, 'desc': desc, 'topics': topics, 'pics': pics})
    res = await asyncio.to_thread(client.dyn_create_draw, form, store=store, account_id=account_id)
    store.mark_used(account_id, proxy=eff_proxy)
    logger.info(f'图文动态发布成功 account={account_id} dynamic_id={res.get("dynamic_id")}')
    return {'work_id': res.get('dynamic_id'), 'url': res.get('url'), 'raw': res.get('raw')}
