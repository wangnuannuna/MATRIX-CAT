# -*- coding: utf-8 -*-
"""快手登录 + 发布(CloakBrowser 指纹浏览器 + 协议直连)。

任务文件三个：login / publish_image / publish_video。
共享两个核：note(发布体) / protocol(签名+上传+接口+身份)。
浏览器会话用 shared.browser，账号存储用 storage.session_store。
结构与 collectors/xhs_publisher/xhs/ 一致。
"""
__all__ = ['config', 'note', 'protocol', 'login', 'publish_image', 'publish_video']
