# -*- coding: utf-8 -*-
"""data/ 目录约定。每个平台自己一份 data/,里面 accounts.json + profiles/ + sessions/ + qr/ + output/。

平台 config.py 里一把梭:
    from shared.paths import DataLayout
    LAYOUT = DataLayout(BASE_DIR)          # BASE_DIR = <platform>_publisher/
    ACCOUNTS_FILE = str(LAYOUT.accounts_file)
    PROFILES_DIR  = str(LAYOUT.profiles_dir)
"""
import os
from pathlib import Path


class DataLayout:
    """<platform>_publisher/data/ 下的标准布局。子目录第一次取用时自动建。"""

    def __init__(self, platform_root, data_dir=None, env_override=""):
        # 允许用环境变量把整个 data 根挪走(比如指到共享盘),默认就在平台目录下的 data/
        root = (os.environ.get(env_override) if env_override else None) or data_dir
        self.root = Path(root) if root else Path(platform_root, "data")

    def _sub(self, name):
        d = self.root / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def accounts_file(self):
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root / "accounts.json"

    @property
    def profiles_dir(self):
        # 一号一份 CloakBrowser profile(cookie/缓存/localStorage 落盘),做到一号一设备
        return self._sub("profiles")

    @property
    def sessions_dir(self):
        return self._sub("sessions")

    @property
    def qr_dir(self):
        return self._sub("qr")

    @property
    def output_dir(self):
        return self._sub("output")
