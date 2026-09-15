# -*- coding: utf-8 -*-
"""发快手【图文/图集】：逐张图 pre→传→拿 key → 拼 atlas 体 → submit。薄薄一层。

⚠️ 诚实前置：图文 atlas 版接口路径(pre/finish/submit)、图片 key 字段名开源里**没有确证**，
全是 [推断]。所以默认门控关闭(config.atlas_calibrated()=False)时**直接 fail-loud**，不拿推断端点
白传素材/被拦。真机抓包确认 atlas 链路后设 KS_ATLAS_CALIBRATED=1(必要时 KS_ATLAS_* 覆盖路径)放行。

放行后的流程按"与视频同构、图集无封面、提交图片 key 数组"的最合理猜测跑，真机再定型。
参考同构：collectors/xhs_publisher/xhs/publish_image.py。
"""
import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from . import config, note, protocol
from shared import browser

logger = logging.getLogger(__name__)


async def publish_image(account_id: str, image_paths: List[str], *, title: str = '',
                        desc: str = '', topics: Optional[List] = None,
                        visibility: str = 'public', is_original: bool = False,
                        proxy: Optional[str] = None, headless: Optional[bool] = None,
                        store=None) -> Dict[str, Any]:
    """发一条图文(图集)，返回 {work_id, url, raw}；失败抛 protocol.PublishError。"""
    if not image_paths:
        raise protocol.PublishError('无图片可发布')
    for p in image_paths:
        if not os.path.isfile(p):
            raise protocol.PublishError(f'图片不存在: {p}')

    # fail-loud 门控：atlas 协议未真机校准，拒绝拿推断端点盲发。
    if not config.atlas_calibrated():
        raise protocol.PublishError(
            '快手图文(atlas)发布协议未真机校准：atlas 版 pre/finish/submit 接口路径与图片 key 字段名'
            '开源无确证，全是推断。为免白传素材/被风控，已 fail-loud 拒发。\n'
            '  → 真机抓包确认后设 KS_ATLAS_CALIBRATED=1(必要时 KS_ATLAS_PRE/FINISH/SUBMIT 覆盖路径)放行。\n'
            '  → 校准清单见 collectors/kuaishou_publisher/README.md「必须真机抓包校准清单」。')

    store = store or protocol.get_store()
    note.resolve_photo_status(visibility)   # 发布前可见性预检:private/friends 未校准就 fail-fast，别白传素材
    if is_original:
        logger.warning('快手原创声明字段 [待校准]，本次未写入 submit 体(不静默假装已声明)；真机确认字段后再接。')
    _acc, storage_state = protocol.resolve_account(store, account_id)
    ident = protocol.extract_identity(storage_state)
    eff_proxy = await protocol.resolve_proxy(proxy)

    sess = await browser.open_session(
        account_id, profiles_dir=config.PROFILES_DIR, goto_url=config.HOME_URL_PIC,
        scene='publish', storage_state=storage_state, proxy=eff_proxy, headless=headless)
    try:
        ex = protocol.SignedApiExecutor(sess.page)
        await ex.wait_ready()
        uploader = protocol.build_uploader(eff_proxy)

        # 逐张图：pre 取 token → 传字节 → finish 拿 key([推断] 同构视频链路)
        images_meta = []
        for i, path in enumerate(image_paths):
            token = await protocol.get_upload_token(ex, ident['api_ph'], path=config.PATH_A_PRE)
            data = await asyncio.to_thread(protocol._read_bytes, path)
            await uploader.upload(token, data, filename=os.path.basename(path))
            fin = await protocol.finish_media(ex, ident['api_ph'], token, path,
                                              finish_path=config.PATH_A_FINISH, file_type='image')
            images_meta.append({'key': fin['file_id']})
            logger.info(f'图 {i + 1}/{len(image_paths)} 上传完成 key={fin["file_id"]}')

        try:
            body = note.build_atlas_submit({
                'title': title, 'desc': desc, 'topics': topics, 'visibility': visibility,
                'images': images_meta,
            })
        except ValueError as e:
            raise protocol.PublishError(str(e))
        await protocol.human_pause()
        result = await protocol.submit_work(ex, config.PATH_A_SUBMIT, body,
                                            ident['api_ph'], store, account_id)
        store.mark_used(account_id, proxy=eff_proxy)
        logger.info(f'✅ 图文发布成功 work_id={result["work_id"]} url={result.get("url")}')
        return result
    finally:
        await sess.aclose()
