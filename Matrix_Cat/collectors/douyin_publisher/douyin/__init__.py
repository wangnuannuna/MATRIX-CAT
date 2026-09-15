# -*- coding: utf-8 -*-
"""抖音发布平台包(仿蚁小二协议直连)。

结论(回蚁小二 reptile 云脚本 + 三路对抗验证,2026-07-13):抖音发布走纯协议,内嵌浏览器
只负责扫码登录收 cookie。发布 create_v2 的 a_bogus **留空**即可,真正把门的是请求头
bd-ticket-guard-client-data —— 本地用 secsdk 种在 cookie 里的 EC 私钥做 ECDSA-SHA256。
所以不用逆 a_bogus,难点收敛成"登录拿全 secsdk cookie + 本地复刻 bd-ticket-guard"。

模块分工(照 collectors/xhs_publisher 的骨架):
  · config   —— 抖音专属常量(域名/接口/aid/ServiceId/cookie 名)
  · signer   —— bd-ticket-guard 客户端签名(clientSign 端口)+ 发布请求头装配
  · probe    —— secsdk cookie 完整性体检 + 离线自测(登录态够不够直发,fail-fast)
后续补:login(CloakBrowser 扫码收 cookie)/ uploader(VOD/ImageX AWS4)/ publish。
"""
