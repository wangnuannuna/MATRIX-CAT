# -*- coding: utf-8 -*-
"""secsdk cookie 完整性体检 + 离线自测。

抖音直发唯一的真风险不是签名算法(那是标准 ECDSA,signer.py 已复刻),而是**登录态里
secsdk 的 EC 私钥 / ticket 能不能拿全、会不会过期**。这个模块干两件事:

  1. check_cookies / probe_account —— 拿真账号的 cookie 逐项体检(哪几个 secsdk 键在、
     能不能解析、能不能真的签出并自校验),给发布层做 fail-fast 的依据。
  2. selftest —— 不需要真号:现场生成一把 P-256 私钥、按抖音的嵌套结构造一份假 cookie,
     跑通 signer 全链并用公钥验签。用来证明"端口本身是对的",把真机要排查的范围缩到
     "cookie 里到底有没有这些键"。
"""
import base64
import json
import logging
from urllib.parse import quote

from . import config, signer

logger = logging.getLogger(config.PLATFORM)


# ════════════════════════════════════════════════════════════════════════════
# 体检
# ════════════════════════════════════════════════════════════════════════════
def check_cookies(cookies) -> dict:
    """对一份 cookie(dict 或 header 串)做 secsdk 体检。返回结构化报告,不抛异常。"""
    cm = signer.cookie_map(cookies)
    report = {
        "ok": False,
        "cookie_count": len(cm),
        "checks": {},          # 键名 -> {present, parsed, detail}
        "ree_public_key": {"present": False, "length": 0},
        "mstoken": {"present": False, "fallback": False},
        "sign": {"ok": False, "self_verified": False, "error": None},
    }

    # 1) EC 私钥
    ec_chk = {"present": config.COOKIE_EC_PRIVATE in cm, "parsed": False, "detail": ""}
    try:
        pem = signer.get_ec_private_pem(cm)
        ec_chk["parsed"] = True
        ec_chk["detail"] = f"ec_privateKey 长度 {len(pem)}"
    except Exception as e:  # noqa: BLE001
        ec_chk["detail"] = str(e)
    report["checks"][config.COOKIE_EC_PRIVATE] = ec_chk

    # 2) ticket / ts_sign
    wp_chk = {"present": config.COOKIE_WEB_PROTECT in cm, "parsed": False, "detail": ""}
    ticket = ""
    try:
        g = signer.get_ticket_bundle(cm)
        ticket = g.get("ticket", "")
        wp_chk["parsed"] = True
        wp_chk["detail"] = (f"ticket 前缀 {ticket[:8]!r}(web-version="
                            f"{'2' if str(ticket).startswith('hash') else '1'}), "
                            f"ts_sign={'有' if g.get('ts_sign') else '无'}")
    except Exception as e:  # noqa: BLE001
        wp_chk["detail"] = str(e)
    report["checks"][config.COOKIE_WEB_PROTECT] = wp_chk

    # 3) ree-public-key(头用,缺了也能签但服务端可能拒)
    ree = signer.get_ree_public_key(cm)
    report["ree_public_key"] = {"present": bool(ree), "length": len(ree)}

    # 4) msToken
    tok = cm.get(config.COOKIE_MSTOKEN)
    report["mstoken"] = {"present": bool(tok), "fallback": not bool(tok)}

    # 5) 真签一发 + 自校验(证明这份 cookie 能签、且签名对私钥自洽)
    if ec_chk["parsed"] and wp_chk["parsed"]:
        try:
            client_data, meta = signer.client_sign(cm)
            # 解开 client_data,拿 req_sign 回验
            payload = json.loads(base64.b64decode(client_data))
            sig_der = base64.b64decode(payload["req_sign"])
            signed_str = (f"ticket={meta['ticket']}&path={config.PATH_CREATE_V2}"
                          f"&timestamp={meta['timestamp']}")
            pem = signer.get_ec_private_pem(cm)
            priv = _load_priv(pem)
            verified = signing_verify(priv, sig_der, signed_str)
            report["sign"] = {"ok": True, "self_verified": verified, "error": None,
                              "client_data_len": len(client_data)}
        except Exception as e:  # noqa: BLE001
            report["sign"] = {"ok": False, "self_verified": False, "error": str(e)}

    report["ok"] = (ec_chk["parsed"] and wp_chk["parsed"]
                    and report["sign"]["ok"] and report["sign"]["self_verified"])
    return report


