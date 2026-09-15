# -*- coding: utf-8 -*-
"""快手发布体(submit 请求体)构造。纯函数、无 I/O、可单测。

结构不是拍脑袋编的：字段名来自开源逆向(视频 submit 的 caption/fileId/coverKey/mediaId/
photoStatus 等已确认)；但**各字段取值(尤其 photoStatus 可见性枚举、coverType/photoType/
domain 默认)未真机核对**，全靠 env 可覆盖，对应处 [待校准] 并对隐私敏感项 fail-loud。

fail-loud 原则(跟 xhs/note.py 一致)：
  · fileId/coverKey 这类"每请求真值"必须来自上传回执，缺了报错，绝不编造。
  · 可见性 private/friends 的枚举值没真机确认前拒绝盲发——猜错会把私密发成公开(隐私事故)。
    公开(public)给最佳猜测默认(猜错顶多被服务端拒、无隐私风险)，放行但打醒目告警。

参考同构已落地：collectors/xhs_publisher/xhs/note.py。
"""
import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# CALIBRATE 区：所有"取值未真机核对"的常量集中在这，全部可用 env 覆盖
# ════════════════════════════════════════════════════════════════════════════
# photoStatus 可见性 —— [待校准] 公开常见取 1(推断)；私密/好友确切值未证实。
def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name, '') or '').strip() or default)
    except Exception:
        return default


CALIBRATE_VIS_PUBLIC = _env_int('KS_VIS_PUBLIC', 1)      # [待校准] 公开(最佳猜测)
CALIBRATE_VIS_PRIVATE = _env_int('KS_VIS_PRIVATE', 2)    # [待校准] 仅自己可见
CALIBRATE_VIS_FRIENDS = _env_int('KS_VIS_FRIENDS', 3)    # [待校准] 好友可见

# submit 里几个"有默认值但取值待抓包"的字段。给 authentic-shaped 占位，可 env 覆盖。
CALIBRATE_COVER_TYPE = _env_int('KS_COVER_TYPE', 2)      # [待校准] 2≈自定义封面(推断)
CALIBRATE_PHOTO_TYPE = _env_int('KS_PHOTO_TYPE', 0)      # [待校准] 区分横/竖屏
CALIBRATE_DOMAIN = os.getenv('KS_DOMAIN', '')            # [待校准] 业务域，默认空


class VisibilityCalibrationError(Exception):
    """可见性档位(private/friends)的 photoStatus 枚举未经真机校准，拒绝按占位盲发——
    可能把仅自己/好友误发成公开。真机确认后设 KS_VISIBILITY_CALIBRATED=1(并按需给 KS_VIS_* 值)。"""


def _visibility_calibrated() -> bool:
    return (os.getenv('KS_VISIBILITY_CALIBRATED', '') or '').strip().lower() in ('1', 'true', 'yes')


def resolve_photo_status(visibility: str) -> int:
    """visibility('public'|'private'|'friends') → photoStatus 整数。

    public 用最佳猜测默认放行(猜错顶多发布失败，无隐私风险)；private/friends 未校准直接抛，
    别把私密内容按占位值发出去。"""
    v = (visibility or 'public').strip().lower()
    if v in ('public', '公开', ''):
        return CALIBRATE_VIS_PUBLIC
    if v in ('private', 'self', 'self_only', '私密', '仅自己'):
        if not _visibility_calibrated():
            raise VisibilityCalibrationError(
                "可见性'私密'(private)的 photoStatus 枚举未真机校准，拒绝按占位值盲发——"
                "可能误发成公开。真机确认后设 KS_VISIBILITY_CALIBRATED=1(必要时 KS_VIS_PRIVATE=<真值>)。")
        return CALIBRATE_VIS_PRIVATE
    if v in ('friends', 'friend', '好友'):
        if not _visibility_calibrated():
            raise VisibilityCalibrationError(
                "可见性'好友'(friends)的 photoStatus 枚举未真机校准，拒绝按占位值盲发。"
                "真机确认后设 KS_VISIBILITY_CALIBRATED=1(必要时 KS_VIS_FRIENDS=<真值>)。")
        return CALIBRATE_VIS_FRIENDS
    # 不认识的档位当公开处理但告警(别静默升级可见性)。
    logger.warning(f'未知可见性 {visibility!r}，按 public 处理')
    return CALIBRATE_VIS_PUBLIC


