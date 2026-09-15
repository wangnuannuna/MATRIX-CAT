# -*- coding: utf-8 -*-
"""
零副作用签名自检：起 CloakBrowser → 开小红书登录页 → 等 _webmsxyw → 对一个 GET 做签名探针。
不登录、不发布、不发任何写请求，只验证"浏览器签名机"这条链路活着。

  python tests/smoke_sign.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8')
    except Exception:
        pass

from xhs import browser, config, signer  # noqa: E402


async def main():
    logging.basicConfig(level='INFO', format='%(asctime)s [%(levelname)s] %(message)s')
    sess = await browser.open_session('smoke_sign', scene='sign',
                                      goto_url=config.LOGIN_URL, headless=True)
    try:
        await sess.wait_webmsxyw()
        ex = signer.SignedApiExecutor(sess.page)
        # 只签名不发送
        r = await ex.sign_probe('GET', config.PATH_USER_INFO)
        xs = r.get('xs', '')
        print('---- 签名探针结果 ----')
        print('ok        :', r.get('ok'))
        print('X-s       :', (xs[:40] + '…') if xs else '(空)')
        print('X-t       :', r.get('xt'))
        print('has_common:', r.get('has_common'))
        print('error     :', r.get('error') or '无')
        good = r.get('ok') and xs.startswith('XYW_')
        print('\n结论:', '✅ 签名链路正常' if good else '⚠️ 签名异常(看 error / 换网络关 VPN)')
        return 0 if good else 1
    finally:
        await sess.aclose()


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
