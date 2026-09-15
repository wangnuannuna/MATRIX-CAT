# -*- coding: utf-8 -*-
"""扫码登录:CloakBrowser 开抖音创作者页,抖音 App 扫码 → 收割 storage_state 存库。

对标蚁小二登录思路——不逆扫码协议,只等"登录成功才有的东西"出现。抖音这里分两步等:
  1) 会话 cookie(sessionid 等)落地 = 登录成功;
  2) **导航到发布页触发 secsdk,再等它把 EC 私钥 / ticket 种进 cookie** —— 这两个 key 是
     bd-ticket-guard 的命根子(见 [[project_matrixcat_douyin_publisher]])。收割后立刻用
     probe 体检,当场告诉你 secsdk 拿全没有(这是整条协议路唯一的真风险点)。
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import config, probe, protocol
from shared import browser

logger = logging.getLogger(config.PLATFORM)

# 登录成功才会有的会话 cookie(任一带值即已登录)
LOGIN_COOKIES = ("sessionid", "sessionid_ss", "sid_tt", "sid_guard", "uid_tt")


@dataclass
class LoginResult:
    success: bool
    account_id: str
    nickname: Optional[str] = None
    user_id: Optional[str] = None
    storage_state: Optional[Dict[str, Any]] = None
    secsdk_ready: bool = False
    secsdk_report: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    elapsed_sec: float = 0.0
    already_logged_in: bool = False


async def _douyin_cookies(ctx) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        for c in await ctx.cookies():
            if "douyin.com" in (c.get("domain") or ""):
                name = c.get("name")
                if name:
                    out[name] = c.get("value", "")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"读 cookie 异常(忽略): {e}")
    return out


def _logged_in(cookies: Dict[str, str]) -> bool:
    return any(cookies.get(n) for n in LOGIN_COOKIES)


def _secsdk_present(cookies: Dict[str, str]) -> bool:
    return all(cookies.get(k) for k in config.REQUIRED_SECSDK_COOKIES)


async def _read_user_info(page, cookies: Dict[str, str]) -> Dict[str, Any]:
    """昵称/去重 id,best-effort。取不到不影响登录(选择器可能随创作者中心改版)。"""
    info: Dict[str, Any] = {}
    if cookies.get("uid_tt"):
        info["user_id"] = cookies["uid_tt"]   # 稳定的去重用 id(非发布用的数字 uid)
    try:
        name = await page.evaluate(
            "() => { const el = document.querySelector('[class*=nickname],[class*=user-name],"
            "[class*=userName]'); return el ? (el.textContent||'').trim() : null; }")
        if name:
            info["nickname"] = name[:40]
    except Exception:
        pass
    return info


async def login(account_id: str, *, proxy: Optional[str] = None,
                headless: Optional[bool] = None, save_qr: bool = False,
                qr_timeout: float = config.LOGIN_TIMEOUT, secsdk_timeout: float = 25.0,
                store=None) -> LoginResult:
    start = time.time()
    store = store or protocol.get_store()
    sess = await browser.open_session(
        account_id, profiles_dir=config.PROFILES_DIR, goto_url=config.LOGIN_URL,
        scene="login", proxy=proxy, headless=headless)
    try:
        # 已登录(持久化 profile 里还有有效态):直接去发布页收 secsdk,不用再扫。
        if _logged_in(await _douyin_cookies(sess.ctx)):
            return await _finish(sess, account_id, store, sess.proxy, start,
                                 already=True, secsdk_timeout=secsdk_timeout)

        headless_eff = config.headless_for("login") if headless is None else headless
        if headless_eff or save_qr:
            os.makedirs(config.QR_DIR, exist_ok=True)
            png = os.path.join(config.QR_DIR, f"qr_{account_id}.png")
            try:
                await sess.page.screenshot(path=png)
                print(f"[{account_id}] 二维码截图已存 → {png}(用抖音 App 扫码)")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"二维码截图失败: {e}")
        else:
            print(f"[{account_id}] 已弹出浏览器窗口,请用抖音 App 扫码登录…")

        deadline = time.time() + qr_timeout
        while time.time() < deadline:
            if _logged_in(await _douyin_cookies(sess.ctx)):
                await asyncio.sleep(1.0)  # 让其余登录态 cookie 落全
                return await _finish(sess, account_id, store, sess.proxy, start,
                                     already=False, secsdk_timeout=secsdk_timeout)
            await asyncio.sleep(1.0)

        return LoginResult(False, account_id, error="登录超时(未在时限内扫码完成)",
                           elapsed_sec=time.time() - start)
    except Exception as e:  # noqa: BLE001
        logger.exception("登录异常")
        return LoginResult(False, account_id, error=str(e), elapsed_sec=time.time() - start)
    finally:
        await sess.aclose()


async def _finish(sess, account_id: str, store, proxy, start: float,
                  *, already: bool, secsdk_timeout: float) -> LoginResult:
    # 导航到发布页,触发 secsdk 种下 bd-ticket-guard 要的 EC 私钥 / ticket,再等它们落地。
    try:
        await sess.page.goto(config.PUBLISH_URL, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"导航发布页异常(继续): {e}")

    deadline = time.time() + secsdk_timeout
    cookies: Dict[str, str] = {}
    while time.time() < deadline:
        cookies = await _douyin_cookies(sess.ctx)
        if _secsdk_present(cookies):
            await asyncio.sleep(0.8)  # 再缓一下让 ticket/ree 也写全
            cookies = await _douyin_cookies(sess.ctx)
            break
        await asyncio.sleep(0.8)

    user_info = await _read_user_info(sess.page, cookies)
    storage_state = await sess.storage_state()
    store.save_login(account_id, storage_state, nickname=user_info.get("nickname"),
                     user_id=user_info.get("user_id"), proxy=proxy)

    rep = probe.check_cookies(cookies)
    ready = bool(rep.get("ok"))
    if ready:
        print(f"[{account_id}] ✅ 登录成功且 secsdk 齐全,可直发(bd-ticket-guard 签名自校验通过)")
    else:
        print(f"[{account_id}] ⚠️  登录成功,但 secsdk 未拿全 —— bd-ticket-guard 还签不出。"
              f"在发布页多停留几秒重试,或重登;详情:`run.py probe {account_id}`")
    logger.info(f"登录完成 account={account_id} secsdk_ready={ready} "
                f"{'(复用已有态)' if already else ''}")
    return LoginResult(True, account_id, nickname=user_info.get("nickname"),
                       user_id=user_info.get("user_id"), storage_state=storage_state,
                       secsdk_ready=ready, secsdk_report=rep,
                       elapsed_sec=time.time() - start, already_logged_in=already)


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
