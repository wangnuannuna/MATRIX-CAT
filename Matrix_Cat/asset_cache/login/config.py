# -*- coding: utf-8 -*-
"""
静态资源缓存的「每平台配置」注册表 —— 登录页这一套。

加一个平台 = 在 CACHE 加一条(缓存哪些 CDN 域名 / 屏蔽哪些资源 / 缓存多久),要预热的话在 PREWARM
再加一条(打开哪个登录页 / 点哪个按钮切扫码 / 二维码就绪判据)。open_session 那边按 goto_url 域名自动
认平台,不用再改任何逻辑。域名/字节数沿用 HuiMei 2026-06-27 真机实测值(小红书/抖音/快手三家都实测过)。
"""
from dataclasses import dataclass


# ============================ 缓存数据面配置 ============================
@dataclass(frozen=True)
class CacheConfig:
    static_hosts: tuple                                          # 命中即走本地缓存的 CDN 域名(子串匹配)
    cache_types: frozenset = frozenset({'script', 'stylesheet'})  # 只缓存这些 resource_type
    block_types: frozenset = frozenset()                         # 直接 abort 的 resource_type(如 media)
    block_url_substr: tuple = ()                                 # URL 含这些子串即 abort
    ttl: int = 7 * 24 * 3600                                     # 缓存有效期秒;0=永不过期(全版本化 URL)


@dataclass(frozen=True)
class PrewarmSpec:
    url: str                                   # 预热打开的登录页
    click_selectors: tuple = ()                # 触发二维码 widget 加载要点的按钮(按序试,点中第一个可见的)
    goto_wait: str = 'domcontentloaded'        # goto 的 wait_until
    ready_selector: str = None                 # 就绪判据①:等这个选择器可见
    ready_js: str = None                       # 就绪判据②:一段返回真值的 JS,轮询 page.evaluate
    click_timeout_ms: int = 8000               # 等每个按钮可见的超时
    ready_timeout_ms: int = 30000              # 等二维码就绪的超时


CACHE = {
    # 小红书:登录页瓶颈是 xhscdn.com 上 ~32 个 script;全是版本哈希 URL → ttl=0 永不过期(改版换 URL 自动失效)。
    'xhs': CacheConfig(static_hosts=('xhscdn.com',), ttl=0),

    # 抖音:创作者中心 creator.douyin.com。JS 很散(共 ~3.9MB):主包 douyinstatic.com 2.3MB + 直登
    #   yhgfb-cn-static.com 0.79MB + npm 包 byted-static.com 0.22MB + 安全 bytetos.com 0.24MB,这四家兜住 ~90%。
    #   ★ block media:登录页另有 64MB 促销视频(lf3-static.bytednsdoc.com),经代理吃满连接池把二维码堵超时,掐掉。
    'douyin': CacheConfig(
        static_hosts=('douyinstatic.com', 'yhgfb-cn-static.com', 'byted-static.com', 'bytetos.com'),
        block_types=frozenset({'media'}),
    ),

    # 快手:登录扫码 widget 的 ~1.1MB JS 在 w2.kskwai.com,另有 yximgs.com 上的零碎静态;
    #   登录/账号 API 在 passport.kuaishou.com / cp.kuaishou.com(不匹配,天然不缓存,不会误伤签名)。
    'ks': CacheConfig(static_hosts=('kskwai.com', 'yximgs.com')),

    # B站:登录页(passport/member)的 JS/CSS 全在 *.hdslb.com(s1/s2/i0 轮换,子串兜住);无促销重资源。
    #   登录/账号 API 在 passport.bilibili.com / api.bilibili.com(不匹配,天然不缓存)。
    'bilibili': CacheConfig(static_hosts=('hdslb.com',)),
}


# 未注册平台兜底:空白名单 → enabled=False,open_session 直接不挂拦截器(等于没装缓存、行为正常、只是不加速)。
_PASSTHROUGH = CacheConfig(static_hosts=())


def get_cache_config(platform: str) -> CacheConfig:
    """未注册平台返回 _PASSTHROUGH(静默直通,不加速也不报错)。"""
    return CACHE.get(platform, _PASSTHROUGH)


# ============================ 预热(prewarm)配置 ============================
PREWARM = {
    'xhs': PrewarmSpec(
        url='https://creator.xiaohongshu.com/login',
        goto_wait='commit',
        # 登录页默认短信登录,点右上角 img.css-wemwzq 切扫码才会加载二维码那批 JS
        click_selectors=('img.css-wemwzq',),
        click_timeout_ms=20000,
        # 出现扫码文案 或 页面里落了张大的 data:image 二维码,就算二维码就绪、JS 拉全了
        ready_js=("() => /扫一扫|扫码登录|打开小红书|APP扫码/.test(document.body.innerText||'') "
                  "|| [...document.querySelectorAll('img')].some(i=>(i.src||'').startsWith('data:image') "
                  "&& i.naturalWidth>=150)"),
    ),
    'douyin': PrewarmSpec(
        url='https://creator.douyin.com/',
        goto_wait='domcontentloaded',
        # 默认就是扫码 tab,二维码在 #animate_qrcode_container 里(//img 兜住其内层 <img>)
        ready_selector='//*[@id="animate_qrcode_container"]//img',
    ),
    'ks': PrewarmSpec(
        # 直接打 passport 登录页(cp.kuaishou.com 未登录也会跳到这),点 .platform-switch-pc 切扫码
        url='https://passport.kuaishou.com/pc/account/login/',
        goto_wait='commit',
        click_selectors=('.platform-switch-pc',),
        ready_selector='img[alt="qrcode"][src^="data:image"]',
    ),
    'bilibili': PrewarmSpec(
        # passport 登录页二维码直接出;预热只求把 hdslb.com 那批 JS 拉下来,等 load 完即可
        url='https://passport.bilibili.com/login',
        goto_wait='domcontentloaded',
        ready_js='() => document.readyState === "complete"',
    ),
}


def get_prewarm_spec(platform: str) -> PrewarmSpec:
    try:
        return PREWARM[platform]
    except KeyError:
        raise KeyError(f"asset_cache 未注册平台 '{platform}' 的登录页 PrewarmSpec;已注册: {sorted(PREWARM)}")
