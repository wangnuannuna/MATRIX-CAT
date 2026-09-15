# -*- coding: utf-8 -*-
"""
小红书「创建笔记」请求体的构造（纯函数，无 I/O，可单测）。

    POST https://edith.xiaohongshu.com/web_api/sns/v2/note   body = serialize(note)
    note = { common, image_info, video_info }   # image_info / video_info 二选一非空

结构不是拍脑袋写的：2026-06-22 用真机 DevTools 抓了真实 create_note 请求体（图文 + 视频各一条），
逐字段 diff 后把每一处都对齐到真机，现在给定文件元数据后与真机**逐字节一致**。所以下面这些
CALIBRATE_* 常量和字段顺序都别乱改——改了就跟真机对不上，等于自曝一个"不是真浏览器发的"的破绽。

fail-loud 原则：file_id / trace_id 这类"每请求真值"必须由上传回执/permit 提供，缺了就报错，
**绝不凭空编造**。宽高/编码元数据这类拿不到才退占位（服务端会自行纠正）。

（本文件源自 HuiMei 引擎 platforms/xhs_common/note_contract.py，是两套发布器 note_builder 的
合并并集经真机校准后的权威版；搬到这作为独立发布器的 note 体，逻辑一字不改。）
"""
import json
import os
from typing import Any, Dict, List, Optional


# ════════════════════════════════════════════════════════════════════════════
# 默认 note 变体（单一来源）
# ════════════════════════════════════════════════════════════════════════════
# 'full'    = 蚁小二完整结构，与真机最接近（默认）；
# 'skeleton'= 精简骨架（老协议版），仅在真机证实需要精简时用 XHS_NOTE_VARIANT=skeleton。
def resolve_default_variant() -> str:
    v = (os.getenv('XHS_NOTE_VARIANT', '') or '').strip().lower()
    return v if v in ('full', 'skeleton') else 'full'


# ════════════════════════════════════════════════════════════════════════════
# CALIBRATE 区：所有"真机抓包校准过"的常量集中在这
# ════════════════════════════════════════════════════════════════════════════

# common.source —— 真机确认：{"type":"web","ids":"","extraInfo":"{\"systemId\":\"web\"}"}
# 内层 extraInfo 必须紧凑分隔(冒号后无空格)，否则跟真机对不上。
CALIBRATE_SOURCE = json.dumps(
    {"type": "web", "ids": "",
     "extraInfo": json.dumps({"systemId": "web"}, separators=(',', ':'))},
    ensure_ascii=False, separators=(',', ':'))

CALIBRATE_SOURCE_LITERAL = '{"type":"web","ids":"","extraInfo":"{\\"systemId\\":\\"web\\"}"}'

# 图片宽高兜底占位。蚁小二写死 4096(服务端按实际图纠正)；正常路径由上传层量真实像素回填。
CALIBRATE_IMAGE_FALLBACK_WH = 4096

# 图片元素附属字段 —— 真机确认：
#   metadata = {"source":-1}
#   stickers = {"version":2,"floating":[]}   ← 真机键名是复数 stickers，version 在前
#   extra_info_json = {"mimeType":<真MIME>,"image_metadata":{"bg_color":"","origin_size":<字节/1024>}}
CALIBRATE_IMAGE_EXTRA_INFO_JSON = json.dumps(
    {"mimeType": "image/jpeg", "image_metadata": {"bg_color": "", "origin_size": 0}},
    separators=(',', ':'))
CALIBRATE_IMAGE_METADATA = {'source': -1}
CALIBRATE_IMAGE_STICKER = {'version': 2, 'floating': []}

# common.capa_trace_info.contextJson —— 真机确认(推荐标题/话题埋点；自动化没用推荐标题)。
CALIBRATE_CAPA_TRACE_CONTEXT = json.dumps({
    "recommend_title": {"recommend_title_id": "", "is_use": 3, "used_index": -1},
    "recommendTitle": [],
    "recommend_topics": {"used": []},
}, separators=(',', ':'))

