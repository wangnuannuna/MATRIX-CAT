# -*- coding: utf-8 -*-
"""
CloakBrowser 会话底座——所有平台共用的"真指纹"隐身浏览器。放 shared/，各平台的 login/publish 都从这起会话。

为什么用 CloakBrowser：源码级 C++ 改指纹的 Chromium(canvas/WebGL/audio/fonts/GPU/screen/WebRTC/CDP)，
指纹是二进制层面真的、不是 JS 临时改的；且每号能独立指纹。

一号一设备(矩阵隔离，对着封号根因来)：
  - 每个 account_id 一份独立持久化 profile 目录(cookie/缓存/localStorage 落盘) → 不是每次"新设备首登"；
  - 每个 account_id 一份稳定的 --fingerprint 种子 → 指纹长期固定且与别号不同；
  - 代理粘账号(默认走北京出口，见 config/settings.resolve_proxy)。

平台相关的东西(profile 目录、打开后导航到哪个 URL)由调用方传参，本模块只管"怎么起一个隐身会话"。
"""
import asyncio
import hashlib
import logging
import os
import sys
from typing import Any, Dict, Optional

# 让 `from config import settings` 找得到：shared/browser.py 往上两级 = 项目根
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import settings as S  # noqa: E402  全局配置(UA/CloakBrowser/代理)

logger = logging.getLogger(__name__)

# license key(若配了)塞进环境变量，让 cloakbrowser 自己读。
if S.CLOAK_LICENSE_KEY and not os.environ.get('CLOAKBROWSER_LICENSE_KEY'):
    os.environ['CLOAKBROWSER_LICENSE_KEY'] = S.CLOAK_LICENSE_KEY


def _seed_for(account_id: str) -> int:
    """account_id → 稳定的 32bit 指纹种子(同号每次一样，不同号不一样)。"""
    if not account_id:
        return 0x12345678
    return int.from_bytes(hashlib.sha256(account_id.encode('utf-8')).digest()[:4], 'big')


def _fingerprint_args(account_id: str) -> list:
    seed = _seed_for(account_id)
    return [f'--fingerprint={seed}', f'--fingerprint-platform={S.CLOAK_FINGERPRINT_PLATFORM}']


def _direct_args() -> list:
    """强制直连、绕开系统 VPN/HTTP_PROXY(没配代理时用)。"""
    return ['--proxy-server=direct://', '--proxy-bypass-list=*']


def _parse_proxy(proxy_url: Optional[str]):
    """'http://user:pass@host:port' → cloakbrowser.ProxySettings；无则 None。"""
    if not proxy_url:
        return None
    import cloakbrowser
    from urllib.parse import urlparse, unquote
    u = urlparse(proxy_url)
    server = f'{u.scheme or "http"}://{u.hostname}{":" + str(u.port) if u.port else ""}'
    return cloakbrowser.ProxySettings(
        server=server,
        username=unquote(u.username) if u.username else None,
        password=unquote(u.password) if u.password else None,
    )


# goto_url 域名 → asset_cache 里的平台键(子串匹配)。用来自动认平台,调用方不用另传。
_HOST_PLATFORM = (
    ('xiaohongshu.com', 'xhs'),
    ('douyin.com', 'douyin'),
    ('kuaishou.com', 'ks'),
    ('bilibili.com', 'bilibili'),
)


def _infer_platform(url: str) -> Optional[str]:
    host = ''
    try:
        host = url.split('/', 3)[2]
    except Exception:
        pass
    for frag, plat in _HOST_PLATFORM:
        if frag in host:
            return plat
    return None


class CloakSession:
    """一个已开好的 CloakBrowser 上下文 + 一个页面。用完 await aclose()。"""

    def __init__(self, ctx: Any, page: Any, *, account_id: str, proxy: Optional[str],
                 asset_cache: Any = None):
        self.ctx = ctx
        self.page = page
        self.account_id = account_id
        self.proxy = proxy
        self.asset_cache = asset_cache   # AssetCache 或 None(未注册平台);.stats() 看命中/屏蔽计数

    async def wait_for(self, js_expr: str, timeout_sec: float = 30.0) -> bool:
        """等页面里某个条件成立(如平台的签名函数挂载好)。js_expr 是个返回真值的表达式。

        例：await sess.wait_for("typeof window._webmsxyw === 'function'")
        """
        deadline = asyncio.get_event_loop().time() + timeout_sec
        last = 'unknown'
        while asyncio.get_event_loop().time() < deadline:
            try:
                if await self.page.evaluate(f"() => ({js_expr})"):
                    return True
                last = 'falsy'
            except Exception as e:
                last = str(e)[:80]
            await asyncio.sleep(0.3)
        raise RuntimeError(f'等待条件超时 [{js_expr}]: {last}')

    async def storage_state(self) -> Dict[str, Any]:
        return await self.ctx.storage_state()

    async def aclose(self) -> None:
        try:
            await self.ctx.close()
        except Exception as e:
            logger.debug(f'关闭 CloakBrowser 上下文异常(忽略): {e}')


