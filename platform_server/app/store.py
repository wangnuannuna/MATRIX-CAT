import time

import redis.asyncio as aioredis


class BaseStore:
    async def get(self, key): ...
    async def setex(self, key, ttl, val): ...
    async def delete(self, key): ...
    async def incr_ttl(self, key, ttl): ...  # +1,首次设置过期,返回当前计数


class RedisStore(BaseStore):
    def __init__(self, client):
        self.r = client

    async def get(self, key):
        v = await self.r.get(key)
        return v.decode() if isinstance(v, (bytes, bytearray)) else v

    async def setex(self, key, ttl, val):
        await self.r.setex(key, ttl, str(val))

    async def delete(self, key):
        await self.r.delete(key)

    async def incr_ttl(self, key, ttl):
        n = await self.r.incr(key)
        if n == 1:
            await self.r.expire(key, ttl)
        return n


class MemoryStore(BaseStore):
    """redis 连不上时的兜底,仅开发用。进程重启即丢。"""

    def __init__(self):
        self._d: dict[str, tuple[str, float]] = {}

    def _live(self, key):
        item = self._d.get(key)
        if not item:
            return None
        val, exp = item
        if exp and exp < time.time():
            self._d.pop(key, None)
            return None
        return val

    async def get(self, key):
        return self._live(key)

    async def setex(self, key, ttl, val):
        self._d[key] = (str(val), time.time() + ttl)

    async def delete(self, key):
        self._d.pop(key, None)

    async def incr_ttl(self, key, ttl):
        cur = self._live(key)
        n = (int(cur) if cur else 0) + 1
        exp = self._d[key][1] if (key in self._d and cur) else time.time() + ttl
        self._d[key] = (str(n), exp)
        return n


async def make_store(redis_url: str) -> BaseStore:
    try:
        client = aioredis.from_url(redis_url)
        await client.ping()
        return RedisStore(client)
    except Exception:
        # redis 起不来就退内存版,保证骨架能跑
        return MemoryStore()
