# -*- coding: utf-8 -*-
"""账号/设备养号(option 2):用纯协议(NodeSigner + curl,不开浏览器)持续做【真用户发帖前会做的创作者端"读"操作】,
给这台 a1 设备积活动史 —— 把"新面孔"养成"老熟人",提升纯协议写接口(create_note)过风控的概率。

只读、不写 → 零封号风险。养号 ≠ 一次见效:建议每天几轮、连养几天,再试纯协议发帖。
**关键前提**:养号期这个号最好用【稳定独享 IP】(KDL 静态/独享 或住宅静态),别用会轮换的 IP——
设备从飘忽 IP 出现反而更像机器,削弱养号效果(逐号独立稳定 IP 是根)。
"""
import asyncio
import logging
import random

from . import config, protocol

logger = logging.getLogger(__name__)

# 创作者端真实读接口(cookie 有效、真用户发帖前常看,实测纯协议 200):个人主页 / 身份 / 笔记数据。
_READS = [
    ("GET", "/api/galaxy/user/info", ""),
    ("GET", "/api/galaxy/creator/home/personal_info", ""),
    ("GET", "/api/galaxy/creator/data/note_stats/new", "?page=1&page_size=10&order_type=0"),
]


async def warm_session(account_id: str, *, proxy=None, rounds: int = 1, store=None) -> dict:
    """给一个号跑 rounds 轮养号读操作。返回 {account_id, ok, total}。"""
    store = store or protocol.get_store()
    acc = store.get(account_id)
    if not acc or not (acc.get("storage_state") or {}).get("cookies"):
        raise protocol.PublishError(f"账号 {account_id} 无登录态,无法养号")
    ss = acc.get("storage_state") or {}
    if not ss.get("_ua"):
        logger.warning(f"[养号] {account_id} 登录态无 _ua(改代码前登的),指纹不自洽、养号效果打折,建议重登一次")
    # 显式传 proxy 就用它(建议给号绑【稳定独享 IP】);否则现取新鲜北京代理(号绑的 KDL 常已过期)。
    eff = await protocol.resolve_proxy(proxy)
    ex = protocol.curl_executor_from_state(ss, proxy=eff)

    ok = tot = 0
    for rd in range(max(1, rounds)):
        reads = list(_READS)
        random.shuffle(reads)                                  # 顺序随机,更像人
        for method, path, q in reads:
            tot += 1
            try:
                resp = await ex.call(method, config.CREATOR_HOST, path, query=q)
                good = (resp.status == 200)
                ok += 1 if good else 0
                logger.info(f"[养号] {account_id} {path} -> {resp.status} {'✓' if good else ''}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[养号] {account_id} {path} 异常: {str(e)[:80]}")
            await asyncio.sleep(random.uniform(4, 11))         # 人味停顿(秒级)
        if rd < rounds - 1:
            await asyncio.sleep(random.uniform(25, 55))        # 轮间更长停顿
    logger.info(f"[养号] {account_id} 完成 {rounds} 轮 · 读成功 {ok}/{tot}")
    return {"account_id": account_id, "ok": ok, "total": tot}


async def warm_all(*, rounds: int = 1, store=None) -> list:
    """给账号库里所有已登录号各养 rounds 轮(串行,别同一 IP 并发)。"""
    store = store or protocol.get_store()
    ids = [a.get("account_id") for a in store.list_accounts()
           if (a.get("storage_state") or {}).get("cookies")]
    out = []
    for aid in ids:
        try:
            out.append(await warm_session(aid, rounds=rounds, store=store))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[养号] {aid} 失败: {str(e)[:80]}")
    return out