# ════════════════════════════════════════════════════════════════════════════
# 文本：话题内联进 caption(快手 #话题# 大概率直接写正文，不是独立数组 —— [推断])
# ════════════════════════════════════════════════════════════════════════════
def build_caption(title: str, desc: str, topics: Optional[List[Any]]) -> str:
    """把标题/正文/话题拼成 caption 文本。快手正文=caption，话题以 #话题# 内联(最多 3 个)。"""
    parts = []
    t = (title or '').strip()
    d = (desc or '').strip()
    if t:
        parts.append(t)
    if d and d != t:
        parts.append(d)
    text = '\n'.join(parts)
    tags = _normalize_topics(topics)[:3]   # [确认] 图文标签最多 3 个；视频从宽也按 3 收口
    for name in tags:
        text += f' #{name}'
    return text


def _normalize_topics(topics: Optional[List[Any]]) -> List[str]:
    out: List[str] = []
    for tp in topics or []:
        if isinstance(tp, str):
            name = tp.lstrip('#').rstrip('#').strip()
        elif isinstance(tp, dict):
            name = (tp.get('name') or tp.get('topic') or '').strip()
        else:
            name = ''
        if name:
            out.append(name)
    return out


# ════════════════════════════════════════════════════════════════════════════
# 视频 submit 体(字段名 [确认]，取值多为 [待校准])
# ════════════════════════════════════════════════════════════════════════════
def build_video_submit(p: Dict[str, Any]) -> Dict[str, Any]:
    """组 /video/pc/submit 的请求体(不含登录态 ph，那个由 protocol 层注入)。

    p 需含 upload 回执真值：file_id/cover_key/media_id(缺则报错，不编造)。"""
    file_id = p.get('file_id')
    cover_key = p.get('cover_key')
    if not file_id:
        raise ValueError('build_video_submit: 缺 fileId（应来自 upload/finish 回执，不可编造）')
    if not cover_key:
        raise ValueError('build_video_submit: 缺 coverKey（应来自封面上传/finish 回执，不可编造）')

    body: Dict[str, Any] = {
        'caption': build_caption(p.get('title', ''), p.get('desc', ''), p.get('topics')),
        'fileId': file_id,
        'coverKey': cover_key,
        'photoStatus': resolve_photo_status(p.get('visibility', 'public')),
        'coverType': CALIBRATE_COVER_TYPE,
        'photoType': CALIBRATE_PHOTO_TYPE,
    }
    # 下面这些"有才带"，没有别塞空值污染请求体(跟真机对不上就是破绽)。
    if p.get('media_id'):
        body['mediaId'] = p['media_id']
    if p.get('cover_time_stamp') is not None:
        body['coverTimeStamp'] = int(p['cover_time_stamp'])
    if CALIBRATE_DOMAIN:
        body['domain'] = CALIBRATE_DOMAIN
    if p.get('publish_time'):        # 定时发布(毫秒时间戳)
        body['publishTime'] = int(p['publish_time'])
    loc = p.get('location') or {}
    if loc.get('latitude') is not None and loc.get('longitude') is not None:
        body['latitude'] = loc['latitude']
        body['longitude'] = loc['longitude']
    return body


# ════════════════════════════════════════════════════════════════════════════
# 图文/图集 submit 体 —— 全 [推断]，路径/字段名开源无确证。由 config.atlas_calibrated() 门控，
# 这里只按"与视频同构、图集无封面、提交图片 key 数组"的最合理猜测拼，真机校准后再定型。
# ════════════════════════════════════════════════════════════════════════════
def build_atlas_submit(p: Dict[str, Any]) -> Dict[str, Any]:
    """组图文 submit 体。images=[{key/id,...}] 必须来自上传回执，缺则报错。
    ⚠️ 字段名(atlasInfo/imageList/coverKey…)是推断，真机抓包前不要当权威。"""
    images = p.get('images') or []
    keys = [img.get('key') or img.get('id') or img.get('file_id') for img in images]
    keys = [k for k in keys if k]
    if not keys:
        raise ValueError('build_atlas_submit: 图片列表缺 key/id（应来自上传回执，不可编造）')

    body: Dict[str, Any] = {
        'caption': build_caption(p.get('title', ''), p.get('desc', ''), p.get('topics')),
        'photoStatus': resolve_photo_status(p.get('visibility', 'public')),
        # [推断] 图集提交图片资源 key 数组；字段名待真机确认(可能叫 imageList/atlasKeys/keys…)
        'atlasKeys': keys,
    }
    if CALIBRATE_DOMAIN:
        body['domain'] = CALIBRATE_DOMAIN
    if p.get('publish_time'):
        body['publishTime'] = int(p['publish_time'])
    return body


# ════════════════════════════════════════════════════════════════════════════
# 序列化：签名(若走页内加签)与发送必须喂同一个串
# ════════════════════════════════════════════════════════════════════════════
def serialize(obj: Dict[str, Any]) -> str:
    """紧凑序列化(无空格)。若走页内 sig3 加签，喂签名的串必须跟 POST 出去的一字不差。"""
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
