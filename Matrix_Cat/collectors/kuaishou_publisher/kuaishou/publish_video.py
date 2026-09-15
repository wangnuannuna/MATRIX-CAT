# -*- coding: utf-8 -*-
"""发快手【视频】：pre 取 token → 分片传视频 → finish 拿 fileId/coverKey → submit。薄薄一层。

协议链路端点全 [确认]；submit 体字段名 [确认]、部分取值 [待校准](见 note.py)。
封面：finish 会回一个视频自动封面 coverKey(够用)。自定义封面上传接口(cover/upload)的
multipart 字段是 [待校准]，没校准前 --cover 会被忽略并退用自动封面(打醒目告警，不假装用了)。
参考同构：collectors/xhs_publisher/xhs/publish_video.py。
"""
import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from . import config, note, protocol
from shared import browser

logger = logging.getLogger(__name__)


async def publish_video(account_id: str, video_path: str, cover_path: Optional[str] = None, *,
                        title: str = '', desc: str = '', topics: Optional[List] = None,
                        visibility: str = 'public', is_original: bool = False,
                        proxy: Optional[str] = None, headless: Optional[bool] = None,
                        store=None) -> Dict[str, Any]:
    """发一条视频，返回 {work_id, url, raw}；失败抛 protocol.PublishError。"""
    if not video_path or not os.path.isfile(video_path):
        raise protocol.PublishError(f'视频不存在: {video_path}')
    if cover_path and not os.path.isfile(cover_path):
        raise protocol.PublishError(f'封面图不存在: {cover_path}')

    store = store or protocol.get_store()
    note.resolve_photo_status(visibility)   # 发布前可见性预检:private/friends 未校准就 fail-fast，别白传素材
    if is_original:
        logger.warning('快手原创声明字段 [待校准]，本次未写入 submit 体(不静默假装已声明)；真机确认字段后再接。')
    _acc, storage_state = protocol.resolve_account(store, account_id)
    ident = protocol.extract_identity(storage_state)
    eff_proxy = await protocol.resolve_proxy(proxy)

    sess = await browser.open_session(
        account_id, profiles_dir=config.PROFILES_DIR, goto_url=config.HOME_URL,
        scene='publish', storage_state=storage_state, proxy=eff_proxy, headless=headless)
    try:
        ex = protocol.SignedApiExecutor(sess.page)
        await ex.wait_ready()
        uploader = protocol.build_uploader(eff_proxy)

        # 1) 申请上传 token → 2) 分片/直传视频字节到网关
        token = await protocol.get_upload_token(ex, ident['api_ph'], path=config.PATH_V_PRE)
        data = await asyncio.to_thread(protocol._read_bytes, video_path)
        await uploader.upload(token, data, filename=os.path.basename(video_path))

        # 3) finish 回传元数据拿 fileId/coverKey
        fin = await protocol.finish_media(ex, ident['api_ph'], token, video_path,
                                          finish_path=config.PATH_V_FINISH, file_type='video')
        if cover_path:
            logger.warning('自定义封面上传接口(cover/upload)字段 [待校准]，本次退用视频自动封面 coverKey；'
                           '真机校准 multipart 字段后再启用 --cover。')

        # 4) 拼 submit 体 → 提交发布(缺回执真值 note 抛 ValueError，转成干净 PublishError)
        try:
            body = note.build_video_submit({
                'title': title, 'desc': desc, 'topics': topics, 'visibility': visibility,
                'file_id': fin['file_id'], 'cover_key': fin['cover_key'], 'media_id': fin['media_id'],
            })
        except ValueError as e:
            raise protocol.PublishError(str(e))
        await protocol.human_pause()
        result = await protocol.submit_work(ex, config.PATH_V_SUBMIT, body,
                                            ident['api_ph'], store, account_id)
        store.mark_used(account_id, proxy=eff_proxy)
        logger.info(f'✅ 视频发布成功 work_id={result["work_id"]} url={result.get("url")}')
        return result
    finally:
        await sess.aclose()
