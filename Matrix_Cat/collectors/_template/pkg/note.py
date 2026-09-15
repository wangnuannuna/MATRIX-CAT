# -*- coding: utf-8 -*-
"""发布体：把 标题/正文/话题/可见性/素材id 拼成平台 create 接口要的 JSON。

参考已落地：collectors/xhs_publisher/xhs/note.py(小红书 note 体，真机逐字节校准)。
关键：serialize 出的串要跟签名/发送同串；file_id 这类真值必须来自上传回执，缺了 fail-loud，别编造。
"""
import json


def build_image_payload(images, meta):
    """images=[{file_id,width,height,...}]，meta 含 title/desc/topics/visibility。→ 平台图文 body(dict)。"""
    raise NotImplementedError("按平台字段契约实现；参考 xhs/note.py 的 build_image_note")


def build_video_payload(video, cover, meta):
    """video/cover={file_id,...}。→ 平台视频 body(dict)。"""
    raise NotImplementedError("按平台字段契约实现；参考 xhs/note.py 的 build_video_note")


def serialize(obj) -> str:
    """紧凑序列化(无空格)。签名与发送必须喂同一个串。"""
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
