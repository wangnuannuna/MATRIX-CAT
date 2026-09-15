# -*- coding: utf-8 -*-
"""crawler_spider 全局配置包。用法：

    from config import settings           # 拿整个模块
    from config import BROWSER_UA, resolve_proxy   # 或直接拿名字
"""
from . import settings
from .settings import *  # noqa: F401,F403