# 无商品/无业务关联时为空。
CALIBRATE_GOODS_INFO: Dict[str, Any] = {}
CALIBRATE_BIZ_RELATIONS: List[Any] = []

# 视频/封面宽高兜底(逆向占位，真实由 ffprobe/cv2 探测)。
CALIBRATE_VIDEO_FALLBACK_W = 720
CALIBRATE_VIDEO_FALLBACK_H = 1280
CALIBRATE_COVER_FALLBACK_W = 1080
CALIBRATE_COVER_FALLBACK_H = 1980

# 视频编码元数据(每文件值，真实由探测提供；拿不到退这些 authentic-shaped 占位)。
CALIBRATE_VIDEO_FRAME_RATE = 30
CALIBRATE_VIDEO_FORMAT = 'AVC'
CALIBRATE_VIDEO_BITRATE = 0
CALIBRATE_VIDEO_COLOUR = 'BT.709'
CALIBRATE_VIDEO_ROTATION = 0
CALIBRATE_AUDIO_FORMAT = 'AAC'
CALIBRATE_AUDIO_CHANNELS = 1
CALIBRATE_AUDIO_BITRATE = 0
CALIBRATE_AUDIO_SAMPLING_RATE = 44100

# video_info.video_preview_type —— 真机：竖屏=full_vertical_screen；横屏真值未抓到(按命名推)。
CALIBRATE_VIDEO_PREVIEW_VERTICAL = 'full_vertical_screen'
CALIBRATE_VIDEO_PREVIEW_HORIZONTAL = 'full_horizontal_screen'

# business_binds 的 bizType —— 真机：图文与视频 web 创作平台都用 0。
CALIBRATE_BIZTYPE_IMAGE = 0
CALIBRATE_BIZTYPE_VIDEO = 13  # 仅 skeleton 老变体用；web full 视频实测也是 0

# 可见性映射 —— 真机确认 public=0 / self_only=1；mutual_friends=2 是唯一未确认的占位。
CALIBRATE_VISIBILITY_MAP = {
    'public': 0,
    'self_only': 1,
    'mutual_friends': 2,   # 未真机确认
}

# 原创声明片段(视频实测有；图文能否带、bizId 取值待确认)。
CALIBRATE_ORIGINAL_STATEMENT = {
    'type': 'ORIGINAL_STATEMENT',
    'relationList': [{'bizType': 'ORIGINAL_STATEMENT', 'bizId': '', 'extraInfo': '{}'}],
}


# ════════════════════════════════════════════════════════════════════════════
# 入参归一化
# ════════════════════════════════════════════════════════════════════════════

