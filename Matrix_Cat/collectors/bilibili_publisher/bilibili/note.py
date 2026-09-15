# -*- coding: utf-8 -*-
"""B站发布体构造。纯函数、无 I/O、可单测。

字段来源:视频 add/v3 的 copyright/cover/title/tid/tag/desc/videos[filename,cid] 属公开逆向 [确认];
但**分区 tid 的取值、图文动态 create_draw 的字段**未逐项真机核对,门控 + fail-loud。

fail-loud 原则(跟 kuaishou/note.py 一致):
  · filename/cid/cover 这类每请求真值必须来自上传回执,缺了报错,绝不编造;
  · B站 web 投稿**不支持仅自己/好友可见**,可见性 private/friends 直接拒绝(别把它当公开静默发出去);
  · tid 无安全默认 —— 用占位默认但打醒目告警(投错分区顶多被降权/移动,无隐私风险);
  · tag 必填 —— 没话题就兜底一个并告警(B站空 tag 会被服务端拒)。
"""
import json
import logging
from typing import Any, Dict, List, Optional

from . import config

logger = logging.getLogger(config.PLATFORM)


class VisibilityUnsupportedError(Exception):
    """B站 web 标准投稿不支持'仅自己/好友可见',拒绝按公开盲发(避免误把私密内容公开)。"""


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


def assert_public(visibility: str) -> None:
    """可见性闸:B站 web 投稿只有公开一档。private/friends 直接拒绝,不静默升级成公开。"""
    v = (visibility or 'public').strip().lower()
    if v in ('public', '公开', ''):
        return
    if v in ('private', 'self', 'self_only', '仅自己', 'friends', 'friend', '好友'):
        raise VisibilityUnsupportedError(
            f"可见性 {visibility!r}:B站 web 标准投稿不支持仅自己/好友可见,拒绝按公开发出去。"
            "只发公开就用 --visibility public;确需私密请走 B站'私密上传'另行校准。")
    logger.warning(f'未知可见性 {visibility!r},按 public 处理')


def resolve_tags(topics: Optional[List[Any]]) -> str:
    """话题 → B站 tag 串(逗号分隔,最多 12 个)。空则兜底一个并告警(B站 tag 必填)。"""
    tags = _normalize_topics(topics)[:12]
    if not tags:
        logger.warning('未给话题(--topics),B站投稿 tag 必填,兜底填 "日常";建议显式给 tag。')
        return '日常'
    return ','.join(tags)


def resolve_tid(p: Dict[str, Any]) -> int:
    """分区 tid:优先 p['tid'],否则 config.DEFAULT_TID(占位默认)并告警。"""
    tid = p.get('tid')
    if tid:
        return int(tid)
    logger.warning(f'未指定分区 --tid,用占位默认 tid={config.DEFAULT_TID}([待校准],'
                   f'建议按目标分区显式 --tid,投错分区会被降权/移动)。')
    return config.DEFAULT_TID


# ════════════════════════════════════════════════════════════════════════════
# 视频投稿体(add/v3;字段 [确认],tid 取值 [待校准])
# ════════════════════════════════════════════════════════════════════════════
def build_video_add(p: Dict[str, Any]) -> Dict[str, Any]:
    """组 add/v3 请求体(csrf 由 client 放 query,不在 body)。

    p 需含上传回执真值:filename(upos key)/cid(biz_id)/cover(封面 url),缺则报错不编造。"""
    assert_public(p.get('visibility', 'public'))
    filename = p.get('filename')
    cid = p.get('cid')
    cover = p.get('cover')
    if not filename:
        raise ValueError('build_video_add: 缺 filename(应来自 upos 上传回执,不可编造)')
    if not cover:
        raise ValueError('build_video_add: 缺 cover 封面 url(应来自封面上传回执,不可编造)')

    title = (p.get('title') or '').strip() or (p.get('desc') or '').strip()[:80] or '未命名'
    desc = (p.get('desc') or '').strip()
    source = (p.get('source') or '').strip()
    copyright_ = 2 if source else 1     # 有来源=转载(2),否则自制(1)

    body: Dict[str, Any] = {
        'copyright': copyright_,
        'source': source,
        'cover': cover,
        'title': title[:80],            # B站标题上限 80 字
        'tid': resolve_tid(p),
        'tag': resolve_tags(p.get('topics')),
        'desc_format_id': 0,
        'desc': desc,
        'recreate': -1,
        'dynamic': '',
        'interactive': 0,
        'videos': [{'filename': filename, 'title': '', 'desc': '', 'cid': cid}],
        'act_reserve_create': 0,
        'no_disturbance': 0,
        'no_reprint': 0 if source else 1,   # 自制默认禁止转载
        'subtitle': {'open': 0, 'lan': ''},
        'dolby': 0,
        'lossless_music': 0,
        'web_os': 2,
    }
    if cid:
        body['videos'][0]['cid'] = cid
    return body


# ════════════════════════════════════════════════════════════════════════════
# 图文动态体(create_draw;全 [待校准],由 config.dynamic_calibrated() 门控)
# ════════════════════════════════════════════════════════════════════════════
def build_dynamic_content(title: str, desc: str, topics: Optional[List[Any]]) -> str:
    """动态正文:标题/正文拼一起,话题以 #话题# 内联。"""
    parts = []
    t = (title or '').strip()
    d = (desc or '').strip()
    if t:
        parts.append(t)
    if d and d != t:
        parts.append(d)
    text = '\n'.join(parts)
    for name in _normalize_topics(topics):
        text += f' #{name}#'
    return text.strip()


def build_dynamic_draw(p: Dict[str, Any]) -> Dict[str, Any]:
    """组 create_draw 表单(csrf 由 client 补)。pics 必须来自上传回执,缺则报错。
    ⚠ 字段名/取值 [待校准],由 config.dynamic_calibrated() 门控放行。"""
    pics = p.get('pics') or []
    pictures = [{'img_src': i['img_src'], 'img_width': i.get('img_width', 0),
                 'img_height': i.get('img_height', 0), 'img_size': i.get('img_size', 0)}
                for i in pics if i.get('img_src')]
    if not pictures:
        raise ValueError('build_dynamic_draw: 图片列表缺 img_src(应来自动态图上传回执,不可编造)')
    content = build_dynamic_content(p.get('title', ''), p.get('desc', ''), p.get('topics'))
    return {
        'biz': '3',
        'category': '3',
        'type': '0',
        'pictures': serialize(pictures),
        'title': '',
        'description': content,
        'content': content,
        'from': 'create.dynamic.web',
        'up_choose_comment': '0',
        'up_close_danmu': '0',
        'at_uids': '',
        'at_control': '',
        'setting': serialize({'copy_forbidden': 0, 'cachedTime': 0}),
    }


def serialize(obj: Any) -> str:
    """紧凑序列化(无空格)。"""
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
