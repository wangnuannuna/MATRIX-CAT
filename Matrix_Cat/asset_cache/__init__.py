# -*- coding: utf-8 -*-
"""
通用静态资源缓存包 —— 各平台「登录页」「发布页」的 JS/CSS 本地缓存 + 无关重资源屏蔽,
一份引擎按作用域(scope)分两套配置和缓存目录,互不污染。搬自 HuiMei 引擎,给 Matrix_Cat 全平台用。

    asset_cache/
    ├─ cache.py            引擎(读+拦+写),scope 无关
    ├─ store.py            _store/<scope>/<platform>/
    ├─ _prewarm_core.py    预热核心(CloakBrowser 直连填缓存)
    ├─ login/   config.py + prewarm.py    ← 登录页这一套
    ├─ publish/ config.py + prewarm.py    ← 发布页这一套
    └─ _store/  login/ | publish/

接入(已在 shared/browser.py 的 open_session 里统一挂好,平台按 goto_url 域名自动推断,三平台全延用):
    from asset_cache import AssetCache
    ac = AssetCache.for_platform('xhs', scope='login')   # scene=login→'login',publish/sign→'publish'
    if ac.enabled:
        await page.route('**/*', ac.handle)
        page.on('response', lambda r: asyncio.ensure_future(ac.save_response(r)))

预热(直连填缓存,绕系统 VPN、绕 KDL 代理——静态资源是公共版本化的,不需要走代理):
    python -m asset_cache.login.prewarm xhs          # 登录页 单平台
    python -m asset_cache.login.prewarm all          # 登录页 全部已注册
    python -m asset_cache.publish.prewarm xhs         # 发布页(需登录态,见 publish 注释)

顶层默认导出「登录页」作用域的配置,历史/简单调用不受目录拆分影响;发布页同名物在 asset_cache.publish.config。
"""
from .cache import AssetCache
from .store import get_store_dir
from .login.config import (CacheConfig, get_cache_config,
                           PrewarmSpec, get_prewarm_spec, CACHE, PREWARM)

__all__ = ['AssetCache', 'CacheConfig', 'get_cache_config',
           'PrewarmSpec', 'get_prewarm_spec', 'get_store_dir', 'CACHE', 'PREWARM']
