# -*- coding: utf-8 -*-
"""
登录页预热 CLI —— 直连把登录页静态 JS/CSS 填进本地缓存,之后真机扫码登录命中本地、绕代理、秒出码。

    python -m asset_cache.login.prewarm xhs          # 单平台
    python -m asset_cache.login.prewarm all          # 全部已注册(xhs/douyin/ks)
前端改版(登录又变慢)后再跑一次即可。要跑真机用带 cloakbrowser 的 conda313 那个 python。
"""
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]   # asset_cache/login/prewarm.py → 项目根
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from asset_cache._prewarm_core import run_cli  # noqa: E402

if __name__ == '__main__':
    asyncio.run(run_cli('login'))
