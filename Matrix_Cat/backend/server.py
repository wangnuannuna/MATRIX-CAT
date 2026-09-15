# -*- coding: utf-8 -*-
"""
crawler_spider 总控测试台（本地网页版）。

一个 aiohttp 小服务，把各平台的「登录 + 发布」接到一个网页上：选号 → 填标题/正文/图 → 发布。
这就是"总程序"的测试入口——各平台的适配器登记在 _ADAPTERS 里，网页统一调度；
已接:小红书(XhsAdapter)、快手/哔哩哔哩(_ProtoAdapter)。加新平台照 _ADAPTERS 加一条即可。

跑法：
    python webconsole/server.py          # 默认 http://127.0.0.1:8799
    浏览器打开上面的地址就能操作。

只监听 127.0.0.1(本机),不对外；发布/登录用的还是各平台自己那套(CloakBrowser + 协议直发)。
"""
import asyncio
import logging
import os
import re
import sys
import time
import webbrowser

from aiohttp import web

HERE = os.path.dirname(os.path.abspath(__file__))          # backend/
ROOT = os.path.dirname(HERE)                                 # 项目根 Matrix_Cat/
# 各平台采集包目录都挂上(包名 xhs/kuaishou/bilibili 各自独立,不冲突)
XHS_DIR = os.path.join(ROOT, 'collectors', 'xhs_publisher')
KS_DIR = os.path.join(ROOT, 'collectors', 'kuaishou_publisher')
BILI_DIR = os.path.join(ROOT, 'collectors', 'bilibili_publisher')
for _p in (ROOT, XHS_DIR, KS_DIR, BILI_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared.io_utf8 import setup_utf8, setup_logging  # noqa: E402

setup_utf8()
setup_logging()
logger = logging.getLogger('webconsole')

UPLOAD_DIR = os.path.join(ROOT, 'storage', 'data', 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ════════════════════════════════════════════════════════════════════════════
# 平台适配器：把每个平台的 登录/发布/账号 统一成一套接口，网页只跟这套打交道
# ════════════════════════════════════════════════════════════════════════════

class _ProtoAdapter:
    """各平台统一适配器:store=protocol.get_store(),发布分 publish_video/publish_image,返回 note_id/work_id。
    xhs/kuaishou/bilibili 模块布局一致,只用 pkg 区分;新平台 照 collectors/<pkg>_publisher 约定即可复用。"""
    name = ''
    label = ''
    pkg = ''
    vis_error = None      # note 里的可见性异常类名(会并进 publish_error,给干净报错)

    def _mods(self):
        import importlib
        return (importlib.import_module(f'{self.pkg}.config'),
                importlib.import_module(f'{self.pkg}.login'),
                importlib.import_module(f'{self.pkg}.note'),
                importlib.import_module(f'{self.pkg}.protocol'),
                importlib.import_module(f'{self.pkg}.publish_video').publish_video,
                importlib.import_module(f'{self.pkg}.publish_image').publish_image)

    def store(self):
        _, _, _, protocol, _, _ = self._mods()
        return protocol.get_store()

    def list_accounts(self):
        store = self.store()
        now = int(time.time())
        out = []
        for a in store.list_accounts():
            cd = a.get('cooldown_until', 0) or 0
            out.append({
                'account_id': a.get('account_id'),
                'nickname': a.get('nickname'),
                'user_id': a.get('user_id'),
                'cooling': max(0, cd - now),
                'logged_in': bool((a.get('storage_state') or {}).get('cookies')),
            })
        return out

    async def login(self, account_id):
        _, login_mod, _, _, _, _ = self._mods()
        r = await login_mod.login(account_id)
        return {'ok': r.success, 'nickname': r.nickname, 'error': r.error,
                'already': r.already_logged_in}

    async def publish(self, account_id, kind, files, meta):
        _, _, _, _, publish_video, publish_image = self._mods()
        common = dict(title=meta.get('title', ''), desc=meta.get('desc', ''),
                      topics=meta.get('topics') or None,
                      visibility=meta.get('visibility', 'public'),
                      is_original=meta.get('is_original', False))
        if kind == 'video':
            if not files.get('video') or not files.get('cover'):
                raise ValueError('发视频要同时上传 视频 和 封面')
            r = await publish_video(account_id, files['video'], files['cover'], **common)
        else:
            if not files.get('images'):
                raise ValueError('发图文至少上传一张图')
            r = await publish_image(account_id, files['images'], **common)
        # 各平台作品 id 字段名不同:小红书 note_id、快手/B站 work_id。
        return {'ok': True, 'note_id': r.get('note_id') or r.get('work_id'), 'url': r.get('url')}

    def publish_error(self):
        _, _, note, protocol, _, _ = self._mods()
        errs = [protocol.PublishError]
        if self.vis_error and hasattr(note, self.vis_error):
            errs.append(getattr(note, self.vis_error))   # 可见性异常也当"干净报错"接住
        return tuple(errs)


class XhsAdapter(_ProtoAdapter):
    name = 'xhs'
    label = '小红书'
    pkg = 'xhs'
    vis_error = 'VisibilityCalibrationError'


class KuaishouAdapter(_ProtoAdapter):
    name = 'kuaishou'
    label = '快手'
    pkg = 'kuaishou'
    vis_error = 'VisibilityCalibrationError'


class BilibiliAdapter(_ProtoAdapter):
    name = 'bilibili'
    label = '哔哩哔哩'
    pkg = 'bilibili'
    vis_error = 'VisibilityUnsupportedError'


_ADAPTERS = {a.name: a for a in [XhsAdapter(), KuaishouAdapter(), BilibiliAdapter()]}


def get_adapter(platform: str):
    ad = _ADAPTERS.get(platform)
    if not ad:
        raise web.HTTPBadRequest(text=f'未知平台: {platform}')
    return ad


# ════════════════════════════════════════════════════════════════════════════
# HTTP 路由
# ════════════════════════════════════════════════════════════════════════════

async def index(_request):
    return web.FileResponse(os.path.join(ROOT, 'frontend', 'index.html'))


async def api_platforms(_request):
    return web.json_response([{'name': a.name, 'label': a.label} for a in _ADAPTERS.values()])


async def api_accounts(request):
    ad = get_adapter(request.query.get('platform', 'xhs'))
    try:
        return web.json_response({'ok': True, 'accounts': ad.list_accounts()})
    except Exception as e:
        logger.exception('列账号失败')
        return web.json_response({'ok': False, 'error': str(e)})


async def api_login(request):
    body = await request.json()
    ad = get_adapter(body.get('platform', 'xhs'))
    account_id = (body.get('account_id') or '').strip()
    if not account_id:
        return web.json_response({'ok': False, 'error': '要填账号名(account_id)'})
    try:
        res = await ad.login(account_id)
        return web.json_response(res)
    except Exception as e:
        logger.exception('登录失败')
        return web.json_response({'ok': False, 'error': str(e)})


_SAFE = re.compile(r'[^A-Za-z0-9_.\-一-鿿]')


def _save_upload(field_name, filename, data) -> str:
    sub = os.path.join(UPLOAD_DIR, str(int(time.time() * 1000)) + '_' + field_name)
    os.makedirs(sub, exist_ok=True)
    safe = _SAFE.sub('_', filename or 'file')
    path = os.path.join(sub, safe)
    with open(path, 'wb') as f:
        f.write(data)
    return path


async def api_publish(request):
    """multipart：字段(platform/account_id/kind/title/desc/topics/visibility) + 文件(images/video/cover)。"""
    reader = await request.multipart()
    fields = {}
    files = {'images': [], 'video': None, 'cover': None}
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.filename:
            data = await part.read(decode=False)
            if not data:
                continue
            path = _save_upload(part.name, part.filename, data)
            if part.name == 'images':
                files['images'].append(path)
            elif part.name in ('video', 'cover'):
                files[part.name] = path
        else:
            fields[part.name] = await part.text()

    platform = fields.get('platform', 'xhs')
    ad = get_adapter(platform)
    account_id = (fields.get('account_id') or '').strip()
    if not account_id:
        return web.json_response({'ok': False, 'error': '先选一个账号'})
    kind = fields.get('kind', 'image')
    topics = [t for t in re.split(r'[,\s，]+', fields.get('topics', '') or '') if t]
    meta = {'title': fields.get('title', ''), 'desc': fields.get('desc', ''),
            'topics': topics, 'visibility': fields.get('visibility', 'self_only'),
            'is_original': fields.get('is_original') in ('1', 'true', 'on')}
    logger.info(f'发布请求 platform={platform} account={account_id} kind={kind} '
                f'imgs={len(files["images"])} vis={meta["visibility"]}')
    try:
        res = await ad.publish(account_id, kind, files, meta)
        return web.json_response(res)
    except ad.publish_error() as e:  # 平台自己的 PublishError → 干净报错
        logger.warning(f'发布失败: {e}')
        return web.json_response({'ok': False, 'error': str(e)})
    except Exception as e:
        logger.exception('发布异常')
        return web.json_response({'ok': False, 'error': f'{type(e).__name__}: {e}'})


def build_app() -> web.Application:
    app = web.Application(client_max_size=512 * 1024 * 1024)  # 允许大文件(视频)上传
    app.router.add_get('/', index)
    app.router.add_get('/api/platforms', api_platforms)
    app.router.add_get('/api/accounts', api_accounts)
    app.router.add_post('/api/login', api_login)
    app.router.add_post('/api/publish', api_publish)
    return app


def main():
    port = int(os.environ.get('WEBCONSOLE_PORT', '8799'))
    url = f'http://127.0.0.1:{port}'
    logger.info(f'总控测试台启动: {url}  (Ctrl+C 退出)')
    try:
        webbrowser.open(url)
    except Exception:
        pass
    web.run_app(build_app(), host='127.0.0.1', port=port, print=None)


if __name__ == '__main__':
    main()
