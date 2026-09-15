# -*- coding: utf-8 -*-
"""
扫码登录：起一个 CloakBrowser 窗口开快手创作者平台，手机快手主 App 扫码 → 拿 storage_state 存库。

对标 xhs 登录思路——不逆向扫码协议本身，只等"登录成功才会有的东西"落地：这里等
kuaishou.web.cp.api_st cookie 出现(比抓某接口更直接、抗前端改版)。未登录时开发布页会自动
302 到 passport 扫码页(sid=kuaishou.web.cp.api，cookie 种根域 .kuaishou.com)；已登录则停发布页。
默认弹真窗口直接扫屏上的码；无头场景 save_qr 截图给远端扫。

⚠️ 二维码只认【快手主 App】扫(极速版/小店不一定认)。参考 collectors/xhs_publisher/xhs/login.py。
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import config, protocol
from shared import browser

logger = logging.getLogger(__name__)


@dataclass
class LoginResult:
    success: bool
    account_id: str
    nickname: Optional[str] = None
    user_id: Optional[str] = None
    avatar: Optional[str] = None
    storage_state: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    elapsed_sec: float = 0.0
    already_logged_in: bool = False


async def _has_login_cookie(ctx) -> bool:
    """登录成功最可靠信号：.kuaishou.com 下 kuaishou.web.cp.api_st 落地。"""
    try:
        for c in await ctx.cookies():
            if c.get('name') == config.CK_LOGIN_ST and c.get('value'):
                return True
    except Exception:
        pass
    return False


async def login(account_id: str, *, proxy: Optional[str] = None,
                headless: Optional[bool] = None, save_qr: bool = False,
                qr_timeout: float = config.LOGIN_TIMEOUT, store=None) -> LoginResult:
    start = time.time()
    store = store or protocol.get_store()
    sess = await browser.open_session(
        account_id, profiles_dir=config.PROFILES_DIR, goto_url=config.LOGIN_URL,
        scene='login', proxy=proxy, headless=headless)
    try:
        # 已登录(持久 profile 里还有有效态)：直接收割。落盘用 sess.proxy(含默认北京代理兜底)，代理粘号。
        if await _has_login_cookie(sess.ctx):
            return await _finish(sess, account_id, store, sess.proxy, start, already=True)

        headless_eff = config.headless_for('login') if headless is None else headless
        if headless_eff or save_qr:
            os.makedirs(config.QR_DIR, exist_ok=True)
            png = os.path.join(config.QR_DIR, f'qr_{account_id}.png')
            try:
                await sess.page.screenshot(path=png)
                print(f'[{account_id}] 二维码截图已存 → {png}（用快手主 App 扫码）')
            except Exception as e:
                logger.warning(f'二维码截图失败: {e}')
        else:
            print(f'[{account_id}] 已弹出浏览器窗口，请用【快手主 App】扫码登录…')

        # 轮询登录完成信号：api_st cookie 落地。
        deadline = time.time() + qr_timeout
        while time.time() < deadline:
            if await _has_login_cookie(sess.ctx):
                await asyncio.sleep(1.0)  # 让其余登录态 cookie 落全
                return await _finish(sess, account_id, store, sess.proxy, start, already=False)
            await asyncio.sleep(1.0)

        return LoginResult(False, account_id, error='登录超时(未在时限内扫码完成)',
                           elapsed_sec=time.time() - start)
    except Exception as e:
        logger.exception('登录异常')
        return LoginResult(False, account_id, error=str(e), elapsed_sec=time.time() - start)
    finally:
        await sess.aclose()


async def _finish(sess, account_id: str, store, proxy, start: float,
                  *, already: bool) -> LoginResult:
    storage_state = await sess.storage_state()
    # 取昵称/uid/头像：调 account/current(localStorage 存不存用户信息未证实，接口最靠谱)。
    ident = protocol.extract_identity(storage_state)
    info: Dict[str, Any] = {}
    try:
        ex = protocol.SignedApiExecutor(sess.page)
        await ex.wait_ready()
        resp = await ex.account_current(ident['api_ph'])
        if resp.result_ok():
            d = resp.json.get('data') or {}
            info = {'nickname': d.get('userName'),
                    'user_id': d.get('userId') or ident.get('user_id'),
                    'avatar': d.get('userAvatar')}
    except Exception as e:
        logger.warning(f'取用户信息失败(不阻断登录落盘): {e}')
    info.setdefault('user_id', ident.get('user_id'))

    store.save_login(account_id, storage_state,
                     nickname=info.get('nickname'), user_id=info.get('user_id'),
                     avatar=info.get('avatar'), proxy=proxy)
    logger.info(f'登录成功 account={account_id} nickname={info.get("nickname")} '
                f'{"(复用已有登录态)" if already else ""}')
    return LoginResult(True, account_id, nickname=info.get('nickname'),
                       user_id=info.get('user_id'), avatar=info.get('avatar'),
                       storage_state=storage_state, elapsed_sec=time.time() - start,
                       already_logged_in=already)


async def batch_login(account_ids: List[str], *, proxy: Optional[str] = None,
                      headless: Optional[bool] = None, save_qr: bool = False,
                      concurrency: int = 4, store=None) -> List[LoginResult]:
    """多号并发登录(各扫各的)。"""
    store = store or protocol.get_store()
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(aid: str) -> LoginResult:
        async with sem:
            return await login(aid, proxy=proxy, headless=headless, save_qr=save_qr, store=store)

    return list(await asyncio.gather(*[_one(a) for a in account_ids]))
