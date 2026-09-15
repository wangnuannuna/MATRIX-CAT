# -*- coding: utf-8 -*-
"""shared —— 跨层公共工具。

前端/后端/采集/存储各层共用的东西放这:curl_cffi 会话(http)、签名原语(signing)、
utf-8 引导(io_utf8)、data 目录约定(paths)。别在各层重复造。

注:账号/会话存储在 storage/(session_store),不在这。
"""
__version__ = "0.1.0"
