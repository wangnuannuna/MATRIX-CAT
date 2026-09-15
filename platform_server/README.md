# Matrix_Cat 云端鉴权服务(platform_server)

矩阵猫 SaaS 的**用户账号 + 会员授权**后端。独立于本地发布工具,只管"谁能用、用到什么档"。
默认 **SQLite + 内存兜底**开箱即跑;上线把 `.env` 换成 **PostgreSQL + Redis** 即可,代码不用动。

## 能力

- 注册/登录:手机号+短信验证码、账号(手机号/用户名)+密码
- 令牌:access(JWT,30min)+ refresh(随机串存库,可轮换/吊销)
- 会员:套餐分档(体验/基础/专业/旗舰),按**可绑账号数**限配额
- 安全:argon2 存密码、验证码频控(冷却+每日上限)、并发在线数限制、踢下线、防越权(IDOR)

## 快速跑起来(Windows PowerShell)

```powershell
cd platform_server
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

.venv\Scripts\python init_db.py       # 建表 + 初始化套餐
.venv\Scripts\python smoke_test.py    # 端到端自测(可选)

.venv\Scripts\python -m uvicorn app.main:app --port 8800 --reload
# 打开 http://127.0.0.1:8800/docs 交互调试
```

> Redis 可选:装了就用(本机 `..\redis-portable\redis-server.exe`),没起就自动退内存版(仅开发)。

## 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/sms/send` | 发验证码(开发模式回显 `dev_code`) |
| POST | `/auth/register` | 手机号+验证码+密码 注册,送体验会员 |
| POST | `/auth/login/password` | 账号+密码 登录 |
| POST | `/auth/login/sms` | 手机号+验证码 登录 |
| POST | `/auth/refresh` | 轮换 refresh、换新 access |
| POST | `/auth/logout` | 吊销当前会话 |
| GET  | `/auth/me` | 当前用户 + 会员/配额 |
| POST | `/accounts/bind` | 绑社媒号(演示配额闸) |
| GET  | `/accounts` | 我的社媒号列表 |
| DELETE | `/accounts/{id}` | 解绑 |
| GET  | `/billing/plans` | 套餐列表 |
| POST | `/billing/subscribe` | 开通/续费会员(mock 支付,同套餐续期叠加) |

## 已内置的安全加固(过了一轮多视角对抗审查)

- **生产启动守卫**:生产形态(非 sqlite 库 / aliyun 短信)下若密钥仍是默认/过短、还开着 console 短信或验证码回显,**直接拒绝启动**
- **OTP 防爆破**:验证码连错 N 次(默认 5)即作废;`dev_code` 仅"非生产 + console 短信"才回显
- **令牌**:refresh 原子轮换 + 重放检测(复用旧 refresh → 下线该用户全部会话);access 的 `jti` 与 `sub` 强绑定
- **限流**:密码登录按 账号+IP 失败计数锁定;发码按 手机号+IP 双维度限
- **配额/会话**:绑号配额、同时在线数用**用户行锁**串行化(防并发 TOCTOU 击穿付费档)
- **越权/输入**:资源按 `user_id` 隔离、解绑校验归属;禁纯数字用户名(防和手机号混淆致 500);社媒号 `(user,平台,名)` 唯一
- **审计**:注册/登录成功失败/refresh 重放/绑号写 `audit_logs`

## 上线前仍要补

1. `MC_DATABASE_URL` 换 PostgreSQL(`pip install asyncpg`);`MC_REDIS_URL` 指真 Redis(限流/验证码才持久)
2. 填 `app/sms.py` 里 `AliyunSms` 的 TODO 接真短信;`/auth/sms/send` 前面再加图形/滑块验证码
3. 全站 HTTPS;可再挂一层全局限流中间件(如 slowapi)做纵深
4. 接支付(微信/支付宝 Native 扫码)→ 回调里写 `Subscription` 开会员
5. 存社媒号 cookie/凭据时做信封加密(KMS),别明文落库
6. ⚠️ 纯云发布要给**每个社媒号绑独立住宅/4G 代理 IP**,别用服务器 IP 群发(见架构讨论)

> 密钥/回显/短信这几项即使忘了配,生产启动守卫也会拦下——但库/Redis/支付/代理仍需自己接。

## 结构

```
platform_server/
├─ app/
│  ├─ config.py      配置(env 覆盖)
│  ├─ db.py          异步 SQLAlchemy 引擎/会话
│  ├─ models.py      User/Plan/Subscription/SocialAccount/AuthSession/AuditLog
│  ├─ security.py    密码哈希 + JWT + refresh
│  ├─ store.py       Redis 或 内存(验证码/频控)
│  ├─ sms.py         短信发送(Console/Aliyun)+ 频控
│  ├─ schemas.py     请求/响应模型
│  ├─ deps.py        鉴身份 + 鉴会员 依赖
│  └─ routers/       auth.py / account.py / billing.py
├─ init_db.py        建表 + 套餐
└─ smoke_test.py     端到端冒烟(11 步)
```