def probe_account(account_id: str) -> dict:
    """从 AccountStore 取号做体检。"""
    from storage.session_store import AccountStore
    store = AccountStore(config.ACCOUNTS_FILE, cookie_domain=config.COOKIE_DOMAIN)
    acc = store.get(account_id)
    if not acc:
        return {"ok": False, "error": f"账号 {account_id} 不存在,请先 login"}
    rep = check_cookies(acc.get("cookies") or {})
    rep["account_id"] = account_id
    rep["nickname"] = acc.get("nickname")
    return rep


# ════════════════════════════════════════════════════════════════════════════
# 小工具(验签用 private→public;避免让 signer 依赖具体 key 对象)
# ════════════════════════════════════════════════════════════════════════════
def _load_priv(pem):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    return load_pem_private_key(pem.encode() if isinstance(pem, str) else pem, password=None)


def signing_verify(priv, sig_der, data) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    try:
        priv.public_key().verify(sig_der, data.encode(), ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False


# ════════════════════════════════════════════════════════════════════════════
# 离线自测:合成一份 secsdk cookie,跑通全链
# ════════════════════════════════════════════════════════════════════════════
def _synth_cookies():
    """现场造一把 P-256,按抖音嵌套结构拼一份假 cookie(URL 编码的双层 JSON / base64)。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub_uncompressed = priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    ree_b64 = base64.b64encode(pub_uncompressed).decode()

    def nested(inner: dict) -> str:               # {"data": "<json串>"} → URL 编码
        return quote(json.dumps({"data": json.dumps(inner)}))

    def b64json(obj: dict) -> str:                # base64(json) → URL 编码
        return quote(base64.b64encode(json.dumps(obj).encode()).decode())

    return {
        config.COOKIE_EC_PRIVATE: nested({"ec_privateKey": pem}),
        config.COOKIE_WEB_PROTECT: nested({"ticket": "hash.deadbeef00", "ts_sign": "ts.abc123"}),
        config.COOKIE_TICKET_GUARD: b64json({"bd-ticket-guard-ree-public-key": ree_b64}),
        config.COOKIE_MSTOKEN: "SYNTH_msToken_for_selftest",
    }


def selftest() -> bool:
    """无真号跑通:合成 cookie → 体检 → 断言签名自洽 + web-version/ree/msToken 都对。"""
    cookies = _synth_cookies()
    rep = check_cookies(cookies)

    checks = [
        ("EC 私钥解析", rep["checks"][config.COOKIE_EC_PRIVATE]["parsed"]),
        ("ticket 解析", rep["checks"][config.COOKIE_WEB_PROTECT]["parsed"]),
        ("web-version=2(ticket 以 hash 开头)",
         "web-version=2" in rep["checks"][config.COOKIE_WEB_PROTECT]["detail"]),
        ("ree-public-key 拿到", rep["ree_public_key"]["present"]),
        ("msToken 拿到(非兜底)", rep["mstoken"]["present"] and not rep["mstoken"]["fallback"]),
        ("签名成功", rep["sign"]["ok"]),
        ("签名对私钥自校验通过", rep["sign"]["self_verified"]),
        ("整体 ok", rep["ok"]),
    ]
    ok = all(v for _, v in checks)
    print("=== douyin signer 离线自测 ===")
    for name, v in checks:
        print(f"  [{'PASS' if v else 'FAIL'}] {name}")
    if not ok:
        print("--- 报告 ---")
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    print(f"=== 结论: {'全通过 ✅ 签名端口自洽' if ok else '有失败 ❌'} ===")
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if selftest() else 1)
