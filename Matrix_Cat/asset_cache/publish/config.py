# -*- coding: utf-8 -*-
"""
静态资源缓存的「每平台配置」注册表 —— 发布页这一套(跟登录页 login/config.py 同构、各自独立一份)。

为什么发布页也要缓存:各平台「创作者中心/发布页」是跟登录页同一个 SPA 的不同路由、走同一批 CDN,
但发布页的 JS/CSS 往往更重(编辑器/上传组件/草稿/话题联想…)。经代理逐个下会让发布页开得很慢。
static_hosts 直接沿用登录页实测值(同源同 CDN);缓存键是完整 URL 的 sha1,发布页与登录页各自的 chunk
各成键、互不覆盖。

跟登录页配置唯一的实质差异:**发布页一律不 block media**——发布视频后常有「预览播放器」,其视频流正是
media 类型,在这儿屏蔽会把预览搞没。所以发布作用域保守点只缓存 script/stylesheet,不碰 media。
"""
# 复用登录页那套 dataclass 定义,两套只是数据不同、结构一致
from ..login.config import CacheConfig, PrewarmSpec


CACHE = {
    # 小红书:发布页 creator.xiaohongshu.com/publish/publish,静态在 xhscdn.com(全版本哈希→永不过期)。
    'xhs': CacheConfig(static_hosts=('xhscdn.com',), ttl=0),

    # 抖音:发布页 creator.douyin.com/creator-micro/content/upload,JS 散在这四家(同登录页实测)。不 block media。
    'douyin': CacheConfig(
        static_hosts=('douyinstatic.com', 'yhgfb-cn-static.com', 'byted-static.com', 'bytetos.com')),

    # 快手:发布页 cp.kuaishou.com/article/publish/video,静态在 kskwai / yximgs。
    'ks': CacheConfig(static_hosts=('kskwai.com', 'yximgs.com')),

    # B站:发布静态在 *.hdslb.com。注:B站发布走 curl 直连、不开浏览器,此条目前不会命中,仅为对齐登记。
    'bilibili': CacheConfig(static_hosts=('hdslb.com',)),
}


_PASSTHROUGH = CacheConfig(static_hosts=())


def get_cache_config(platform: str) -> CacheConfig:
    """未注册平台返回 _PASSTHROUGH(静默直通,不加速也不报错)。"""
    return CACHE.get(platform, _PASSTHROUGH)


# ============================ 预热(prewarm)配置(发布页)============================
# ⚠ 发布页要「登录态」才打得开(否则跳登录页,只能抓到登录页 JS)。所以完整预热要用已登录的持久化 profile,
#   见 publish/prewarm.py 的 --headful 用法。多数情况不用手动预热——open_session 已挂好,第一次真机发布就自动填上。
PREWARM = {
    'xhs': PrewarmSpec(url='https://creator.xiaohongshu.com/publish/publish', goto_wait='commit'),
    'douyin': PrewarmSpec(url='https://creator.douyin.com/creator-micro/content/upload', goto_wait='domcontentloaded'),
    'ks': PrewarmSpec(url='https://cp.kuaishou.com/article/publish/video', goto_wait='commit'),
    'bilibili': PrewarmSpec(url='https://member.bilibili.com/platform/upload/video/frame', goto_wait='domcontentloaded'),
}


def get_prewarm_spec(platform: str) -> PrewarmSpec:
    try:
        return PREWARM[platform]
    except KeyError:
        raise KeyError(f"asset_cache 未注册平台 '{platform}' 的发布页 PrewarmSpec;已注册: {sorted(PREWARM)}")
