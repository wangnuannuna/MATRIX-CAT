# -*- coding: utf-8 -*-
"""养号命令行入口(纯协议只读,零封号风险)。

    python sign/warm_run.py                    # 所有已登录号各养 1 轮
    python sign/warm_run.py <account_id>       # 指定号
    python sign/warm_run.py <account_id> --rounds 3
    python sign/warm_run.py --rounds 2         # 所有号各 2 轮

建议:每天跑几轮、连养几天,再试纯协议发帖(XHS_PURE_PROTO=1)。养号期用稳定独享 IP 效果最好。
"""
import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xhs import warm  # noqa: E402


def main():
    # 养号焊死【住宅 IP 直连】:境内 xhs 直连走物理网卡=你家稳定住宅 IP(不轮换),这是给 a1 积历史的必要条件。
    # KDL 机房代理会轮换 IP、且是机房 IP,养不熟。要指定别的稳定住宅代理:设 CRAWLER_PROXY_MODE / XHS_PROXY。
    os.environ.setdefault("CRAWLER_PROXY_MODE", "direct")
    os.environ.setdefault("CRAWLER_ALLOW_DIRECT_FALLBACK", "1")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("account", nargs="?", default=None)
    ap.add_argument("--rounds", type=int, default=1)
    args = ap.parse_args()

    async def go():
        if args.account:
            r = await warm.warm_session(args.account, rounds=args.rounds)
            print(f"\n[养号完成] {r['account_id']} 读成功 {r['ok']}/{r['total']}")
        else:
            rs = await warm.warm_all(rounds=args.rounds)
            print(f"\n[养号完成] {len(rs)} 个号:" + " · ".join(f"{x['account_id']} {x['ok']}/{x['total']}" for x in rs))
    asyncio.run(go())


if __name__ == "__main__":
    main()
