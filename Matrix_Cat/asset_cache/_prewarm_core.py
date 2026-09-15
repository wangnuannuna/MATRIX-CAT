# -*- coding: utf-8 -*-
"""
预热核心:把某平台登录页/发布页用到的静态 JS/CSS 提前【直连】下到本地缓存,之后真机登录/发布命中本地、
绕过 KDL 代理,二维码从「经代理几十秒超时」变秒出。不跑也行——首次登录会自己填,只是那一次慢。

两个刻意的设计:
  ① 用 CloakBrowser(不是随便一个 Chrome)预热——真机登录也是 CloakBrowser(chromium-146),同内核同 UA
     拉到的静态 chunk URL 才和真登录一模一样,缓存键(sha1(URL))才对得上、真登录才命中。
  ② 直连、不走代理——缓存的是公共版本化静态资源(不绑账号),直连即可拉;还要 --proxy-server=direct://
     强行绕开本机系统 VPN(小红书/抖音/快手是境内站,VPN 绕境外反而慢/断)。代理是留给登录/账号流量的。
"""
import asyncio
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]   # asset_cache/_prewarm_core.py → 上一级 = 项目根
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import settings as S           # noqa: E402
from asset_cache import AssetCache, get_store_dir  # noqa: E402


def _spec_and_cache(platform: str, scope: str):
    if scope == 'publish':
        from asset_cache.publish.config import get_prewarm_spec
    else:
        from asset_cache.login.config import get_prewarm_spec
    return get_prewarm_spec(platform), AssetCache.for_platform(platform, scope=scope)


async def prewarm_platform(platform: str, scope: str = 'login', headful: bool = False):
    spec, cache = _spec_and_cache(platform, scope)
    store = get_store_dir(platform, scope)
    print(f'[prewarm:{scope}:{platform}] 缓存目录(与真机共用): {store}')

    import cloakbrowser
    if S.CLOAK_LICENSE_KEY and not os.environ.get('CLOAKBROWSER_LICENSE_KEY'):
        os.environ['CLOAKBROWSER_LICENSE_KEY'] = S.CLOAK_LICENSE_KEY

    # 直连:--proxy-server=direct:// 强绕系统 VPN;proxy=None 不挂 KDL。指纹用固定种子(缓存跟账号无关)。
    args = ['--fingerprint=20260719', f'--fingerprint-platform={S.CLOAK_FINGERPRINT_PLATFORM}',
            '--proxy-server=direct://', '--proxy-bypass-list=*']
    prof = os.path.join(store, '_profile_prewarm')   # 持久化:发布页 --headful 扫码登录一次后,后续即登录态
    os.makedirs(prof, exist_ok=True)

    t0 = time.monotonic()
    ctx = await cloakbrowser.launch_persistent_context_async(
        prof, headless=not headful, proxy=None, args=args,
        stealth_args=S.CLOAK_STEALTH, locale=S.LOCALE, timezone=S.TIMEZONE,
        geoip=False, humanize=False)
    await ctx.route('**/*', cache.handle)
    page = ctx.pages[0] if getattr(ctx, 'pages', None) else await ctx.new_page()
    page.on('response', lambda r: asyncio.ensure_future(cache.save_response(r)))
    try:
        await page.goto(spec.url, wait_until=spec.goto_wait, timeout=60000)
        # 点登录/切扫码,触发二维码 widget 的 JS 加载(瓶颈正是这批)
        for sel in spec.click_selectors:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state='visible', timeout=spec.click_timeout_ms)
                await btn.click()
                break
            except Exception:
                continue
        # 等就绪:优先选择器,其次就绪 JS 轮询;等不到也没事,下面静置照样能抓到已发起的请求
        if spec.ready_selector:
            try:
                await page.locator(spec.ready_selector).first.wait_for(
                    state='visible', timeout=spec.ready_timeout_ms)
            except Exception:
                pass
        elif spec.ready_js:
            for _ in range(int(spec.ready_timeout_ms / 100)):
                try:
                    if await page.evaluate(spec.ready_js):
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.1)
        if headful:
            print(f'[prewarm:{scope}:{platform}] 已用 --headful 打开,请在窗口里完成登录,登录后停留几秒'
                  f'让页面 JS 加载完,然后关窗/Ctrl+C。')
            await asyncio.sleep(180)
        else:
            await asyncio.sleep(3)   # 静置让剩余 response 存完
        n = len([f for f in os.listdir(store) if f.endswith('.bin')]) if os.path.isdir(store) else 0
        landed = getattr(page, 'url', '')
        print(f'[prewarm:{scope}:{platform}] 完成,用时 {time.monotonic()-t0:.1f}s,'
              f'缓存 {cache.stats()},已存静态文件 {n} 个;落地 URL: {landed}')
        if scope == 'publish' and any(k in landed for k in ('login', 'passport', 'signin')):
            print(f'[prewarm:publish:{platform}] ⚠ 落在登录页(未登录态),只抓到公共 JS。要抓全发布页 JS 请加 '
                  f'--headful 先登录: python -m asset_cache.publish.prewarm {platform} --headful')
    finally:
        try:
            await ctx.close()
        except Exception:
            pass
        await asyncio.sleep(0.2)


async def run_cli(scope: str):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    if scope == 'publish':
        from asset_cache.publish.config import PREWARM
    else:
        from asset_cache.login.config import PREWARM
    argv = sys.argv[1:]
    headful = '--headful' in argv
    targets = [a for a in argv if not a.startswith('--')]
    if not targets:
        print(f'用法: python -m asset_cache.{scope}.prewarm <平台名|all> [--headful];已注册: {sorted(PREWARM)}')
        return
    if targets == ['all']:
        targets = sorted(PREWARM)
    for platform in targets:
        if platform not in PREWARM:
            print(f'[prewarm:{scope}] 跳过未注册平台 {platform};已注册: {sorted(PREWARM)}')
            continue
        await prewarm_platform(platform, scope=scope, headful=headful)
