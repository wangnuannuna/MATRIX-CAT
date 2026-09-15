# -*- coding: utf-8 -*-
"""create_v2 提交层:拼 buildPostData_v2 请求体 + 取 csrf token + 发。

签名/头由 signer.build_publish_headers 出(bd-ticket-guard,a_bogus 留空);素材 vid/图片 uri
由 uploader 出。这里只负责"把 item 报文拼对 + POST create_v2 + 读结果"。

体结构是回蚁小二 buildPostData_v2 抠的**核心字段 + 脚手架空对象**(完整体还有 chapter/mix/
话题/活动/富文本等可选项,MVP 先不带)。真机首发若被 create_v2 挑字段,按返回的 status_msg 补。
视频路是主场、做实;图文路按结构搭好,image_info.images 的字段名待真机校准。
"""
import json
import logging
import time
from typing import Any, Dict, List, Optional

from . import config, signer
from shared.http import request_retry

logger = logging.getLogger(config.PLATFORM)

# getSdkToken 会轮着 HEAD 这几个创作接口拿 csrf(读响应头 x-ware-csrf-token)
_CSRF_PROBE_URLS = [
    "https://creator.douyin.com/web/api/media/anchor/search",
    "https://creator.douyin.com/web/api/media/aweme/create/",
    "https://creator.douyin.com/aweme/v1/creator/homepage/module/",
]


class PublishError(Exception):
    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


# ════════════════════════════════════════════════════════════════════════════
# csrf token:HEAD 创作接口读 x-ware-csrf-token(蚁小二 getSdkToken)
# ════════════════════════════════════════════════════════════════════════════
def get_csrf_token(session, cookie_header: str) -> str:
    for i, url in enumerate(_CSRF_PROBE_URLS):
        try:
            r = session.request("HEAD", url, headers={
                "Cookie": cookie_header,
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://creator.douyin.com/content/upload",
                "x-secsdk-csrf-request": "1",
                "x-secsdk-csrf-version": "1.2.7",
                "User-Agent": config.DEFAULT_UA,
            })
            tok = r.headers.get("x-ware-csrf-token") or r.headers.get("X-Ware-Csrf-Token")
            if tok:
                parts = str(tok).split(",")
                return parts[1] if len(parts) > 1 else parts[0]
        except Exception as e:  # noqa: BLE001
            logger.debug(f"csrf HEAD {url} 失败: {e}")
    logger.warning("没取到 x-ware-csrf-token,create_v2 可能被判 csrf;继续尝试(留空)")
    return ""


# ════════════════════════════════════════════════════════════════════════════
# 请求体:视频 / 图文
# ════════════════════════════════════════════════════════════════════════════
def build_video_body(*, vid: str, title: str = "", desc: str = "", visibility: int = 0,
                     download: int = 1, timing: int = 0, poster_uri: Optional[str] = None,
                     cover_w: int = 251, cover_h: int = 335) -> str:
    """视频 create_v2 体(核心字段,照 buildPostData_v2 视频路)。"""
    common = {
        "text": desc or "", "caption": "", "item_title": title or "",
        "activity": "[]", "text_extra": "", "challenges": "", "mentions": "",
        "hashtag_source": "", "hot_sentence": "",
        "visibility_type": visibility, "download": download, "timing": timing,
        "creation_id": f"jdhajhsh{int(time.time() * 1000)}",
        "media_type": 4, "video_id": vid, "music_source": 0,
    }
    cover = {
        "custom_cover_image_height": cover_h, "custom_cover_image_width": cover_w,
        "poster": poster_uri, "poster_delay": 0,
        "horizontal_custom_cover_image_uri": poster_uri, "horizontal_cover_tsp": 0,
        "horizontal_custom_cover_image_height": cover_h,
        "horizontal_custom_cover_image_width": 447,
        "cover_tools_extend_info": "", "cover_tools_info": "",
    }
    item = {
        "common": common, "cover": cover, "mix": {}, "anchor": {},
        "sync": {"should_sync": False, "sync_to_toutiao": 0},
        "open_platform": {}, "assistant": {"is_preview": 0, "is_post_assistant": 1},
        "declare": {"user_declare_info": "{}"},
    }
    return json.dumps({"item": item}, separators=(",", ":"), ensure_ascii=False)


def build_image_body(*, image_uris: List[str], title: str = "", desc: str = "",
                     visibility: int = 0, download: int = 1, timing: int = 0) -> str:
    """图文 create_v2 体。⚠️ image_info.images 的字段名(uri/file_id/web_uri + w/h)是按记忆
    推断的,真机首发很可能要按 create_v2 报错校准。"""
    images = [{"uri": u, "web_uri": u} for u in image_uris]   # TODO 真机校准字段名
    common = {
        "text": desc or "", "caption": "", "item_title": title or "",
        "activity": "[]", "text_extra": "", "challenges": "", "mentions": "",
        "hashtag_source": "", "hot_sentence": "", "type": "normal",
        "visibility_type": visibility, "download": download, "timing": timing,
        "creation_id": f"jdhajhsh{int(time.time() * 1000)}",
    }
    item = {
        "common": common,
        "image_info": {"images": images},
        "video_info": None,
        "mix": {}, "anchor": {},
        "sync": {"should_sync": False, "sync_to_toutiao": 0},
        "assistant": {"is_preview": 0, "is_post_assistant": 1},
        "declare": {"user_declare_info": "{}"},
        "business_binds": [{"bizType": 0}],
    }
    return json.dumps({"item": item}, separators=(",", ":"), ensure_ascii=False)


# ════════════════════════════════════════════════════════════════════════════
# 提交
# ════════════════════════════════════════════════════════════════════════════
def submit_create_v2(session, cookies, body_json: str, *, csrf: str = "",
                     is_video: bool = True) -> Dict[str, Any]:
    """POST create_v2。cookies 传 {name:value} dict 或 header 串。返回解析后的结果。"""
    url = signer.build_create_v2_url(cookies)
    headers = signer.build_publish_headers(cookies, csrf, is_video=is_video)
    r = request_retry(session, "POST", url, data=body_json.encode("utf-8"), headers=headers)
    try:
        data = r.json()
    except Exception:
        raise PublishError(f"create_v2 返回非 JSON(status={r.status_code}): {r.text[:200]}",
                           status=r.status_code, body=r.text[:500])

    status_code = data.get("status_code")
    if status_code not in (0, None):
        raise PublishError(f"create_v2 被拒 status_code={status_code} "
                           f"status_msg={data.get('status_msg')}", status=status_code, body=data)
    aweme_id = _dig(data, ("aweme_id", "item_id", "awemeId"))
    logger.info(f"create_v2 提交成功 aweme_id={aweme_id}")
    return {"ok": True, "aweme_id": aweme_id, "resp": data}


def _dig(obj, keys):
    if isinstance(obj, dict):
        for k in keys:
            if obj.get(k):
                return obj[k]
        for v in obj.values():
            got = _dig(v, keys)
            if got:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _dig(v, keys)
            if got:
                return got
    return None
