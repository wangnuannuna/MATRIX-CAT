# -*- coding: utf-8 -*-
"""
扫码登录：起一个 CloakBrowser 窗口开小红书创作者登录页，手机 App 扫码 → 拿 storage_state 存库。

对标蚁小二的登录思路——不逆向扫码/短信协议本身，只等"登录成功才会有的东西"出现：这里等
access-token-creator cookie 落地(比抓某个业务接口更直接、抗前端改版)。默认弹出真窗口直接扫屏上的码；
无头场景可 save_qr 截图给远端扫。登录态天然带 a1(cookie)/b1(localStorage)，发布签名同源可用。
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

TOKEN_COOKIE = 'access-token-creator.xiaohongshu.com'


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


_USER_INFO_JS = r"""() => {
  const pick = (o) => {
    if (!o || typeof o !== 'object') return null;
    const nick = o.userName || o.nickname || o.nick_name || o.nickName || o.name;
    if (!nick) return null;
    return {nickname: nick,
            user_id: o.userId || o.redId || o.user_id || o.red_id || o.userIdStr || o.id || null,
            avatar: o.userAvatar || o.avatar || o.image || o.images || null};
  };
  // 1) 老路子:USER_INFO_FOR_BIZ 直取
  try { const r = pick(JSON.parse(localStorage.getItem('USER_INFO_FOR_BIZ') || 'null')); if (r) return r; } catch (e) {}
  // 2) 兜底:扫整个 localStorage,找第一个带昵称字段的 JSON 对象(抗改键名)
  for (let i = 0; i < localStorage.length; i++) {
    try {
      const v = localStorage.getItem(localStorage.key(i));
      if (!v || (v[0] !== '{' && v[0] !== '[')) continue;
      const obj = JSON.parse(v);
      const r = pick(obj) || (obj && pick(obj.userInfo)) || (obj && pick(obj.user)) || (obj && pick(obj.data));
      if (r) return r;
    } catch (e) {}
  }
  return null;
}"""


async def _read_user_info(page, tries: int = 6) -> Dict[str, Any]:
    """拿昵称/UID/头像(best-effort)。先 USER_INFO_FOR_BIZ,读不到就扫整个 localStorage 找带
    昵称字段的对象(小红书改键名也不怕)。登录后要等页面重定向到创作者页、localStorage 落全,
    所以轮询几次。抓不到只是没名字(不影响登录成功),外层已兜底可手动改名。"""
    for _ in range(max(1, tries)):
        try:
            d = await page.evaluate(_USER_INFO_JS)
            if d and d.get('nickname'):
                return {'nickname': d.get('nickname'),
                        'user_id': d.get('user_id'), 'avatar': d.get('avatar')}
        except Exception:
            pass
        await page.wait_for_timeout(800)
    return {}


async def _has_token(ctx) -> bool:
    try:
        for c in await ctx.cookies():
            if c.get('name') == TOKEN_COOKIE and c.get('value'):
                return True
    except Exception:
        pass
    return False


async def _switch_to_qr(page, tries: int = 5) -> bool:
    """登录卡默认【短信登录】,点右上角 img.css-wemwzq 切到【扫码登录】。
    React 点击会抽风,重试到页面真的出现扫码文案为止。"""
    for _ in range(tries):
        try:
            await page.wait_for_selector('img.css-wemwzq', timeout=6000)
            await page.click('img.css-wemwzq', timeout=3000)   # 真实指针点击
        except Exception:
            pass
        try:
            await page.wait_for_function(
                "() => /扫一扫|扫码登录|打开小红书|APP扫码/.test(document.body.innerText||'')",
                timeout=3500)
            return True
        except Exception:
            continue
    return False


async def _save_qr_png(page, png_path: str, poll: int = 12) -> bool:
    """存二维码 png。不认易变的哈希类名——按【登录卡里最大的方形 data:image(~196px)】定位那张
    二维码,直接取它的 base64 落盘(清晰、无缩放、抗改版)。拿到返回 True。"""
    import base64
    js = ("() => { const q=[...document.querySelectorAll('img')].filter(i=>"
          "(i.src||'').startsWith('data:image') && i.naturalWidth>=150 && "
          "Math.abs(i.naturalWidth-i.naturalHeight)<24).sort((a,b)=>b.naturalWidth-a.naturalWidth); "
          "return q[0] ? q[0].src : null; }")
    for _ in range(poll):
        try:
            src = await page.evaluate(js)
        except Exception:
            src = None
        if src and ',' in src:
            try:
                with open(png_path, 'wb') as f:
                    f.write(base64.b64decode(src.split(',', 1)[1]))
                return True
            except Exception as e:
                logger.warning(f'二维码 base64 落盘失败: {e}')
                return False
        await page.wait_for_timeout(500)
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
        # 已登录(持久化 profile 里还有有效态)：直接收割，不用再扫。
        # 落盘用 sess.proxy(实际生效的代理，含 open_session 内 None→默认北京代理 的兜底)，代理粘住号。
        if await _has_token(sess.ctx):
            return await _finish(sess, account_id, store, sess.proxy, start, already=True)

        # 小红书创作登录页默认是【短信登录】,右上角 img.css-wemwzq 是切到【扫码登录】的开关。
        # React 的点击会抽风(点太早/点不动),所以重试几次,直到页面真的出现"扫一扫/扫码"文案为止。
        await _switch_to_qr(sess.page)

        headless_eff = config.headless_for('login') if headless is None else headless
        if headless_eff or save_qr:
            os.makedirs(config.QR_DIR, exist_ok=True)
            png = os.path.join(config.QR_DIR, f'qr_{account_id}.png')
            if not await _save_qr_png(sess.page, png):
                try:
                    await sess.page.screenshot(path=png)   # 兜底:整页(至少含二维码)
                except Exception as e:
                    logger.warning(f'二维码截图失败: {e}')
            print(f'[{account_id}] 二维码已存 → {png}（用小红书 App 扫码）')
        else:
            print(f'[{account_id}] 已弹出浏览器窗口，请用小红书 App 扫码登录…')

        # 轮询登录完成信号：access-token cookie 落地。
        deadline = time.time() + qr_timeout
        while time.time() < deadline:
            if await _has_token(sess.ctx):
                await asyncio.sleep(1.0)  # 让其余登录态 cookie/localStorage 落全
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
    user_info = await _read_user_info(sess.page)
    storage_state = await sess.storage_state()
    # 冻结这个号登录时的真实 UA 进登录态:b1 指纹编码的就是这台浏览器的 UA,纯协议发布时
    # 请求头 UA / X-S-Common 编码的 UA / sec-ch-ua 必须都用它,才能 UA↔b1 自洽、不被风控软封。
    try:
        storage_state['_ua'] = await sess.page.evaluate("() => navigator.userAgent")
    except Exception:  # noqa: BLE001
        pass
    store.save_login(account_id, storage_state,
                     nickname=user_info.get('nickname'),
                     user_id=user_info.get('user_id'),
                     avatar=user_info.get('avatar'),
                     proxy=proxy)
    logger.info(f'登录成功 account={account_id} nickname={user_info.get("nickname")} '
                f'{"(复用已有登录态)" if already else ""}')
    return LoginResult(True, account_id, nickname=user_info.get('nickname'),
                       user_id=user_info.get('user_id'), avatar=user_info.get('avatar'),
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
