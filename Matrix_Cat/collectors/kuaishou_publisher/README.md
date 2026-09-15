# kuaishou_publisher —— 快手登录 + 发布

快手创作者平台(cp.kuaishou.com)自动发布器。**协议直连**为主，反检测底座用 **CloakBrowser** 指纹浏览器，
与 `../xhs_publisher` 同一套框架、同一套账号存储/浏览器会话/代理策略。

## 一句话架构

```
扫码登录(CloakBrowser 弹窗，快手主 App 扫) ──▶ 存 storage_state(api_st/api_ph/userId) 到 accounts.json
                                                    │
发布任务(标题/正文/图或视频/话题)                    ▼
  视频: ①pre 取 upload_token  ②分片传视频字节  ③finish 拿 fileId/coverKey  ④submit 提交
        ├── cp/rest 业务接口(pre/finish/submit): 在账号自己的 CloakBrowser 发布页里 fetch(credentials:include)
        │   直发，让站点自己的请求拦截器把 __NS_sig3 追加到 URL —— 跟 xhs 页内 _webmsxyw 同思路，不是 RPA。
        └── 素材字节: curl_cffi 直连 upload.kuaishouzt.com(只认 upload_token，不需要 sig3，带 Chrome TLS 指纹)
                                                    │
                                          返回 work_id → short-video/{id} 链接
```

**为什么这么分工**：cp.kuaishou.com 的 REST 接口是 **cookie + `__NS_sig3` 双重鉴权**（已确认 `photo/list`、
`upload/pre` 的 URL 上带 sig3）。sig3 由前端 webpack 模块的 `$encode` 异步生成、疑似 white-box，纯 JS 脱环境
复现脆弱且随版本易碎；所以**让账号自己的真实页面把签名加上**最稳（和小红书页内签名机一个道理）。素材字节走
独立上传网关，那层不需要 sig3、协议与快手开放平台一致、相对稳定，交给 curl 直传快且省。

## 目录

```
kuaishou_publisher/
├── run.py                  # 命令行入口(login / publish / refresh / accounts)
├── requirements.txt
├── kuaishou/
│   ├── config.py           # 域名/接口路径/cookie 口径/本地目录(全局项从项目根 config re-export)
│   ├── note.py             # 发布体(caption/photoStatus/图片key)；纯函数可单测
│   ├── protocol.py         # 账号身份 + 页内加签执行器 + 上传网关 + pre/finish/submit 编排
│   ├── login.py            # 扫码登录(等 api_st cookie 落地 → account/current 取昵称/uid)
│   ├── publish_video.py    # 发视频(薄编排)
│   └── publish_image.py    # 发图文/图集(薄编排；atlas 协议未校准前 fail-loud 拒发)
├── tests/
│   └── test_offline.py     # 离线单测(纯逻辑，零副作用)
└── data/                   # 运行时数据(accounts.json / profiles/ / qr/)
```

## 快速开始

```powershell
# 依赖(用装了 cloakbrowser 的解释器,本机 = E:\...\HuiMei\conda313\python.exe;
# anaconda base 没装 cloakbrowser,只够跑下面惰性依赖的离线自测)
pip install -r requirements.txt

# 0) 先跑离线自测(不登录/不联网,纯逻辑,anaconda base 也能跑)
python tests/test_offline.py

# 1) 扫码登录(弹真窗口，用【快手主 App】扫；极速版/小店不一定认)
python run.py login myacc

# 2) 发视频(测试期先 --headed 盯着，可见性先留 public)
python run.py publish myacc --video v.mp4 --title 标题 --desc 正文 --topics 探店 美食

# 3) 发图文(atlas 协议未真机校准前会 fail-loud 拒发，见下方清单)
python run.py publish myacc --images a.jpg b.jpg --title 标题 --desc 正文

# 账号列表 / 校验登录态
python run.py accounts
python run.py refresh myacc
```

## 协议置信度(照抄自调研，别当都验证过了)

