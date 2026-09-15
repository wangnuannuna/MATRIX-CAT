# _template —— 新平台起手模板

复制这个目录做一个新平台。跟 `../xhs_publisher` 一个结构,只是把公共件换成了 `../common`。

## 怎么用

```bat
:: 在 crawler_spider/ 下
xcopy /E /I _template douyin_publisher
cd douyin_publisher
ren pkg douyin
```

然后：

1. `douyin/config.py`：`PLATFORM='douyin'`、`COOKIE_DOMAIN='douyin.com'`、填 `API_HOST` 和各 `PATH_*`。
2. `run.py`：把 `from pkg import ...` 改成 `from douyin import ...`。
3. 补 `TODO`（都是这几处，其余框架件已就绪）：
   - `login.py` —— CloakBrowser 登录采 `storage_state`（照 xhs_publisher 搬）
   - `signer.py` —— 用 `common.signing` 原语拼签名（抖音类）/ 页内加签（小红书类）
   - `uploader.py` —— `common.http` 会话直传素材
   - `content.py` / `publish.py` —— 拼发布体 + 编排提交
4. `python douyin\run.py login <account_id>` → 扫码 → `python douyin\run.py publish <id> --media x.mp4`

## 已经给好的（不用自己写）

- `config.py` 用 `common.paths.DataLayout` 起好 `data/` 布局 + `common.http` 的代理路由。
- `accounts.py` 直接用 `common.session_store.AccountStore`（落盘/去重/风控冷却都有）。
- `run.py` 把 `common` 和平台包都挂上 `sys.path`，utf-8 和日志也引导好了。

## 目录

```
<platform>_publisher/
├─ run.py            入口(login / publish)
├─ requirements.txt
├─ tests/
└─ pkg/  → 改名 douyin/ …
   ├─ config.py      常量集中(已接 common)
   ├─ accounts.py    账号存储(已接 common)
   ├─ login.py       TODO 登录
   ├─ signer.py      TODO 签名
   ├─ uploader.py    TODO 传素材
   ├─ content.py     TODO 发布体
   └─ publish.py     TODO 发布编排
```
