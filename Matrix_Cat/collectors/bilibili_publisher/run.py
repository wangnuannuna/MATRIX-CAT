# -*- coding: utf-8 -*-
"""
B站登录 + 发布 命令行入口。

  # 登录(弹窗扫码)
  python run.py login myacc
  python run.py login myacc --proxy http://user:pass@host:port

  # 发视频(封面必填;分区 tid 建议显式给)
  python run.py publish myacc --video v.mp4 --cover c.jpg --title 标题 --desc 正文 --topics 科技 教程 --tid 122

  # 发图文(图文动态;需先真机校准 BILI_DYN_CALIBRATED=1)
  python run.py publish myacc --images a.jpg b.jpg --title 标题 --desc 正文 --topics 日常

  # 账号列表 / 校验登录态
  python run.py accounts
  python run.py refresh myacc

B站 web 投稿只有公开一档;--visibility private/friends 会被拒(不支持,避免误公开)。
"""
import argparse
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Windows 控制台默认 GBK,打印 ✅/中文会崩 UnicodeEncodeError;强制转 UTF-8。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8')
    except Exception:
        pass

from bilibili import config, login as login_mod, note, protocol  # noqa: E402
from bilibili.publish_image import publish_image  # noqa: E402
from bilibili.publish_video import publish_video  # noqa: E402


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
    """把 LoginResult 原子写成 JSON(含 storage_state),给后端 worker 回写 DB。"""
    _dump_json(path, {'success': bool(r.success), 'account_id': r.account_id,
                      'nickname': r.nickname, 'user_id': r.user_id, 'avatar': r.avatar,
                      'error': r.error, 'elapsed_sec': r.elapsed_sec,
                      'already_logged_in': r.already_logged_in,
                      'storage_state': r.storage_state})


async def _cmd_login(args):
    emit = getattr(args, 'emit_state', None)
    proxy = args.proxy or os.getenv('BILIBILI_PROXY') or None   # 代理优先走 env,免得口令进 argv
    try:
        r = await login_mod.login(args.account, proxy=proxy,
                                  headless=_headless(args), save_qr=args.save_qr)
    except Exception as e:          # open_session 取代理失败等在 login() try 外抛,也要回传
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
    proxy = args.proxy or os.getenv('BILIBILI_PROXY') or None   # 代理优先走 env
    try:
        if args.video:
            r = await publish_video(args.account, args.video, args.cover,
                                    title=args.title, desc=args.desc, topics=args.topics,
                                    visibility=args.visibility, is_original=args.original,
                                    proxy=proxy, headless=_headless(args),
                                    tid=args.tid, source=args.source or '')
        else:
            if not args.images:
                if emit:
                    _dump_json(emit, {'ok': False, 'error': '发图文需 --images,发视频需 --video'})
                print('❌ 发图文需 --images,发视频需 --video'); return 1
            r = await publish_image(args.account, args.images,
                                    title=args.title, desc=args.desc, topics=args.topics,
                                    visibility=args.visibility, is_original=args.original,
                                    proxy=proxy, headless=_headless(args))
    except (protocol.PublishError, note.VisibilityUnsupportedError) as e:
        if emit:
            _dump_json(emit, {'ok': False, 'error': str(e)})
        print(f'❌ 发布失败: {e}')
        return 1
    except Exception as e:
        if emit:
            _dump_json(emit, {'ok': False, 'error': f'{type(e).__name__}: {e}'})
        raise
    if emit:
        _dump_json(emit, {'ok': True, 'work_id': r.get('work_id'), 'url': r.get('url')})
    print(f'✅ 发布成功  work_id={r["work_id"]}\n   {r.get("url") or ""}')
    return 0


async def _cmd_refresh(args):
    store = protocol.get_store()
    acc = store.get(args.account)
    if not acc:
        print(f'❌ 账号 {args.account} 不存在'); return 1
    ok, info = await protocol.check_login(args.account, acc.get('storage_state') or {},
                                          proxy=(args.proxy or acc.get('last_proxy')))
    print(f'{"✅" if ok else "❌"} {args.account}  nickname={info.get("nickname")}  {info.get("error") or ""}')
    return 0 if ok else 1


def _cmd_accounts(_args):
    store = protocol.get_store()
    st = store.stats()
    print(f'账号总数 {st["total"]}  已登录 {st["logged_in"]}  冷却中 {st["cooling"]}')
    print(f'文件: {st["file"]}')
    import time as _t
    for a in store.list_accounts():
        cd = a.get('cooldown_until', 0) or 0
        cool = f' 冷却剩 {int(cd - _t.time())}s' if cd > _t.time() else ''
        uid = a.get('user_id') or '-'
        print(f'  - {a.get("account_id"):<16} {a.get("nickname") or "":<12} uid={uid}{cool}')
    return 0


def _headless(args):
    if getattr(args, 'headless', False):
        return True
    if getattr(args, 'headed', False):
        return False
    return None  # 交给场景默认(登录有头;发布不开浏览器,此项对发布无效)


def main():
    p = argparse.ArgumentParser(description='B站登录+发布(登录 CloakBrowser 扫码 / 发布 curl 直连)')
    sub = p.add_subparsers(dest='cmd', required=True)

    pl = sub.add_parser('login', help='扫码登录一个账号')
    pl.add_argument('account')
    pl.add_argument('--proxy', default='')
    pl.add_argument('--save-qr', action='store_true', help='把二维码截图存文件(无头/远端扫码用)')
    pl.add_argument('--headless', action='store_true')
    pl.add_argument('--headed', action='store_true')
    pl.add_argument('--emit-state', dest='emit_state', default=None,
                    help='把登录结果(含 storage_state)原子写到此 JSON,给后端 worker 回传')

    pp = sub.add_parser('publish', help='发布视频或图文动态')
    pp.add_argument('account')
    pp.add_argument('--images', nargs='+', help='图文动态:一张或多张图片路径')
    pp.add_argument('--video', help='视频文件路径')
    pp.add_argument('--cover', help='视频封面图路径(发视频必填)')
    pp.add_argument('--title', default='')
    pp.add_argument('--desc', default='')
    pp.add_argument('--topics', nargs='*', default=None, help='话题/标签(不带#)')
    pp.add_argument('--tid', type=int, default=None, help='视频分区 id(建议显式给,如 122=野生技术协会)')
    pp.add_argument('--source', default='', help='转载来源 URL(给了即按转载 copyright=2)')
    pp.add_argument('--visibility', default='public', choices=['public', 'private', 'friends'])
    pp.add_argument('--original', action='store_true', help='声明原创(自制)')
    pp.add_argument('--proxy', default='')
    pp.add_argument('--headless', action='store_true')
    pp.add_argument('--headed', action='store_true')
    pp.add_argument('--emit-result', dest='emit_result', default=None,
                    help='把发布结果(work_id/url)原子写到此 JSON,给后端 worker 回传')

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
