# -*- coding: utf-8 -*-
"""命令行入口。复制模板成新平台后：把 `from pkg ...` 的 pkg 改成你的包名(如 douyin)。

  python run.py login   <account_id>
  python run.py publish <account_id> --images a.jpg b.jpg --title 标题
  python run.py publish <account_id> --video v.mp4 --cover c.jpg --title 标题
  python run.py accounts
"""
import argparse
import asyncio
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))     # <platform>_publisher/
ROOT = os.path.dirname(os.path.dirname(HERE))          # 项目根
for _p in (HERE, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared.io_utf8 import setup_utf8, setup_logging  # noqa: E402

setup_utf8()
setup_logging()

from pkg import config, login as login_mod, protocol  # noqa: E402  TODO 改包名
from pkg.publish_image import publish_image           # noqa: E402
from pkg.publish_video import publish_video           # noqa: E402

logger = logging.getLogger(config.PLATFORM)


def main(argv=None):
    ap = argparse.ArgumentParser(prog=config.PLATFORM)
    sub = ap.add_subparsers(dest='cmd', required=True)
    lp = sub.add_parser('login'); lp.add_argument('account')
    pp = sub.add_parser('publish'); pp.add_argument('account')
    pp.add_argument('--images', nargs='+'); pp.add_argument('--video'); pp.add_argument('--cover')
    pp.add_argument('--title', default=''); pp.add_argument('--desc', default='')
    pp.add_argument('--visibility', default='public')
    sub.add_parser('accounts')
    args = ap.parse_args(argv)

    if args.cmd == 'accounts':
        print(protocol.get_store().stats()); return 0

    async def _run():
        if args.cmd == 'login':
            r = await login_mod.login(args.account)
            print(r); return 0
        common = dict(title=args.title, desc=args.desc, visibility=args.visibility)
        if args.video:
            r = await publish_video(args.account, args.video, args.cover, **common)
        else:
            r = await publish_image(args.account, args.images, **common)
        print(r); return 0

    return asyncio.run(_run())


if __name__ == '__main__':
    sys.exit(main())
