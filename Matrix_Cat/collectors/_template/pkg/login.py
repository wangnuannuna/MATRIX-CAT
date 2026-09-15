# -*- coding: utf-8 -*-
"""登录：起 CloakBrowser(一号一 profile) 让用户扫码/密码登录，拿 storage_state 存库。
协议直发只登录一次，之后反复用，直到 cookie 过期再来。参考 collectors/xhs_publisher/xhs/login.py。
"""
import logging

from . import config, protocol
from shared import browser

logger = logging.getLogger(config.PLATFORM)


async def login(account_id, *, proxy=None, headless=None, store=None):
    """登录一个号，成功后 store.save_login(account_id, storage_state, nickname=..., user_id=...)。"""
    store = store or protocol.get_store()
    sess = await browser.open_session(
        account_id, profiles_dir=config.PROFILES_DIR, goto_url=config.LOGIN_URL,
        scene='login', proxy=proxy, headless=headless)
    try:
        # TODO（参考 xhs/login.py）：
        #   1) 轮询"登录成功才会有的东西"(如某 cookie 落地 / 跳转到 HOME_URL)；
        #   2) storage_state = await sess.storage_state()；读昵称/uid/头像；
        #   3) store.save_login(account_id, storage_state, nickname=..., user_id=..., proxy=sess.proxy)。
        raise NotImplementedError("按平台登录完成信号实现；参考 xhs/login.py")
    finally:
        await sess.aclose()
