# backend —— Matrix_Cat 后端(FastAPI)

矩阵猫 SaaS 的编排后端:平台用户登录鉴权、社媒账号管理、会员计费、总后台,统一挂在
`/api/*`,并把前端几张页面 serve 出去。协议直连的真发布由采集 worker 消费队列执行(见 `jobs/`)。

> 老的 aiohttp `server.py`(crawler_spider 总控测试台)已不是现役,仅根 `run.py` 作"旧测试台"单独拉起。
> 现役是本目录的 `app.py` 这套 FastAPI。

## 跑起来

```powershell
# 用装了依赖的解释器(本机 = HuiMei\conda313)。只起 API 的话不需要 Redis。
cd E:\Wangnuannuan\王丛宇\Wangnunannuan\Matrix_Cat\Matrix_Cat
python -m backend                       # = uvicorn backend.app:app,监听 127.0.0.1:8799
# 或一键起全套(Redis + 发布 worker + 定时器 + API):
python run_backend.py
```

- 端口 `MATRIXCAT_PORT`(默认 8799),`MATRIXCAT_RELOAD=1` 开热重载。
- 首次启动自动建表(`init_db`)+ 把会员套餐 upsert 进库(`sync_plans`)。
- **没有内置默认管理员**:第一个注册的用户自动成管理员,后续由管理员在总后台授权。

## 模块

| 文件 | 干什么 |
|---|---|
| `app.py` | 装配:把 7 个 router 挂到 `/api`,serve 前端 `/ /app /account /admin`,API-Key 中间件 |
| `config.py` | 配置(env 前缀 `MATRIXCAT_`):DB、JWT、`local_console`、支付渠道、套餐 seed |
| `db.py` | 引擎/会话/Base + 轻量迁移(老库 ALTER 补 `owner_id/proxy/status`) |
| `models.py` | ORM:Account / Task / TaskLog / User / Plan / Order |
| `schemas.py` | 请求体校验(Pydantic) |
| `crud.py` | 所有 DB 读写走这;路由只调 crud |
| `auth.py` | 密码哈希(scrypt→pbkdf2 回退)+ 自签 HS256 token + `require_user/require_admin` |
| `security.py` | Fernet 加解密(账号登录态 storage_state 加密落库) |
| `payments.py` | 可插拔支付渠道:`mock`(默认)/ `alipay`(RSA2 真实签名)/ `wechat`(预留) |
| `api/*` | 路由:auth / accounts / membership / admin / platforms / tasks / publish |
| `jobs/*` | 采集队列:`broker`(Redis)`tasks`(发布 actor)`login`(扫码登录 actor)`collector`(桥采集层) |

## 接口速查(`/api` 前缀)

**登录鉴权** `auth.py`
- `POST /auth/register` `POST /auth/login` → 返回自签 JWT
- `GET/PATCH /auth/me`、`POST /auth/password`

**社媒账号** `accounts.py`(登录用户只看/管自己的号;`local_console=0` 时匿名一律 401)
- `GET /accounts?platform=` · `POST /accounts`(可带 `proxy` 绑定固定独立代理)
- `GET/PATCH/DELETE /accounts/{pk}`(归属校验:非本人非管理员一律 404)
- `POST /login`(派活给 worker 起 CloakBrowser 扫码)· `GET /login/status?pk=`(轮询二维码+结果)

**会员计费** `membership.py`
- `GET /plans` `GET /membership` `POST /membership/purchase` `POST /membership/pay`(mock)`GET /membership/orders`
- `POST /membership/notify/alipay`(支付宝异步回调,验签后激活)

**总后台** `admin.py`(整条挂 `require_admin`)
- `GET /admin/stats|users|orders|plans` · `PATCH /admin/users/{uid}` · `POST /admin/users/{uid}/grant`
- `GET /admin/accounts?platform=&q=` · `PATCH/DELETE /admin/accounts/{pk}`(跨用户管平台号)

## 扫码登录怎么走

`POST /api/login` 只登记号并把作业推进 Redis 队列;采集 worker(`python -m dramatiq backend.jobs.tasks`)
起 CloakBrowser 弹二维码,进度写 Redis,前端轮询 `GET /api/login/status` 拿二维码 → 手机扫 →
登录态加密写回 **后端 DB**(DB 即单一事实源)。

- 真扫码需 worker + Redis + 真人扫码。
- 自测管线不想弹浏览器:`MATRIXCAT_LOGIN_DRYRUN=1`,走模拟登录(写占位登录态),验证"派活→轮询→写回"整条链。

## 关键配置(env,前缀 `MATRIXCAT_`)

- `DATABASE_URL`:默认 SQLite 开箱即跑;生产切 Postgres 改这一处。
- `LOCAL_CONSOLE`:默认 `1`(本机总控台匿名可全量操作);**正式 SaaS 部署务必设 `0`**,否则匿名可越权看全部号。
- `PAY_PROVIDER`:`mock`(默认)/`alipay`。切 `alipay` 还要填 `ALIPAY_APP_ID / ALIPAY_PRIVATE_KEY / ALIPAY_PUBLIC_KEY / ALIPAY_NOTIFY_URL`。
- `JWT_SECRET`:空则自动在 `storage/data/.jwt.key` 生成一把(多实例部署要统一同一把)。
- `API_KEY`:设了则所有 `/api/*`(除 health)要带 `X-API-Key`。
