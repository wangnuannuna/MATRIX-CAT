# xhs_publisher —— 小红书登录 + 发布

仿蚁小二的自动发布器（先做小红书）。**协议直连**为主，反检测底座用 **CloakBrowser** 指纹浏览器。

## 一句话架构

```
扫码登录(CloakBrowser 弹窗) ──▶ 存 storage_state(cookie/a1/b1) 到 accounts.json
                                        │
发布任务(标题/正文/图或视频/话题)        ▼
  ①校验登录  ②permit 拿 COS token   ← 在账号自己的 CloakBrowser 页面里用 window._webmsxyw
  ④create_note 提交(带 X-s/X-t/X-S-Common)   签名 + 页内 fetch 直发(原生带齐验签头/cookie/真指纹)
  ③素材字节直传腾讯云 COS            ← curl_cffi 纯协议直连(带 Chrome TLS 指纹，不走浏览器)
                                        │
                              返回 note_id → explore/{id} 链接
```

**分工**（对应蚁小二：云端签名 + 客户端直传 COS）：
- 需验签的 `permit` / `create_note`：在**账号自己的** CloakBrowser 页面里用 `_webmsxyw` 页内 `fetch` 发。
  不是 RPA（不点页面），是"协议直发 + 浏览器当签名机"。X-s/X-t/**X-S-Common** 全原生产出，签名天然同源
  （页面就是账号 a1/b1 登录的，不用注入 hack）。这是当前**唯一真机验证过能发成功**的路。
- 素材字节直传 COS：`curl_cffi` 直连，快且省，跟蚁小二"客户端直传 COS"一致。

## 为什么用 CloakBrowser

源码级 C++ 改指纹的 Chromium（canvas/WebGL/audio/fonts/GPU/screen/WebRTC/CDP 66 处 patch），
指纹是**二进制层面真的**、不是 JS 临时改的，且**每号能独立**。据此把审计出的封号根因逐条对上：

| 封号根因（审计） | 这里怎么解 |
|---|---|
| 矩阵同质化（多号同机同硬件指纹） | 每号一份稳定 `--fingerprint=<seed>` + 独立 profile 目录 → 一号一设备 |
| 地理不一致（VPN 致 IP/时区/语言错配） | 没代理时强制直连绕 VPN；配住宅代理时 `geoip=True` 让时区/语言跟出口 IP 对齐 |
| profile/IP 不按号粘 | 每号持久化 profile（cookie/缓存/localStorage 落盘），代理粘账号 |
| 签名 a1 ≠ 账号 a1 | 在账号自己的持久化 context 里签，a1 天然同源，无需注入 |
| 签名串 ≠ 发送串 | note 用同一个 `serialize()` 出串，签名与 fetch 发送同一字节 |

## 目录

```
xhs_publisher/
├── run.py                 # 命令行入口
├── requirements.txt
├── xhs/
│   ├── config.py          # 域名/路径/常量/环境变量
│   ├── note.py            # 创建笔记请求体(2026-06-22 真机逐字节校准，图文+视频)
│   ├── cos.py             # 素材直传 COS(curl_cffi) + permit 解析
│   ├── accounts.py        # accounts.json 存储 + storage_state→a1/b1 抽取 + 冷却
│   ├── browser.py         # CloakBrowser 会话(按号持久化 profile + 稳定指纹 + 代理/geoip)
│   ├── signer.py          # 页内 _webmsxyw 签名 + 页内 fetch 执行器
│   ├── login.py           # 扫码登录
│   └── publish.py         # 发布编排
├── tests/
│   └── smoke_sign.py      # 零副作用签名自检(免登录)
└── data/                  # 运行时数据(accounts.json / profiles/ / qr/)
```

## 快速开始

```powershell
# 依赖(用装了 cloakbrowser 的解释器，本机是 HuiMei\conda313)
pip install -r requirements.txt

# 0) 先跑签名自检，确认浏览器签名机链路活着(不登录/不发布)
python tests/smoke_sign.py

# 1) 扫码登录(弹出真窗口，手机 App 扫)
python run.py login myacc

# 2) 发图文(测试期先 self_only 只给自己看)
python run.py publish myacc --images a.jpg b.jpg --title 标题 --desc 正文 --topics 美食 --visibility self_only

# 3) 发视频
python run.py publish myacc --video v.mp4 --cover c.jpg --title 标题 --desc 正文 --visibility self_only

# 账号列表 / 校验登录态
python run.py accounts
python run.py refresh myacc
```

## 常用环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `XHS_PROXY` | 空(直连) | 代理 URL，建议每号固定一个北京住宅出口 |
| `XHS_HEADLESS` | 按场景 | `1`/`0` 强制无头/有头（登录默认有头、发布默认无头） |
| `CLOAKBROWSER_LICENSE_KEY` | 空 | CloakBrowser Pro key（按需） |
| `XHS_DATA_DIR` | `./data` | accounts.json / profiles / qr 的落地目录 |
| `XHS_VISIBILITY_MUTUAL_CALIBRATED` | 空 | 真机确认"仅互关"档位后设 `1` 才放行 mutual_friends |
| `XHS_NOTE_VARIANT` | `full` | note 体变体（`full` 贴真机 / `skeleton` 精简） |

## 诚实边界（还没做/还没真机验证的）

- **图文路真机已验证过能发成功**（HuiMei 里同一套 note_contract + 页内签名拿到过 post_id）；本仓是把那套
  搬成独立、换 CloakBrowser 底座。**搬完后的端到端真机实发需再验一次**（尤其 CloakBrowser 页里 `_webmsxyw`
  是否照常挂载、X-S-Common 是否照常产出）。先用 `--visibility self_only` 联调。
- **视频路**联调更弱：note 体已按真机校准，但整条端到端实发未充分验证；无 `cv2` 时视频编码元数据退占位（低危，服务端转码会重探）。
- **可见性**：public=0 / self_only=1 已真机确认；mutual_friends 未确认，默认拒发（见上表开关）。
- **降权/限流**：发成功 ≠ 有曝光。只能发布后用非作者视角核验，代码层面看不到。
- **话题 id**：结构化 hash_tag 的真实 id 需走话题联想接口换取（当前留空、靠正文 `#话题[话题]#` 内联兜底）。
```