| 环节 | 端点/字段 | 置信度 |
|---|---|---|
| 登录成功信号 | `.kuaishou.com` 下 `kuaishou.web.cp.api_st` cookie 落地 | ✅ 确认 |
| 取用户信息 | `POST /rest/pc/authority/account/current` → `data.userId/userName/userAvatar` | ✅ 确认 |
| 视频取 token | `POST /rest/cp/works/v2/video/pc/upload/pre` → `token` | ✅ 确认 |
| 素材分片上传 | `upload.kuaishouzt.com` 的 `/api/upload/{resume,fragment,complete}` + `upload_token` | ✅ 确认 |
| 视频 finish | `POST /rest/cp/works/v2/video/pc/upload/finish` → `fileId/coverKey/mediaId` | ✅ 确认 |
| 视频 submit | `POST /rest/cp/works/v2/video/pc/submit`，字段 `caption/fileId/coverKey/photoStatus…` | ✅ 字段名确认 / ⚠️ 取值待校准 |
| 签名 | cp/rest 需 `__NS_sig3`(URL query)，页内拦截器加签 | ✅ 机制确认 / ⚠️ 裸 fetch 是否被加签待校准 |
| 图文/图集 | `atlas/pc/{pre,finish,submit}` 路径、图片 key 字段 | ❌ 全推断，门控关闭默认拒发 |

## ⚠️ 必须真机抓包校准清单(fail-loud，禁止拿推断值静默上线)

代码里这些点都做了显式门控/诊断——拿不到真实值就**停下报错**，不会用占位值假装成功：

| # | 校准点 | 现在的行为 | 校准后怎么放行 |
|---|---|---|---|
| 1 | **页内裸 `fetch` 是否被站点拦截器加 `__NS_sig3`**（还是只 hook 了 axios） | pre/finish/submit 第一次调用即试金石，疑似缺签抛 `SigningNotReady` 清晰诊断 | 若裸 fetch 不被加签，实现 `protocol.SignedApiExecutor.sign_via_webpack`(捞 webpack `$encode` 主动出签) |
| 2 | **submit 可见性 `photoStatus` 枚举**(公开/私密/好友确切值) | 公开用最佳猜测(默认 1)放行；私密/好友未校准直接抛 `VisibilityCalibrationError` | `KS_VISIBILITY_CALIBRATED=1`，必要时 `KS_VIS_PUBLIC/PRIVATE/FRIENDS=<真值>` |
| 3 | **submit 其余字段取值**(coverType/photoType/domain 默认) | 给 authentic-shaped 占位，可 env 覆盖 | `KS_COVER_TYPE / KS_PHOTO_TYPE / KS_DOMAIN` |
| 4 | **图文 atlas 版 pre/finish/submit 路径 + 图片 key 字段名** | `config.atlas_calibrated()=False` → publish_image **fail-loud 拒发** | `KS_ATLAS_CALIBRATED=1`，必要时 `KS_ATLAS_PRE/FINISH/SUBMIT=<真路径>` |
| 5 | **自定义封面上传**(`cover/upload` 的 multipart 字段) | `--cover` 暂被忽略，退用视频自动封面 coverKey(打告警，不假装用了) | 校准 multipart 字段后在 publish_video 里启用 |
| 6 | **fragment 是否需 `Content-Range`、精确格式** | 现按 fragment_id 分片、不带 Content-Range | 真机确认需要就补上 |

## 常用环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `KUAISHOU_PROXY` | 空(走全局北京出口) | 代理 URL，建议每号固定一个北京住宅出口 |
| `CRAWLER_HEADLESS` | 按场景 | `1`/`0` 强制无头/有头(登录默认有头、发布默认无头) |
| `KS_VISIBILITY_CALIBRATED` | 空 | 真机确认 photoStatus 枚举后设 `1` 才放行 private/friends |
| `KS_ATLAS_CALIBRATED` | 空 | 真机确认 atlas 图文链路后设 `1` 才放行图文发布 |
| `KUAISHOU_DATA_DIR` | `./data` | accounts.json / profiles / qr 的落地目录 |

## 诚实边界

- **登录**：思路与 xhs 一致(等登录 cookie 落地)，端点已确认；但快手 2021-2023 逆向的接口改版频繁，
  首次真机登录需确认 `api_st` 照常落 `.kuaishou.com`、`account/current` 照常 `result==1`。
- **视频**：整条 pre→传→finish→submit 端点已确认，但**端到端真机实发未验证**；submit 取值(可见性等)待校准。
- **图文**：atlas 协议开源无确证，**默认 fail-loud 拒发**。真机抓包补齐后翻 `KS_ATLAS_CALIBRATED` 开关即用
  (框架已就位，publish_image 的逐图上传+submit 编排按视频同构写好了)。
- **降权/限流**：发成功 ≠ 有曝光；同 IP 高频易触发验证码软封，按号绑 IP、控频。
```
