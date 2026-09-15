# -*- coding: utf-8 -*-
"""平台包。复制模板后把这个目录 pkg/ 改名成平台名(如 douyin/)，run.py 里的 import 同步改。

结构(任务文件 + 2 个共享核，跟 xhs 一致)：
  config.py         平台域名/接口路径/本地目录(全局项从项目根 config re-export)
  note.py           发布体：把标题/正文/素材id 拼成平台 create 接口的 body
  protocol.py       协议层：账号身份 + 签名 + 素材上传 + 提交接口 + 发布编排积木
  login.py          登录：起 CloakBrowser 拿 storage_state 存库
  publish_image.py  发图文(薄，调 protocol 的积木)
  publish_video.py  发视频(薄)
已落地参考：collectors/xhs_publisher/xhs/
"""
