# -*- coding: utf-8 -*-
"""
小红书登录 + 发布 命令行入口。

  # 登录(弹窗扫码)
  python run.py login myacc
  python run.py login myacc --proxy http://user:pass@host:port

  # 发图文
  python run.py publish myacc --images a.jpg b.jpg --title 标题 --desc 正文 --topics 美食 探店

  # 发视频
  python run.py publish myacc --video v.mp4 --cover c.jpg --title 标题 --desc 正文

  # 账号列表 / 校验登录态
  python run.py accounts
  python run.py refresh myacc

默认可见性 public；测试期可用 --visibility self_only 只发给自己看。
"""
import argparse
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Windows 控制台默认 GBK，打印 ✅/中文会崩 UnicodeEncodeError；强制转 UTF-8。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8')
    except Exception:
        pass

from xhs import config, login as login_mod, note, protocol  # noqa: E402
from xhs.publish_image import publish_image  # noqa: E402
from xhs.publish_video import publish_video  # noqa: E402
from shared import browser  # noqa: E402


def _setup_log(level='INFO'):
    logging.basicConfig(level=level,
                        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')


def _dump_json(path, obj):
    """原子写 JSON,给后端 worker 子进程回传(worker 不会读到半截)。"""
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _dump_login_result(path, r):
    """把 LoginResult 原子写成 JSON(含 storage_state)。"""
    _dump_json(path, {'success': bool(r.success), 'account_id': r.account_id,
                      'nickname': r.nickname, 'user_id': r.user_id, 'avatar': r.avatar,
                      'error': r.error, 'elapsed_sec': r.elapsed_sec,
                      'already_logged_in': r.already_logged_in,
                      'storage_state': r.storage_state})


async def _cmd_login(args):
    emit = getattr(args, 'emit_state', None)
    proxy = args.proxy or os.getenv('XHS_PROXY') or None   # 代理优先走 env,免得口令进 argv
    try:
        r = await login_mod.login(args.account, proxy=proxy,
                                  headless=_headless(args), save_qr=args.save_qr)
    except Exception as e:          # open_session 在 login() 的 try 外抛(取代理失败等)也要回传
        if emit:
            _dump_login_result(emit, login_mod.LoginResult(
                False, args.account, error=f'{type(e).__name__}: {e}'))
        raise
    if emit:
        _dump_login_result(emit, r)
    if r.success:
        tag = '(复用已有登录态)' if r.already_logged_in else ''
        print(f'✅ 登录成功 {r.account_id}  {r.nickname or ""}  {r.elapsed_sec:.1f}s {tag}')
        return 0
    print(f'❌ 登录失败 {r.account_id}: {r.error}')
    return 1


async def _cmd_publish(args):
    emit = getattr(args, 'emit_result', None)
    proxy = args.proxy or os.getenv('XHS_PROXY') or None    # 代理优先走 env,免得口令进 argv
    # 后端把 DB 登录态(单一事实源)通过 --storage-state 灌进来:发布前 upsert 进采集层账号库,
    # 免得依赖本地 accounts.json 与 DB 同步(修"账号 xxx 不存在,请先 login"那个坑)。save_login 落盘,
    # 下面 publish_image 自己 get_store() 从同一文件就能读到。
    ss_path = getattr(args, 'storage_state', None)
    if ss_path:
        try:
            with open(ss_path, encoding='utf-8') as f:
                ss = json.load(f)
            protocol.get_store().save_login(args.account, ss,
                                            nickname=getattr(args, 'acct_nickname', None),
                                            proxy=getattr(args, 'acct_proxy', None) or proxy)
        except Exception as e:
            if emit:
                _dump_json(emit, {'ok': False, 'error': f'载入登录态失败: {type(e).__name__}: {e}'})
            print(f'❌ 载入登录态失败: {e}'); return 1
    common = dict(title=args.title, desc=args.desc, topics=args.topics,
                  visibility=args.visibility, is_original=args.original,
                  proxy=proxy, headless=_headless(args))
    try:
        if args.video:
            r = await publish_video(args.account, args.video, args.cover, **common)
        elif args.images:
            r = await publish_image(args.account, args.images, **common)
        else:
            if emit:
                _dump_json(emit, {'ok': False, 'error': '发图文需 --images，发视频需 --video'})
            print('❌ 发图文需 --images，发视频需 --video'); return 1
    except Exception as e:
        if emit:
            _dump_json(emit, {'ok': False, 'error': f'{type(e).__name__}: {e}'})
        if isinstance(e, (protocol.PublishError, note.VisibilityCalibrationError)):
            print(f'❌ 发布失败: {e}'); return 1
        raise
    if emit:
        _dump_json(emit, {'ok': True, 'note_id': r.get('note_id'), 'url': r.get('url')})
    print(f'✅ 发布成功  note_id={r["note_id"]}\n   {r["url"]}')
    return 0


async def _cmd_refresh(args):
    """开一个会话，用账号态调 user/info，确认登录态还活着。"""
    store = protocol.get_store()
    acc = store.get(args.account)
    if not acc:
        print(f'❌ 账号 {args.account} 不存在'); return 1
    sess = await browser.open_session(
        args.account, profiles_dir=config.PROFILES_DIR, goto_url=config.HOME_URL,
        scene='sign', storage_state=acc.get('storage_state') or {},
        proxy=(args.proxy or acc.get('last_proxy')), headless=True)
    try:
        await sess.wait_for("typeof window._webmsxyw === 'function'")
        ex = protocol.SignedApiExecutor(sess.page)
        r = await ex.call('GET', config.CREATOR_HOST, config.PATH_USER_INFO)
        d = (r.json or {}).get('data') or {}
        ok = r.ok and bool(d.get('userName') or d.get('user_id') or d.get('userId'))
        print(f'{"✅" if ok else "❌"} {args.account}  status={r.status}  nickname={d.get("userName")}')
        return 0 if ok else 1
    finally:
        await sess.aclose()


def _cmd_accounts(_args):
    store = protocol.get_store()
    st = store.stats()
    print(f'账号总数 {st["total"]}  已登录 {st["logged_in"]}  冷却中 {st["cooling"]}')
    print(f'文件: {st["file"]}')
    import time as _t
    for a in store.list_accounts():
        cd = a.get('cooldown_until', 0) or 0
        cool = f' 冷却剩 {int(cd - _t.time())}s' if cd > _t.time() else ''
        uid = a.get('user_id') or a.get('xhs_user_id') or '-'
        print(f'  - {a.get("account_id"):<16} {a.get("nickname") or "":<12} uid={uid}{cool}')
    return 0


def _headless(args):
    if getattr(args, 'headless', False):
        return True
    if getattr(args, 'headed', False):
        return False
    return None  # 交给场景默认(登录有头/发布无头)


def main():
    p = argparse.ArgumentParser(description='小红书登录+发布(CloakBrowser 底座)')
    sub = p.add_subparsers(dest='cmd', required=True)

    pl = sub.add_parser('login', help='扫码登录一个账号')
    pl.add_argument('account')
    pl.add_argument('--proxy', default='')
    pl.add_argument('--save-qr', action='store_true', help='把二维码截图存文件(无头/远端扫码用)')
    pl.add_argument('--headless', action='store_true')
    pl.add_argument('--headed', action='store_true')
    pl.add_argument('--emit-state', dest='emit_state', default=None,
                    help='把登录结果(含 storage_state)原子写到此 JSON,给后端 worker 回传')

    pp = sub.add_parser('publish', help='发布图文或视频')
    pp.add_argument('account')
    pp.add_argument('--images', nargs='+', help='图文：一张或多张图片路径')
    pp.add_argument('--video', help='视频文件路径')
    pp.add_argument('--cover', help='视频封面图路径(发视频必填)')
    pp.add_argument('--title', default='')
    pp.add_argument('--desc', default='')
    pp.add_argument('--topics', nargs='*', default=None, help='话题(不带#)')
    pp.add_argument('--visibility', default='public',
                    choices=['public', 'self_only', 'mutual_friends'])
    pp.add_argument('--original', action='store_true', help='声明原创')
    pp.add_argument('--proxy', default='')
    pp.add_argument('--headless', action='store_true')
    pp.add_argument('--headed', action='store_true')
    pp.add_argument('--emit-result', dest='emit_result', default=None,
                    help='把发布结果(note_id/url)原子写到此 JSON,给后端 worker 回传')
    pp.add_argument('--storage-state', dest='storage_state', default=None,
                    help='从此 JSON 载入登录态,发布前灌进采集层账号库(后端 DB 为单一事实源,免依赖本地 accounts.json 同步)')
    pp.add_argument('--acct-nickname', dest='acct_nickname', default=None)
    pp.add_argument('--acct-proxy', dest='acct_proxy', default=None)

    pr = sub.add_parser('refresh', help='校验某账号登录态是否还有效')
    pr.add_argument('account')
    pr.add_argument('--proxy', default='')

    sub.add_parser('accounts', help='列出所有账号')

    args = p.parse_args()
    _setup_log()

    if args.cmd == 'accounts':
        sys.exit(_cmd_accounts(args))
    handler = {'login': _cmd_login, 'publish': _cmd_publish, 'refresh': _cmd_refresh}[args.cmd]
    sys.exit(asyncio.run(handler(args)))


if __name__ == '__main__':
    main()
