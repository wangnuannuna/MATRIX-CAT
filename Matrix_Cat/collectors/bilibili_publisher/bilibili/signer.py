# -*- coding: utf-8 -*-
"""B站 wbi 签名 —— 自算,纯函数、离线可测。

B站近年给 api.bilibili.com/x/web-interface/* 一批读接口加了 wbi:请求要带 w_rid + wts。
算法是确定性的,不用逆 JS、不用浏览器:
  1. 从 nav 接口拿 wbi_img.img_url / sub_url,取文件名(去扩展名)= img_key / sub_key(各 32 位 hex);
  2. img_key+sub_key 拼成 64 位串,按固定重排表 MIXIN_KEY_ENC_TAB 取前 32 位 = mixin_key;
  3. 业务参数 + wts(秒级时间戳)按 key 排序,value 过滤掉 !'()* 字符,urlencode 后接上 mixin_key,
     md5 得 w_rid。

⚠ 投稿链路(preupload/upos/cover/add)靠 cookie+csrf,**不需要 wbi**。这个模块是给读接口(如带 wbi
的账号信息/数据)备的,也顺便证明"自算签名"能力。取 key 失败就 raise,不编造。
"""
import hashlib
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlencode

# 固定重排表(B站 web wbi 混淆密钥用,值稳定;改了会一起改前端,真变了 selftest 会先炸)
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


class WbiKeyError(Exception):
    """从 nav 拿不到 wbi_img 的 img/sub key —— 登录态或响应异常,别拿空 key 硬算。"""


def get_mixin_key(orig: str) -> str:
    """img_key+sub_key(64 hex) → 按重排表取前 32 位 mixin_key。"""
    return ''.join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _key_from_url(url: str) -> str:
    """'https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png' → '7cd0...077c'。"""
    if not url:
        return ''
    return url.rsplit('/', 1)[-1].split('.', 1)[0]


def wbi_keys_from_nav(nav_json: dict) -> Tuple[str, str]:
    """从 nav 响应抽 (img_key, sub_key)。拿不到就 raise WbiKeyError。"""
    data = (nav_json or {}).get('data') or {}
    wbi = data.get('wbi_img') or {}
    img_key = _key_from_url(wbi.get('img_url', ''))
    sub_key = _key_from_url(wbi.get('sub_url', ''))
    if not img_key or not sub_key:
        raise WbiKeyError(f'nav 响应里没有 wbi_img 的 img/sub key(img={img_key!r} sub={sub_key!r})')
    return img_key, sub_key


def _filter_value(v) -> str:
    """value 里去掉 !'()* (B站 web 端 urlencode 前的清洗,不去会签不对)。"""
    return ''.join(ch for ch in str(v) if ch not in "!'()*")


def sign_wbi(params: Dict, img_key: str, sub_key: str, *, wts: Optional[int] = None) -> Dict:
    """给 params 补上 wts + w_rid,返回新 dict(不改原 params)。wts 可注入,便于离线单测。"""
    mixin_key = get_mixin_key(img_key + sub_key)
    ts = int(time.time()) if wts is None else int(wts)
    signed = dict(params)
    signed['wts'] = ts
    ordered = {k: _filter_value(signed[k]) for k in sorted(signed)}
    query = urlencode(ordered)
    signed['w_rid'] = hashlib.md5((query + mixin_key).encode('utf-8')).hexdigest()
    return signed
