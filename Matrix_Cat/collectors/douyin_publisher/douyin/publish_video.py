# -*- coding: utf-8 -*-
"""发视频:解析账号(secsdk 就绪校验)→ 传视频(+可选封面)→ 拼体 → create_v2。薄薄一层编排。"""
import logging

from . import config, protocol, publish, signer, uploader

logger = logging.getLogger(config.PLATFORM)

_VIS = {"public": 0, "private": 1, "friends": 2, "friend": 2}


def _vis(v) -> int:
    if isinstance(v, int):
        return v
    return _VIS.get(str(v).lower().strip(), 0)


def _resolve_user_id(acc, cookies):
    """上传要的 user_id(数字 aweme uid)。best-effort:account.user_id → cookie。
    ⚠️ uid_tt 不一定等于发布用的数字 uid,真机校准。"""
    uid = acc.get("user_id") or cookies.get("uid_tt") or cookies.get("sec_user_id")
    if not uid:
        logger.warning("没拿到 user_id,上传接口可能报错;真机确认数字 uid 从哪来")
    return uid


def publish_video(account_id, video_path, cover_path=None, *, title="", desc="",
                  visibility="public", proxy=None):
    store = protocol.get_store()
    acc = protocol.resolve_account(store, account_id)   # 缺 secsdk / 签不出会 raise
    cookies = acc.get("cookies") or {}
    cookie_header = signer.cookie_header(cookies)
    user_id = _resolve_user_id(acc, cookies)
    # 抖音境内默认直连;要走代理传 --proxy 或设 DOUYIN_PROXY
    proxy = proxy or None

    # 1) 传视频
    vinfo = uploader.upload_video(cookie_header, user_id, video_path, proxy=proxy)
    vid = vinfo.get("vid")
    if not vid:
        raise publish.PublishError(f"视频上传没拿到 vid: {vinfo}")

    # 2) 封面(可选)→ 图片 uri
    poster = None
    if cover_path:
        try:
            poster = uploader.upload_image(cookie_header, user_id, cover_path, proxy=proxy).get("uri")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"封面上传失败(不阻断发布): {e}")

    # 3) 拼体 + csrf + 提交
    body = publish.build_video_body(vid=vid, title=title, desc=desc,
                                    visibility=_vis(visibility), poster_uri=poster)
    session = protocol.build_client(proxy=proxy)
    csrf = publish.get_csrf_token(session, cookie_header)
    res = publish.submit_create_v2(session, cookies, body, csrf=csrf, is_video=True)
    store.mark_used(account_id, proxy=proxy)
    return res