def _normalize_topics(topics: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """topics 兼容 dict 列表({name/topic,id,link}) 或 str 列表('#话题#' / '话题')。"""
    out: List[Dict[str, Any]] = []
    for t in topics or []:
        if isinstance(t, str):
            name = t.lstrip('#').rstrip('#').strip()
            if name:
                out.append({'name': name, 'id': '', 'link': ''})
        elif isinstance(t, dict):
            name = t.get('name') or t.get('topic') or ''
            if name:
                out.append({'name': name, 'id': t.get('id', ''), 'link': t.get('link', '')})
    return out


def _normalize_mentions(mentions: Optional[List[Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in mentions or []:
        if isinstance(m, dict):
            nick = m.get('nickname') or m.get('name') or m.get('user_nickname') or ''
            out.append({
                'nickname': nick,
                'name': m.get('name') or nick,
                'user_id': m.get('user_id', ''),
            })
    return out


class VisibilityCalibrationError(Exception):
    """可见性档位没经真机确认(mutual_friends)，拒绝按占位盲发——避免"仅互关"误发成公开。"""


def _mutual_calibrated() -> bool:
    return (os.getenv('XHS_VISIBILITY_MUTUAL_CALIBRATED', '') or '').strip().lower() in ('1', 'true', 'yes')


def _resolve_visibility(visibility_type: Optional[int], visibility: Optional[str]) -> int:
    """显式 int 优先；否则按字符串查表；都没有默认 public(0)。

    public=0 / self_only=1 已真机确认；mutual_friends=2 未确认，按 2 盲发有把"仅互关"发成"公开"的风险，
    所以未校准时对 mutual_friends 直接抛错。真机确认后设 XHS_VISIBILITY_MUTUAL_CALIBRATED=1 放行。
    """
    if visibility_type is not None:
        return int(visibility_type)
    if visibility is not None:
        if visibility == 'mutual_friends' and not _mutual_calibrated():
            raise VisibilityCalibrationError(
                "可见性'仅互关'(mutual_friends)的档位值未经真机校准，拒绝按占位(2)盲发——"
                "可能把仅互关误发成公开。真机确认后设 XHS_VISIBILITY_MUTUAL_CALIBRATED=1。")
        return int(CALIBRATE_VISIBILITY_MAP.get(visibility, CALIBRATE_VISIBILITY_MAP['public']))
    return CALIBRATE_VISIBILITY_MAP['public']


# ════════════════════════════════════════════════════════════════════════════
# 文本 / 结构化标签拼装
# ════════════════════════════════════════════════════════════════════════════

def _inline_desc(desc: str, topics: List[Dict[str, Any]],
                 mentions: List[Dict[str, Any]]) -> str:
    """把话题/@内联进正文文本(和结构化 hash_tag/ats 并存，跟蚁小二一致)。"""
    out = desc or ''
    for t in topics or []:
        name = t.get('name') or ''
        if name:
            out += f' #{name}[话题]#'
    for m in mentions or []:
        name = m.get('nickname') or m.get('name') or ''
        if name:
            out += f' @{name}'
    return out


def _hash_tag(topics: List[Dict[str, Any]], variant: str) -> List[Dict[str, Any]]:
    out = []
    for t in topics or []:
        name = t.get('name') or ''
        if not name:
            continue
        if variant == 'skeleton':
            out.append({'id': t.get('id', ''), 'name': name, 'type': 'topic'})
        else:
            out.append({'id': t.get('id', ''), 'name': name,
                        'link': t.get('link', ''), 'type': 'topic'})
    return out


def _ats(mentions: List[Dict[str, Any]], variant: str) -> List[Dict[str, Any]]:
    """@ 用户。full 版需要 user_id(没有的丢弃，跟蚁小二一致)。"""
    out = []
    for m in mentions or []:
        if variant == 'full' and not m.get('user_id'):
            continue
        out.append({
            'nickname': m.get('nickname') or m.get('name') or '',
            'name': m.get('name') or m.get('nickname') or '',
            'user_id': m.get('user_id', ''),
        })
    return out


def _post_loc(location: Optional[Dict[str, Any]], poi_type_field: str,
              variant: str) -> Optional[Dict[str, Any]]:
    empty = {} if variant == 'skeleton' else None
    if not location:
        return empty
    poi_id = location.get('poi_id') or location.get('poiOid')
    if not poi_id:
        return empty
    return {
        'poi_id': poi_id,
        'name': location.get('name', ''),
        'poi_type': location.get('poi_type') or location.get(poi_type_field, ''),
        'subname': location.get('subname', ''),
    }


# ════════════════════════════════════════════════════════════════════════════
# business_binds 构造
# ════════════════════════════════════════════════════════════════════════════

def _business_binds_image(p: Dict[str, Any], variant: str, is_original: bool) -> Any:
    if variant == 'skeleton':
        return p.get('business_binds') or {
            'version': 1, 'bizType': CALIBRATE_BIZTYPE_IMAGE, 'noteId': '', 'bind': []}
    # full：真机键序一致。notePostTiming 未定时发布时真机是 {}(不带 postTime)。
    binds: Dict[str, Any] = {
        "version": 1, "noteId": 0, "bizType": CALIBRATE_BIZTYPE_IMAGE,
        "noteOrderBind": {},
        "notePostTiming": ({"postTime": p['post_time']} if p.get('post_time') else {}),
        "noteCollectionBind": {"id": p.get('collection_id', '')},
        "noteSketchCollectionBind": {"id": ""},
        "coProduceBind": {"enable": True},
        "noteCopyBind": {"copyable": True},
        "interactionPermissionBind": {"commentPermission": 0},
        "optionRelationList": [],
    }
    if is_original:
        binds["optionRelationList"].append(CALIBRATE_ORIGINAL_STATEMENT)
    return json.dumps(binds, ensure_ascii=False, separators=(',', ':'))


def _business_binds_video(p: Dict[str, Any], variant: str, is_original: bool) -> Any:
    if variant == 'skeleton':
        return p.get('business_binds') or {
            'version': 1, 'bizType': CALIBRATE_BIZTYPE_VIDEO, 'noteId': '', 'bind': []}
    # full：真机确认视频 business_binds 与图文逐字节一致，直接复用。
    return _business_binds_image(p, variant, is_original)


# ════════════════════════════════════════════════════════════════════════════
# 权威 build_image_note / build_video_note
# ════════════════════════════════════════════════════════════════════════════

def build_image_note(p: Dict[str, Any], *, variant: Optional[str] = None) -> Dict[str, Any]:
    """图文笔记体。每张图必须有 file_id(来自上传回执)，缺则报错——不编造。"""
    if variant is None:
        variant = resolve_default_variant()
    topics = _normalize_topics(p.get('topics') or p.get('tags'))
    mentions = _normalize_mentions(p.get('mentions') or p.get('ats'))
    is_original = bool(int(p.get('is_original', 0) or 0))
    vis_type = _resolve_visibility(p.get('visibility_type'), p.get('visibility'))
    desc = _inline_desc(p.get('desc', ''), topics, mentions)

    images = []
    for img in p.get('images') or []:
        if 'file_id' not in img or not img['file_id']:
            raise ValueError('build_image_note: 图片缺 file_id（应来自上传回执，不可编造）')
        if variant == 'skeleton':
            images.append({
                'file_id': img['file_id'],
                'width': int(img.get('width') or 0),
                'height': int(img.get('height') or 0),
                'metadata': img.get('metadata', {}),
                'extra_info_json': img.get('extra_info_json', '{}'),
                'sticker': img.get('sticker', {}),
            })
        else:
            # full：字段顺序与真机一致 file_id,width,height,metadata,stickers,extra_info_json。
            images.append({
                'file_id': img['file_id'],
                'width': int(img.get('width') or CALIBRATE_IMAGE_FALLBACK_WH),
                'height': int(img.get('height') or CALIBRATE_IMAGE_FALLBACK_WH),
                'metadata': dict(CALIBRATE_IMAGE_METADATA),
                'stickers': dict(CALIBRATE_IMAGE_STICKER),
                'extra_info_json': img.get('extra_info_json') or CALIBRATE_IMAGE_EXTRA_INFO_JSON,
            })

    if variant == 'skeleton':
        common = {
            'type': 'normal',
            'title': p.get('title', '') or '',
            'note_id': '',
            'desc': desc,
            'source': CALIBRATE_SOURCE_LITERAL,
            'business_binds': _business_binds_image(p, variant, is_original),
            'ats': _ats(mentions, variant),
            'hash_tag': _hash_tag(topics, variant),
            'post_loc': _post_loc(p.get('location'), 'recommendType', variant),
            'privacy_info': {'op_type': 1, 'type': vis_type},
        }
    else:
        # full：键序与真机逐字段一致。
        common = {
            'type': 'normal',
            'note_id': '',
            'source': CALIBRATE_SOURCE,
            'title': p.get('title', '') or '',
            'desc': desc,
            'ats': _ats(mentions, variant),
            'hash_tag': _hash_tag(topics, variant),
            'business_binds': _business_binds_image(p, variant, is_original),
            'privacy_info': {'op_type': 1, 'type': vis_type, 'user_ids': []},
            'goods_info': dict(CALIBRATE_GOODS_INFO),
            'biz_relations': list(CALIBRATE_BIZ_RELATIONS),
            'capa_trace_info': {'contextJson': CALIBRATE_CAPA_TRACE_CONTEXT},
        }
        # post_loc：真机无地点时不带该键，有地点才插。
        loc = _post_loc(p.get('location'), 'recommendType', variant)
        if loc:
            common['post_loc'] = loc
    return {'common': common, 'image_info': {'images': images}, 'video_info': None}


def build_video_note(p: Dict[str, Any], *, variant: Optional[str] = None) -> Dict[str, Any]:
    """视频笔记体。video/cover 必须有 file_id(来自上传回执)，缺则报错——不编造。"""
    if variant is None:
        variant = resolve_default_variant()
    topics = _normalize_topics(p.get('topics') or p.get('tags'))
    mentions = _normalize_mentions(p.get('mentions') or p.get('ats'))
    is_original = bool(int(p.get('is_original', 0) or 0))
    vis_type = _resolve_visibility(p.get('visibility_type'), p.get('visibility'))
    desc = _inline_desc(p.get('desc', ''), topics, mentions)

    if variant == 'skeleton':
        video_info = p.get('video_info')
        if video_info is None:
            raise ValueError("build_video_note(variant='skeleton') 需直接传 p['video_info']")
        source = CALIBRATE_SOURCE_LITERAL
    else:
        video_info = _build_video_info(p)
        source = CALIBRATE_SOURCE

    if variant == 'skeleton':
        common = {
            'type': 'video',
            'title': p.get('title', '') or '',
            'note_id': '',
            'desc': desc,
            'source': source,
            'business_binds': _business_binds_video(p, variant, is_original),
            'ats': _ats(mentions, variant),
            'hash_tag': _hash_tag(topics, variant),
            'post_loc': _post_loc(p.get('location'), 'source', variant),
            'privacy_info': {'op_type': 1, 'type': vis_type},
        }
    else:
        common = {
            'type': 'video',
            'note_id': '',
            'source': source,
            'title': p.get('title', '') or '',
            'desc': desc,
            'ats': _ats(mentions, variant),
            'hash_tag': _hash_tag(topics, variant),
            'business_binds': _business_binds_video(p, variant, is_original),
            'privacy_info': {'op_type': 1, 'type': vis_type, 'user_ids': []},
            'goods_info': dict(CALIBRATE_GOODS_INFO),
            'biz_relations': list(CALIBRATE_BIZ_RELATIONS),
            'capa_trace_info': {'contextJson': CALIBRATE_CAPA_TRACE_CONTEXT},
        }
        loc = _post_loc(p.get('location'), 'source', variant)
        if loc:
            common['post_loc'] = loc
    return {'common': common, 'image_info': None, 'video_info': video_info}


def _codec_metadata(video: Dict[str, Any], vw: int, vh: int, dur_ms: int) -> Dict[str, Any]:
    """composite_metadata / original_metadata 共用的 {video, audio} 编码元数据块(真机键序)。"""
    a = video.get('audio') or {}
    return {
        'video': {
            'bitrate': int(video.get('bitrate') or CALIBRATE_VIDEO_BITRATE),
            'colour_primaries': video.get('colour_primaries') or CALIBRATE_VIDEO_COLOUR,
            'duration': dur_ms,
            'format': video.get('format') or CALIBRATE_VIDEO_FORMAT,
            'frame_rate': int(video.get('frame_rate') or CALIBRATE_VIDEO_FRAME_RATE),
            'height': vh,
            'matrix_coefficients': video.get('matrix_coefficients') or CALIBRATE_VIDEO_COLOUR,
            'rotation': int(video.get('rotation') or CALIBRATE_VIDEO_ROTATION),
            'transfer_characteristics': video.get('transfer_characteristics') or CALIBRATE_VIDEO_COLOUR,
            'width': vw,
        },
        'audio': {
            'bitrate': int(a.get('bitrate') or CALIBRATE_AUDIO_BITRATE),
            'channels': int(a.get('channels') or CALIBRATE_AUDIO_CHANNELS),
            'duration': int(a.get('duration_ms') or a.get('duration') or dur_ms),
            'format': a.get('format') or CALIBRATE_AUDIO_FORMAT,
            'sampling_rate': int(a.get('sampling_rate') or CALIBRATE_AUDIO_SAMPLING_RATE),
        },
    }


def _build_video_info(p: Dict[str, Any]) -> Dict[str, Any]:
    """full 变体：依 video/cover 组装完整 video_info(2026-06-22 真机逐字段校准)。"""
    video = p.get('video') or {}
    cover = p.get('cover') or {}
    if not video.get('file_id'):
        raise ValueError('build_video_note: video 缺 file_id（应来自上传回执，不可编造）')
    if not cover.get('file_id'):
        raise ValueError('build_video_note: cover 缺 file_id（应来自封面上传回执，不可编造）')

    vw = int(video.get('width') or CALIBRATE_VIDEO_FALLBACK_W)
    vh = int(video.get('height') or CALIBRATE_VIDEO_FALLBACK_H)
    dur_sec = float(video.get('duration') or 0)          # 入参单位=秒
    dur_ms = int(round(dur_sec * 1000))                  # composite_metadata 用毫秒

    codec = _codec_metadata(video, vw, vh, dur_ms)

    preview_type = (CALIBRATE_VIDEO_PREVIEW_VERTICAL if vh >= vw
                    else CALIBRATE_VIDEO_PREVIEW_HORIZONTAL)

    # cover：真机键序 fileid, file_id, height, width, frame, stickers, fonts, extra_info_json。
    # 视频封面的 stickers 用 neptune(不是图片的 floating)。
    cover_obj = {
        'fileid': cover['file_id'],
        'file_id': cover['file_id'],
        'height': int(cover.get('height') or vh),
        'width': int(cover.get('width') or vw),
        'frame': {'ts': 0, 'is_user_select': False, 'is_upload': False},
        'stickers': {'version': 2, 'neptune': []},
        'fonts': [],
        'extra_info_json': '{}',
    }

    # segments：单视频未切片 → 1 个 item；item.duration 单位=秒(真机 1.6 vs composite 1600ms)。
    segments_item = {
        'mute': 0, 'speed': 1, 'start': 0,
        'duration': dur_sec,
        'transcoded': 0, 'media_source': 1,
        'original_metadata': _codec_metadata(video, vw, vh, dur_ms),
    }

    return {
        'fileid': video['file_id'],
        'file_id': video['file_id'],
        'format_width': vw,
        'format_height': vh,
        'video_preview_type': preview_type,
        'composite_metadata': codec,
        'timelines': [],
        'cover': cover_obj,
        'chapters': [],
        'chapter_sync_text': False,
        'segments': {'count': 1, 'need_slice': False, 'items': [segments_item]},
        'entrance': 'web',
        'pk_cover_biz_relations': [],
    }


# ════════════════════════════════════════════════════════════════════════════
# 序列化：签名与发送必须用同一字符串
# ════════════════════════════════════════════════════════════════════════════

def serialize(note_obj: Dict[str, Any]) -> str:
    """铁律：喂给 _webmsxyw 签名的串，必须跟实际 POST 出去的串一字不差，否则验签必挂。
    调用方务必：body_str = serialize(note)；先 sign(path, body_str) 再 send(body_str)。"""
    return json.dumps(note_obj, ensure_ascii=False, separators=(',', ':'))
