"""端到端冒烟:发码→注册→me→绑号到配额→超额拦→刷新轮换→密码登录,
外加安全项:重复绑号拦、纯数字用户名拦、OTP 连错锁定。
进程内直连 ASGI。跑:python smoke_test.py(先 python init_db.py)"""
import os

# 关掉发送冷却,方便一个号连续发码自测(必须在导入 app 之前设)
os.environ.setdefault("MC_SMS_COOLDOWN", "0")

import asyncio  # noqa: E402
import secrets  # noqa: E402

import httpx  # noqa: E402

from app.main import app  # noqa: E402

BASE = "http://test"


def rand_phone() -> str:
    return "13" + "".join(secrets.choice("0123456789") for _ in range(9))


async def main():
    phone, pw = rand_phone(), "test123456"
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
            # ① 发验证码(开发模式回显)
            r = await c.post("/auth/sms/send", json={"phone": phone})
            assert r.status_code == 200, r.text
            code = r.json().get("dev_code")
            assert code, "没拿到开发回显验证码"
            print("① 验证码:", code)

            # ② 注册
            r = await c.post("/auth/register", json={"phone": phone, "code": code, "password": pw})
            assert r.status_code == 200, r.text
            tok = r.json()
            auth = {"Authorization": f"Bearer {tok['access_token']}"}
            print("② 注册成功")

            # ③ /me + 会员
            r = await c.get("/auth/me", headers=auth)
            assert r.status_code == 200, r.text
            m = r.json()["membership"]
            assert m["active"] and m["plan_code"] == "trial", m
            cap = m["max_accounts"]
            print(f"③ /me 会员=trial 可绑 {cap} 个号")

            # ④ 绑满配额
            for i in range(cap):
                r = await c.post("/accounts/bind", headers=auth,
                                 json={"platform": "xhs", "account_name": f"号{i + 1}"})
                assert r.status_code == 200, r.text
            print(f"④ 绑满 {cap} 个号")

            # ⑤ 超配额 → 403
            r = await c.post("/accounts/bind", headers=auth,
                             json={"platform": "xhs", "account_name": "超额号"})
            assert r.status_code == 403, r.text
            print("⑤ 超配额被拦:", r.json()["detail"])

            # ⑥ 刷新轮换,老 refresh 失效
            r = await c.post("/auth/refresh", json={"refresh_token": tok["refresh_token"]})
            assert r.status_code == 200, r.text
            r2 = await c.post("/auth/refresh", json={"refresh_token": tok["refresh_token"]})
            assert r2.status_code == 401, "老 refresh 应已失效"
            print("⑥ refresh 轮换 OK,老 refresh 已作废")

            # ⑦ 密码登录 + 错密码被拒(登录后换用最新会话的 token——旧会话已被轮换吊销)
            r = await c.post("/auth/login/password", json={"account": phone, "password": pw})
            assert r.status_code == 200, r.text
            auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
            r = await c.post("/auth/login/password", json={"account": phone, "password": "wrong"})
            assert r.status_code == 401
            print("⑦ 密码登录 OK,错密码被拒")

            # ⑧ 腾一个配额位后,重复绑已存在的号 → 409(唯一约束兜底)
            accs = (await c.get("/accounts", headers=auth)).json()
            await c.delete(f"/accounts/{accs[0]['id']}", headers=auth)
            r = await c.post("/accounts/bind", headers=auth,
                             json={"platform": "xhs", "account_name": accs[1]["account_name"]})
            assert r.status_code == 409, r.text
            print("⑧ 重复绑号被拦(409)")

            # ⑨ 纯数字用户名注册 → 422(入参校验)
            r = await c.post("/auth/register", json={
                "phone": rand_phone(), "code": "000000", "password": pw, "username": "13800138000"})
            assert r.status_code == 422, r.text
            print("⑨ 纯数字用户名被拒(422)")

            # ⑩ OTP 连错 5 次锁定:之后正确码也失效
            code2 = (await c.post("/auth/sms/send", json={"phone": phone})).json()["dev_code"]
            for _ in range(5):
                rr = await c.post("/auth/login/sms", json={"phone": phone, "code": "000000"})
                assert rr.status_code == 400, rr.text
            r = await c.post("/auth/login/sms", json={"phone": phone, "code": code2})
            assert r.status_code == 400, "连错后正确码也应失效"
            print("⑩ OTP 连错5次锁定,正确码随之作废")

            # ⑪ 购买会员:升级 pro,配额随套餐变大
            plans = (await c.get("/billing/plans")).json()["plans"]
            assert any(p["code"] == "pro" for p in plans), plans
            r = await c.post("/billing/subscribe", headers=auth, json={"plan_code": "pro"})
            assert r.status_code == 200, r.text
            m = (await c.get("/auth/me", headers=auth)).json()["membership"]
            assert m["plan_code"] == "pro", m
            print(f"⑪ 购买专业版成功,配额升到 {m['max_accounts']} 个号")

    print("\n[通过] 全流程冒烟测试成功")


if __name__ == "__main__":
    asyncio.run(main())
