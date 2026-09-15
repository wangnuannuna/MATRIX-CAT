# -*- coding: utf-8 -*-
"""
静态资源缓存(JS/CSS)+ 登录/发布无关重资源屏蔽 —— 数据面。搬自 HuiMei 引擎那套,原样能用。

痛点是同构的:各平台登录/发布页要拉 ~几十个静态 JS/CSS,现在全走 KDL 北京代理逐个下,
一个 8-13s,把代理连接池占满,二维码/接口被堵在队列后头超时——这才是登录"慢半天不出码"的真凶。
本类把这批静态资源缓存到本地磁盘:首次经代理下载后存盘,之后命中本地直接 fulfill 喂给浏览器、
完全绕过代理;顺带 abort 掉一批"登录/发布根本用不到、却把连接池吃满"的重资源(首页推广大图/
视频/打点 beacon),把带宽让给真正要紧的请求。

平台差异(命中哪些域名、屏蔽啥、缓存多久)全下沉到 config.CacheConfig,本类只读 config、不含任何
平台常量。跟 open_session 里的挂载协作:handler 注册在 page.route('**/*') 上,每个请求先到这里——
命中→从磁盘 fulfill;该屏蔽→abort;没命中/过期→continue_ 走代理下,save_response 旁路存盘;
其余(接口/图片/字体等)→fallback() 放行(Matrix_Cat 这边 open_session 没别的拦截器,fallback 自动兜底 continue)。
键=sha1(完整 URL 含 query);版本化 URL 一改版就换 key、命中即正确,不会喂到旧的。
"""
import os
import json
import hashlib
import time


def _host(url: str) -> str:
    try:
        return url.split('/', 3)[2]
    except Exception:
        return ''


def _key(url: str) -> str:
    return hashlib.sha1(url.encode('utf-8')).hexdigest()


class AssetCache:
    def __init__(self, store_dir: str, config):
        """store_dir: 本平台缓存目录(get_store_dir 给出);config: config.CacheConfig。"""
        self.dir = store_dir
        self.cfg = config
        os.makedirs(self.dir, exist_ok=True)
        self.hits = 0
        self.misses = 0
        self.saved = 0
        self.blocked = 0

    @classmethod
    def for_platform(cls, platform: str, scope: str = 'login'):
        """一行构造:AssetCache.for_platform('xhs')(默认登录页 scope='login');发布页传 scope='publish'。
        各 scope 的规则表在 asset_cache/<scope>/config.py,缓存目录在 _store/<scope>/<platform>/,两套互不干扰。
        未注册平台拿到的是空白名单配置(passthrough),行为完全正常、只是不加速。"""
        from .store import get_store_dir
        if scope == 'publish':
            from .publish.config import get_cache_config
        else:
            from .login.config import get_cache_config
        return cls(get_store_dir(platform, scope), get_cache_config(platform))

    @property
    def enabled(self) -> bool:
        """有没有任何缓存/屏蔽规则。没有(未注册平台)就别去拦截所有请求白费开销,直接不挂。"""
        c = self.cfg
        return bool(c.static_hosts or c.block_types or c.block_url_substr)

    def _paths(self, url: str):
        k = _key(url)
        return os.path.join(self.dir, k + '.bin'), os.path.join(self.dir, k + '.json')

    def _is_static_cdn(self, url: str, rtype: str) -> bool:
        return rtype in self.cfg.cache_types and any(h in _host(url) for h in self.cfg.static_hosts)

    def _is_blocked(self, url: str, rtype: str) -> bool:
        return rtype in self.cfg.block_types or any(s in url for s in self.cfg.block_url_substr)

    def _fresh(self, path: str) -> bool:
        """文件在且没过 TTL。ttl<=0 当永不过期(全版本化 URL 的场景可以关 TTL,如小红书)。"""
        try:
            if not os.path.exists(path):
                return False
            if self.cfg.ttl and self.cfg.ttl > 0:
                return (time.time() - os.path.getmtime(path)) < self.cfg.ttl
            return True
        except Exception:
            return False

    async def _fallback(self, route):
        """非缓存请求放行。open_session 没别的拦截器接 fallback 时,退回 continue_ 兜底。"""
        try:
            await route.fallback()
        except Exception:
            try:
                await route.continue_()
            except Exception:
                pass

    # 挂到 page.route('**/*', cache.handle)
    async def handle(self, route, request):
        try:
            url = request.url
            rtype = request.resource_type
            # ① 先掐掉登录/发布用不到的重资源:正是它们经代理几十秒、把连接池吃满堵住二维码
            if self._is_blocked(url, rtype):
                self.blocked += 1
                try:
                    await route.abort()
                except Exception:
                    await self._fallback(route)
                return
            # ② 静态 JS/CSS 走本地缓存
            if request.method == 'GET' and self._is_static_cdn(url, rtype):
                bin_p, meta_p = self._paths(url)
                if self._fresh(bin_p):
                    self.hits += 1
                    with open(bin_p, 'rb') as f:
                        body = f.read()
                    ct = 'application/javascript' if rtype == 'script' else 'text/css'
                    try:
                        ct = json.load(open(meta_p, 'r', encoding='utf-8')).get('ct', ct)
                    except Exception:
                        pass
                    await route.fulfill(status=200, body=body, headers={'content-type': ct})
                    return
                # 没命中/过期:走网络(经代理首次或刷新下),save_response 负责存盘/覆盖
                self.misses += 1
                try:
                    await route.continue_()
                except Exception:
                    await self._fallback(route)
                return
            # 其余放行
            await self._fallback(route)
        except Exception:
            await self._fallback(route)

    # 挂到 page.on('response', lambda r: asyncio.ensure_future(cache.save_response(r)))
    async def save_response(self, response):
        try:
            req = response.request
            if req.method != 'GET' or response.status != 200:
                return
            if not self._is_static_cdn(req.url, req.resource_type):
                return
            bin_p, meta_p = self._paths(req.url)
            if self._fresh(bin_p):          # 只在缺失/过期时写(过期就覆盖刷新)
                return
            body = await response.body()
            if not body:
                return
            with open(bin_p, 'wb') as f:
                f.write(body)
            with open(meta_p, 'w', encoding='utf-8') as f:
                json.dump({'ct': response.headers.get('content-type', ''), 'url': req.url}, f)
            self.saved += 1
        except Exception:
            pass

    def stats(self) -> dict:
        return {'hits': self.hits, 'misses': self.misses,
                'saved': self.saved, 'blocked': self.blocked}
