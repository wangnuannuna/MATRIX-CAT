# -*- coding: utf-8 -*-
"""抖音发布器命令行入口。

现已接通(不需真机):
  python run.py selftest              # 离线自测 bd-ticket-guard 签名端口是否自洽
  python run.py probe   <account_id>  # 对已登录账号做 secsdk cookie 体检
  python run.py sign    <account_id>  # 打印一份 create_v2 请求头(人工核对签名/头)
  python run.py accounts              # 账号池统计

待补(下一步):login(CloakBrowser 扫码收 cookie)/ publish(uploader + create_v2)。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))       # douyin_publisher/
ROOT = os.path.dirname(os.path.dirname(HERE))            # 项目根
for _p in (HERE, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared.io_utf8 import setup_utf8, setup_logging  # noqa: E402

setup_utf8()
setup_logging()

from douyin import config, probe, signer  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(prog=config.PLATFORM)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest")
    pr = sub.add_parser("probe"); pr.add_argument("account")
    sg = sub.add_parser("sign"); sg.add_argument("account")
    sub.add_parser("accounts")
    lg = sub.add_parser("login")
    lg.add_argument("account")
    lg.add_argument("--headless", action="store_true", help="无头(截二维码给远端扫,默认有头直接扫屏)")
    lg.add_argument("--save-qr", action="store_true", help="有头也顺手把二维码截图存一份")
    lg.add_argument("--proxy", default=None, help="显式代理 URL(默认境内直连)")
    pb = sub.add_parser("publish")
    pb.add_argument("account")
    pb.add_argument("--video", help="视频文件路径(发视频)")
    pb.add_argument("--images", nargs="+", help="图片路径(发图文)")
    pb.add_argument("--cover", help="视频封面图(可选)")
    pb.add_argument("--title", default="")
    pb.add_argument("--desc", default="")
    pb.add_argument("--visibility", default="public", help="public/private/friends")
    pb.add_argument("--proxy", default=None)
    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        from douyin import uploader
        ok_sign = probe.selftest()
        print()
        ok_up = uploader.selftest()
        return 0 if (ok_sign and ok_up) else 1

    if args.cmd == "probe":
        rep = probe.probe_account(args.account)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep.get("ok") else 2

    if args.cmd == "sign":
        from storage.session_store import AccountStore
        store = AccountStore(config.ACCOUNTS_FILE, cookie_domain=config.COOKIE_DOMAIN)
        acc = store.get(args.account)
        if not acc:
            print(f"账号 {args.account} 不存在,请先 login"); return 2
        headers = signer.build_publish_headers(acc.get("cookies") or {})
        url = signer.build_create_v2_url(acc.get("cookies") or {})
        # cookie 头太长,打印时截断
        show = dict(headers)
        if "Cookie" in show:
            show["Cookie"] = show["Cookie"][:60] + f"...(共 {len(headers['Cookie'])} 字符)"
        print("URL:", url)
        print(json.dumps(show, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "accounts":
        from storage.session_store import AccountStore
        store = AccountStore(config.ACCOUNTS_FILE, cookie_domain=config.COOKIE_DOMAIN)
        print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "login":
        import asyncio
        from douyin import login as login_mod
        r = login_mod.login(args.account, proxy=args.proxy,
                            headless=(True if args.headless else None),
                            save_qr=args.save_qr)
        res = asyncio.run(r)
        print(f"\n登录结果: success={res.success} secsdk_ready={res.secsdk_ready} "
              f"nickname={res.nickname} 用时={res.elapsed_sec:.1f}s"
              + (f" error={res.error}" if res.error else ""))
        return 0 if res.success else 2

    if args.cmd == "publish":
        common = dict(title=args.title, desc=args.desc, visibility=args.visibility,
                      proxy=args.proxy)
        try:
            if args.video:
                from douyin.publish_video import publish_video
                res = publish_video(args.account, args.video, args.cover, **common)
            elif args.images:
                from douyin.publish_image import publish_image
                res = publish_image(args.account, args.images, **common)
            else:
                print("要么 --video 要么 --images"); return 2
        except Exception as e:  # noqa: BLE001
            print(f"发布失败: {type(e).__name__}: {e}")
            return 2
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
        return 0 if res.get("ok") else 2

    return 1


if __name__ == "__main__":
    sys.exit(main())
