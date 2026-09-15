# -*- coding: utf-8 -*-
"""B站(bilibili)登录 + 发布模块。

登录走 CloakBrowser 扫码(拿 storage_state);发布全程 curl_cffi 直连(cookie+csrf),不开浏览器。
子模块:config(常量)/ signer(wbi 自签)/ protocol(直连客户端+编排)/ note(发布体)/
        login(扫码)/ publish_video / publish_image。
"""