async def open_session(account_id: str, *, profiles_dir: str, goto_url: str,
                       scene: str = 'publish',
                       storage_state: Optional[Dict[str, Any]] = None,
                       proxy: Optional[str] = None,
                       headless: Optional[bool] = None,
                       platform: Optional[str] = None) -> CloakSession:
    """开一个 CloakBrowser 会话。

    profiles_dir: 该平台放 per-account profile 的根目录(一号一子目录)。
    goto_url:     开好后导航到哪(平台的登录页 / 首页)。
    scene:        'login' | 'publish' | 'sign'。决定默认有头/无头 + 是否载入账号态 + 是否拟人。
    storage_state: 发布/签名场景传账号登录态(cookies + localStorage)，会载进上下文(登录场景不载)。
    proxy:        代理 URL；None 则按全局模式取(默认北京出口，见 config.settings.resolve_proxy)。
    platform:     asset_cache 平台键(xhs/douyin/ks);None 则按 goto_url 域名自动认。未注册平台=不挂缓存。
    """
    import cloakbrowser

    if headless is None:
        headless = S.headless_for(scene)
    if proxy is None:
        # 默认挑北京出口代理(去 KDL 取+探城市，是阻塞网络活，丢线程里别卡事件循环)。
        # 拿不到合格代理会 raise ProxyUnavailable，往上抛(调用层转成干净报错)。
        proxy = await asyncio.to_thread(S.resolve_proxy)

    udd = os.path.join(profiles_dir, account_id or 'default')
    os.makedirs(udd, exist_ok=True)

    args = _fingerprint_args(account_id)
    ps = _parse_proxy(proxy)
    if ps is None:
        args += _direct_args()

    ctx = await cloakbrowser.launch_persistent_context_async(
        udd,
        headless=headless,
        proxy=ps,
        args=args,
        stealth_args=S.CLOAK_STEALTH,
        locale=S.LOCALE,
        timezone=S.TIMEZONE,
        geoip=False,               # 时区/语言已显式钉死；geoip 那次探 IP 是阻塞外呼且认证代理下拿不到，无收益
        humanize=(scene == 'login' and S.CLOAK_HUMANIZE_LOGIN),
    )

    if storage_state and scene != 'login':
        await _load_account_state(ctx, storage_state)

    page = ctx.pages[0] if getattr(ctx, 'pages', None) else await ctx.new_page()

    # 静态资源本地缓存(学 HuiMei):登录/发布页那批经代理死慢的 JS/CSS 命中本地磁盘直接喂、绕过代理,
    # 顺带 abort 掉登录用不到的促销大图/视频,把代理连接池让给二维码/接口。必须在 goto 前挂好。
    # 平台按 goto_url 域名自动认;没注册的平台 enabled=False,直接不挂(不加速也不报错)。
    asset_cache = None
    try:
        from asset_cache import AssetCache
        plat = platform or _infer_platform(goto_url)
        if plat:
            scope = 'login' if scene == 'login' else 'publish'
            ac = AssetCache.for_platform(plat, scope=scope)
            if ac.enabled:
                await page.route('**/*', ac.handle)
                page.on('response', lambda r: asyncio.ensure_future(ac.save_response(r)))
                asset_cache = ac
                logger.info(f'静态资源缓存已挂 platform={plat} scope={scope}')
    except Exception as e:
        logger.debug(f'静态资源缓存未挂载(忽略,不影响功能): {e}')

    try:
        await page.goto(goto_url, wait_until='domcontentloaded', timeout=45000)
    except Exception as e:
        logger.warning(f'导航到 {goto_url} 异常(继续): {e}')

    if asset_cache is not None:
        logger.info(f'CloakBrowser 会话就绪 account={account_id} scene={scene} '
                    f'headless={headless} proxy={"有" if ps else "直连"} 缓存={asset_cache.stats()}')
    else:
        logger.info(f'CloakBrowser 会话就绪 account={account_id} scene={scene} '
                    f'headless={headless} proxy={"有" if ps else "直连"}')
    return CloakSession(ctx, page, account_id=account_id, proxy=proxy, asset_cache=asset_cache)


async def _load_account_state(ctx: Any, storage_state: Dict[str, Any]) -> None:
    """把账号登录态(cookies + localStorage)载入上下文。cookies→add_cookies；
    localStorage→add_init_script 在对应 origin 的 document-start 注入(持久跨导航)。"""
    import json as _json

    norm = []
    for c in storage_state.get('cookies') or []:
        if not c.get('name') or not c.get('domain'):
            continue
        cc = {'name': c['name'], 'value': c.get('value', ''),
              'domain': c['domain'], 'path': c.get('path', '/')}
        exp = c.get('expires')
        if isinstance(exp, (int, float)) and exp > 0:
            cc['expires'] = exp
        if 'httpOnly' in c:
            cc['httpOnly'] = bool(c['httpOnly'])
        if 'secure' in c:
            cc['secure'] = bool(c['secure'])
        if c.get('sameSite') in ('Strict', 'Lax', 'None'):
            cc['sameSite'] = c['sameSite']
        norm.append(cc)
    if norm:
        try:
            await ctx.add_cookies(norm)
        except Exception as e:
            logger.warning(f'add_cookies 失败: {e}')

    for o in storage_state.get('origins') or []:
        origin = o.get('origin')
        items = o.get('localStorage') or []
        if not origin or not items:
            continue
        kv = {it['name']: it.get('value', '') for it in items if it.get('name') is not None}
        script = (f"(()=>{{try{{if(location.origin==={_json.dumps(origin)}){{"
                  f"const d={_json.dumps(kv, ensure_ascii=False)};"
                  f"for(const k in d){{try{{localStorage.setItem(k,d[k]);}}catch(e){{}}}}}}}}catch(e){{}}}})()")
        try:
            await ctx.add_init_script(script)
        except Exception as e:
            logger.warning(f'localStorage 注入失败: {e}')
