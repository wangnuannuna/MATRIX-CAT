# -*- coding: utf-8 -*-
"""
发布页预热 CLI —— 直连把发布页静态 JS/CSS 填进本地缓存,之后真机发布页秒开。

    python -m asset_cache.publish.prewarm xhs            # 单平台(未登录,只抓公共 JS)
    python -m asset_cache.publish.prewarm xhs --headful  # 先手动扫码登录,再抓全发布页编辑器/上传组件 JS
    python -m asset_cache.publish.prewarm all

多数情况不用手动跑——open_session 已挂好,第一次真机发布就会自动把发布页缓存填上。要跑真机用 conda313 那个 python。
"""
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]   # asset_cache/publish/prewarm.py → 项目根
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from asset_cache._prewarm_core import run_cli  # noqa: E402

if __name__ == '__main__':
    asyncio.run(run_cli('publish'))
