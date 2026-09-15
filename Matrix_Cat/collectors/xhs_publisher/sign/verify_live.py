# -*- coding: utf-8 -*-
"""P2 真网络验证:纯协议(Node 出签 + curl_cffi 直发,不开浏览器)打通小红书读接口。

拿一个【真实登录态】的账号,对 GET /api/galaxy/user/info 用 NodeSigner 出 X-s/X-t、
curl_cffi 带 cookie 直发,看服务器认不认。返回 200 + 拿到 userName = 纯协议链路通。

跑法(项目根 collectors/xhs_publisher 下,用带 curl_cffi 的解释器):
    python sign/verify_live.py                 # 自动挑账号库里第一个已登录的号
    python sign/verify_live.py <account_id>
    python sign/verify_live.py --storage-state C:/path/ss.json
    python sign/verify_live.py <account_id> --proxy http://user:pass@host:port
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # xhs_publisher 上目录

from xhs import config, protocol            # noqa: E402
from xhs.signer import get_signer           # noqa: E402


def _load_identity(args):
    """拿到 {a1, cookie_header, ua_hint}。来源:--storage-state 文件 或 账号库里的号。"""
    if args.storage_state:
        with open(args.storage_state, encoding="utf-8") as f:
            ss = json.load(f)
        aid = args.account or "(storage-state)"
    else:
        store = protocol.get_store()
        aid = args.account
        if not aid:
            ids = [a.get("account_id") for a in store.list_accounts()
                   if (a.get("storage_state") or {}).get("cookies")]
            if not ids:
                raise SystemExit("账号库里没有带登录态的号。先登录/发布一次(会把 DB 登录态灌进账号库),或用 --storage-state。")
            aid = ids[0]
            print(f"[i] 未指定账号,自动选中已登录号: {aid}")
        acc = store.get(aid)
        if not acc:
            raise SystemExit(f"账号 {aid} 不在账号库。")
        ss = acc.get("storage_state") or {}

    ident = protocol.extract_identity(ss)
    if not ident.get("a1"):
        raise SystemExit("登录态里没有 a1(设备指纹),没法签名。请重新登录该号。")
    # 拼 Cookie 头:所有 xiaohongshu.com 域 cookie
    cookies = ss.get("cookies") or []
    jar = "; ".join(f"{c['name']}={c['value']}" for c in cookies
                    if "xiaohongshu" in (c.get("domain") or "") and c.get("name"))
    return aid, ident["a1"], ident.get("b1") or "", jar


def _sign_path(method, path, query):
    if method == "GET" and config.get_sign_uri_mode() == "path_query":
        return path + (query or "")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("account", nargs="?", default=None)
    ap.add_argument("--storage-state", dest="storage_state", default=None)
    ap.add_argument("--proxy", default=None, help="不给则直连(trust_env=False 不吃系统 VPN)")
    args = ap.parse_args()

    aid, a1, b1, cookie_header = _load_identity(args)
    print(f"[i] 账号 {aid}  a1={a1[:16]}…  b1={'有' if b1 else '无'}  cookie {len(cookie_header)} 字节")

    from curl_cffi import requests as cffi
    sess = cffi.Session(impersonate=config.CURL_IMPERSONATE, trust_env=False,
                        proxies=({"http": args.proxy, "https": args.proxy} if args.proxy else None))
    signer = get_signer()

    def signed_get(path, query, with_common, tag):
        sp = _sign_path("GET", path, query)
        sig = signer.sign(sp, {}, a1, b1=b1, common=with_common)
        headers = {
            "user-agent": config.CURL_UA, "sec-ch-ua": config.CURL_CH_UA,
            "referer": config.REFERER, "origin": config.ORIGIN,
            "accept": "application/json, text/plain, */*", "accept-language": "zh-CN,zh;q=0.9",
            "cookie": cookie_header, "x-s": sig["xs"], "x-t": str(sig["xt"]),
        }
        if with_common and sig.get("xsc"):
            headers["x-s-common"] = sig["xsc"]
        r = sess.get(config.CREATOR_HOST + path + (query or ""), headers=headers, timeout=20)
        try:
            d = r.json()
        except Exception:  # noqa: BLE001
            d = None
        ok = r.status_code == 200 and isinstance(d, dict) and (d.get("success") or d.get("data") is not None)
        print(f"[{tag}] {'含X-S-Common ' if with_common else '仅X-s/X-t '}GET {path} -> HTTP {r.status_code}  {'OK' if ok else '返回:'+((r.text or '')[:120])}")
        return ok, d

    # A) 读接口(不需 X-S-Common)—— 证明 Node 出签 + curl 直发通
    okA, dA = signed_get(config.PATH_USER_INFO, "", False, "A")
    if okA:
        data = (dA or {}).get("data") or {}
        print(f"    userName={data.get('userName') or data.get('nickname')!r}")

    # B) 发布端点 permit,带完整三件套(含我们 Node 生成的 X-S-Common)—— 验 X-S-Common 是否被接受
    permit_q = "?biz_name=spectrum&scene=image&file_count=1&version=1&source=web"
    okB, dB = signed_get(config.PATH_UPLOAD_PERMIT, permit_q, True, "B")

    print()
    if okA and okB:
        print("[纯协议全链验证通过] Node 出 X-s/X-t/X-S-Common + curl 直发,全程不开浏览器:")
        print("  · 读接口(user/info)200 ✓   · 发布端点(permit,含我们的 X-S-Common)200 ✓")
        print("  → X-S-Common 已被小红书发布端点接受,create_note 写接口可上纯协议。")
        return 0
    if okA and not okB:
        print("[部分通过] 读接口通,但 permit(带 X-S-Common)未过 —— X-S-Common 可能需按当前版校准或用本号真 b1。把上面 B 行返回发我。")
        return 2
    print("[未通过] 读接口都没过 —— 多半该号登录态已失效,点「重新登录」重扫后再试。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
