# -*- coding: utf-8 -*-
"""Dramatiq broker(Redis)。发布任务走这条队列;worker 消费它执行真发布。

Redis 地址:MATRIXCAT_REDIS_URL(默认 127.0.0.1:6379)。
本机跑便携版 Redis:双击 redis-portable/redis-server.exe 或 scripts 里的启动器。
"""
import os

import dramatiq
import redis
from dramatiq.brokers.redis import RedisBroker

REDIS_URL = os.getenv("MATRIXCAT_REDIS_URL", "redis://127.0.0.1:6379/0")

# 便携版 Redis 是 5.0,不认 RESP3 的 HELLO;redis-py 8.x 默认 RESP3,故显式用 protocol=2(RESP2)。
_client = redis.from_url(REDIS_URL, protocol=2)
broker = RedisBroker(client=_client)
dramatiq.set_broker(broker)
