# -*- coding: utf-8 -*-
"""Windows 控制台 utf-8 引导 + 统一 logging。每个平台 run.py 开头调 setup_utf8() 就行。"""
import io
import logging
import sys


def setup_utf8():
    # Windows 控制台默认 gbk,打印中文昵称/emoji 直接 UnicodeEncodeError 崩,包一层 utf-8。
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        buf = getattr(stream, "buffer", None)
        if buf is not None:
            setattr(sys, name, io.TextIOWrapper(buf, encoding="utf-8", errors="replace"))


def setup_logging(level=logging.INFO):
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname).1s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
