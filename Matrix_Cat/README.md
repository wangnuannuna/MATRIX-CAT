<p align="center">
  <img src="frontend/assets/logo.png" alt="矩阵猫 Matrix_Cat" width="180">
</p>

# Matrix_Cat

多平台爬虫 / 自动发布(Python)。协议直连(curl_cffi + 自算签名),登录用 CloakBrowser 指纹浏览器。

**跑起来:** 双击 `启动.bat`(或 `python run.py`)→ 起 Web 总控台 http://127.0.0.1:8799。
命令行:`python run.py xhs login <账号>` / `python run.py xhs publish <账号> --images a.jpg`。

## 结构(前端 / 后端 / 采集 / 存储 + 最外层 runner)

```
Matrix_Cat/
├─ run.py            运行程序(最外层)—— 起网页总控台,或转发到某平台命令行;自动找带 cloakbrowser 的 python
├─ 启动.bat          双击入口 → run.py
├─ frontend/         前端 —— index.html(网页总控台 UI)
├─ backend/          后端 —— server.py(aiohttp;平台适配器注册表 + /api/login|publish|accounts)
├─ collectors/       采集 —— 每平台一个 <平台>_publisher/(登录+发布)
│   ├─ xhs_publisher/    小红书(已落地)
│   └─ _template/        新平台起手模板(复制改)
├─ storage/          存储 —— session_store(通用 AccountStore)+ data/(账号会话 / 上传)
├─ shared/           公共 —— http(curl_cffi 会话+代理路由)/ signing(签名原语)/ io_utf8 / paths
└─ config/           全局配置 —— settings.py(UA / CloakBrowser / 代理 / 超时,集中一处)
```

依赖:`curl_cffi`、`cryptography`、`cloakbrowser`、`aiohttp`(本机 = HuiMei\conda313)。

## 加一个新平台

```bat
xcopy /E /I collectors\_template collectors\douyin_publisher
cd collectors\douyin_publisher & ren pkg douyin
```
填 `douyin/config.py`(域名/接口/COOKIE_DOMAIN),补 `login/signer/uploader/content/publish` 的 TODO,
在 `backend/server.py` 的 `_ADAPTERS` 加一条适配器即可接进网页总控台。细节见 `collectors/_template/README.md`。

> 老的 C++ 桌面版 + 抖音逆向资产已全部移除(git 历史可恢复:`git log --diff-filter=D --name-only`)。
