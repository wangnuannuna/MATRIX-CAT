# -*- coding: utf-8 -*-
"""NodeSigner:拉起常驻 sign_worker.js 子进程,毫秒级出 X-s/X-t —— 纯协议签名,不开浏览器。

- 单例 + 线程锁串行(一条 stdin/stdout 复用,请求-响应必须串行)。
- 崩溃自愈:子进程死了下次调用自动重启再试一次。
- Windows 下走 subprocess 拉 node(与项目既有做法一致,避 worker 线程起进程的 EBADF)。
- 签名算法是小红书自己的 JS(sign/bundle/xhs_webmsxyw.js),换算法只需重 dump 那个 bundle,Python 侧不动。

用法:
    from xhs.signer import get_signer
    r = get_signer().sign("/api/sns/web/v1/feed", {"source_note_id": "..."}, a1="...")
    # r = {"xs": "XYW_...", "xt": 1784..., "xsc": ""}
"""
import json
import os
import subprocess
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKER = os.path.normpath(os.path.join(_HERE, "..", "sign", "sign_worker.js"))


def _node_bin() -> str:
    return os.getenv("MATRIXCAT_NODE_BIN") or os.getenv("NODE_BIN") or "node"


class SignerError(RuntimeError):
    pass


class NodeSigner:
    def __init__(self, worker_path: str = None):
        self.worker = os.path.abspath(worker_path or _WORKER)
        self._proc = None
        self._lock = threading.Lock()

    def _ensure(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        if not os.path.exists(self.worker):
            raise SignerError(f"签名 worker 不存在: {self.worker}")
        self._proc = subprocess.Popen(
            [_node_bin(), self.worker],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(self.worker), text=True, encoding="utf-8", bufsize=1)
        # 读握手行 {"ready":true}(容错:最多跳过 3 行噪音)
        for _ in range(3):
            line = self._proc.stdout.readline()
            if not line:
                raise SignerError("签名 worker 启动即退出(检查 node / bundle)")
            if '"ready"' in line:
                return
        raise SignerError("签名 worker 未发出 ready 握手")

    def sign(self, path: str, data, a1: str, b1: str = None, ua: str = None,
             xt: int = None, common: bool = True) -> dict:
        """返回 {xs, xt, xsc, body}。data 传 dict 或 None(GET 无体);b1 传本号真实指纹(参与 X-S-Common);
        ua 传请求将用的 UA(X-S-Common 会编码它,须与 curl 请求头 UA 一致);xt 钉死时间戳;
        common=False 只出 xs/xt(读接口不需 X-S-Common)。失败抛 SignerError。"""
        req = {"id": 1, "path": path, "data": data, "a1": a1, "common": bool(common)}
        if b1:
            req["b1"] = b1
        if ua:
            req["ua"] = ua
        if xt is not None:
            req["xt"] = int(xt)
        payload = json.dumps(req, ensure_ascii=False) + "\n"
        with self._lock:
            last = None
            for attempt in range(2):                 # 崩溃自愈:重启再试一次
                try:
                    self._ensure()
                    self._proc.stdin.write(payload)
                    self._proc.stdin.flush()
                    line = self._proc.stdout.readline()
                    if not line:
                        raise SignerError("签名 worker 无输出(子进程可能已退出)")
                    r = json.loads(line)
                    if not r.get("ok"):
                        raise SignerError(f"出签失败: {r.get('err')}")
                    return {"xs": r.get("xs") or "", "xt": r.get("xt"),
                            "xsc": r.get("xsc") or "", "body": r.get("usedBodyStr") or ""}
                except (BrokenPipeError, OSError, ValueError, SignerError) as e:
                    last = e
                    self._kill()
            raise SignerError(f"NodeSigner 出签失败: {last}")

    def gen_a1(self) -> str:
        """本地生成一个全新 a1(蚁小二云端下发的那个,getA1 纯算法)。失败抛 SignerError。"""
        payload = json.dumps({"id": 1, "op": "gena1"}) + "\n"
        with self._lock:
            last = None
            for attempt in range(2):
                try:
                    self._ensure()
                    self._proc.stdin.write(payload)
                    self._proc.stdin.flush()
                    line = self._proc.stdout.readline()
                    if not line:
                        raise SignerError("gen_a1 无输出")
                    r = json.loads(line)
                    if not r.get("ok"):
                        raise SignerError(f"gen_a1 失败: {r.get('err')}")
                    return r.get("a1") or ""
                except (BrokenPipeError, OSError, ValueError, SignerError) as e:
                    last = e
                    self._kill()
            raise SignerError(f"gen_a1 失败: {last}")

    def _kill(self) -> None:
        try:
            if self._proc:
                self._proc.kill()
        except Exception:  # noqa: BLE001
            pass
        self._proc = None


_singleton = None
_singleton_lock = threading.Lock()


def get_signer() -> NodeSigner:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = NodeSigner()
    return _singleton
