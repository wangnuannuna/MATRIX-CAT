# -*- coding: utf-8 -*-
"""
缓存目录:asset_cache/_store/<scope>/<platform>/(按「作用域(登录页/发布页)+ 平台」分,登录/发布/预热共用,与 cwd 无关)。
缓存按 URL 存,跟账号无关——一份填好,全矩阵所有号的浏览器都命中。_store/ 是运行时产物(可上百 MB),
不用入库,靠预热或首次使用自动重建。
"""
from pathlib import Path


def get_store_dir(platform: str, scope: str = 'login') -> str:
    p = Path(__file__).resolve().parent / '_store' / scope / (platform or 'default')
    p.mkdir(parents=True, exist_ok=True)
    return str(p)
