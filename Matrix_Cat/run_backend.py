# -*- coding: utf-8 -*-
"""一键起后端全家桶:Redis + 发布 worker + 定时调度 + FastAPI API。

    python run_backend.py

会自动找装了依赖的 python(本机 conda313);Redis 用仓库外的便携版(redis-portable/)。
worker / scheduler 各开一个新窗口看日志;API 在当前窗口(Ctrl+C 退出,顺带收掉子进程)。
"""
import ctypes
import importlib.util
import os
import socket
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent                       # 项目根
REDIS_EXE = HERE.parent / "redis-portable" / "redis-server.exe"

# 找不到 dramatiq 时的候选解释器(环境变量 MATRIXCAT_PY 可覆盖)
_KNOWN_PY = [os.environ.get("MATRIXCAT_PY"),
             r"E:\Wangnuannuan\王丛宇\Wangnunannuan\HuiMei\conda313\python.exe"]


def _has_deps(py: str) -> bool:
    try:
        return subprocess.run([py, "-c", "import dramatiq, fastapi, sqlalchemy"],
                              capture_output=True).returncode == 0
    except Exception:
        return False


def _find_py() -> str | None:
    if importlib.util.find_spec("dramatiq") and importlib.util.find_spec("fastapi"):
        return sys.executable
    for c in _KNOWN_PY:
        if c and os.path.exists(c) and _has_deps(c):
            return c
    return None


def _port_up(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _spawn(args, cwd=None):
    flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    return subprocess.Popen(args, cwd=cwd, creationflags=flags)


def _make_kill_on_close_job():
    """建一个 Windows Job Object 并设 KILL_ON_JOB_CLOSE:把 worker/scheduler/API 都塞进这个 job,
    只要本进程(run_backend)一退出(Ctrl+C / 点窗口 X / 崩溃都算),job 句柄随进程关闭,
    操作系统就自动杀光 job 里的所有成员及它们的子进程 —— 关主程序 = 连带关掉后台窗口,不留孤儿 worker。
    子进程 import 慢(几百 ms)才 fork 出 dramatiq 工作子进程,我们 Popen 后立刻 assign,稳赢这个竞态,
    之后 fork 出来的孙子进程按 Windows 默认自动并入同一 job。"""
    if os.name != "nt":
        return None
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None

        class _BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _IO(ctypes.Structure):
            _fields_ = [("ReadOperationCount", ctypes.c_uint64),
                        ("WriteOperationCount", ctypes.c_uint64),
                        ("OtherOperationCount", ctypes.c_uint64),
                        ("ReadTransferCount", ctypes.c_uint64),
                        ("WriteTransferCount", ctypes.c_uint64),
                        ("OtherTransferCount", ctypes.c_uint64)]

        class _EXT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _BASIC), ("IoInfo", _IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        info = _EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000        # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                wintypes.LPVOID, wintypes.DWORD]
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return None                                       # 9 = JobObjectExtendedLimitInformation
        return job
    except Exception:
        return None


def _assign_to_job(job, proc) -> None:
    """把一个已 spawn 的子进程塞进 job。失败静默(大不了退回旧的 terminate 兜底)。"""
    if not job or os.name != "nt" or proc is None:
        return
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        h = k32.OpenProcess(0x0100 | 0x0001, False, proc.pid)  # SET_QUOTA | TERMINATE
        if h:
            k32.AssignProcessToJobObject(job, h)
            k32.CloseHandle(h)
    except Exception:
        pass


def _load_env_file() -> None:
    """启动时把 .env 灌进本进程环境,子进程(worker/scheduler/API)一律继承。
    这样 MATRIXCAT_DRYRUN / AI key 等在每个子进程里从环境直接可读,不依赖各自的 import 顺序。"""
    envf = HERE / ".env"
    try:
        with open(envf, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k:
                    os.environ.setdefault(k, v)      # 真实环境变量优先,不覆盖
    except OSError:
        pass


def _kill_stale_workers(py: str) -> None:
    """干掉上一轮遗留的 worker/scheduler。它们连着同一个 Redis,老的可能还是 DRY_RUN(模拟)模式,
    会抢走新任务当"模拟发布"消费掉 → 前端显示成功、平台上却没作品。

    坑:dramatiq 在 Windows 靠 multiprocessing 起【工作子进程】,它的命令行是
    `... multiprocessing.spawn ... --multiprocessing-fork`,不含 backend.jobs.tasks;主进程一死,
    这个子进程会变【孤儿】继续连 Redis 抢任务(实测见过 17:24 的僵尸活到晚上)。所以除了按
    backend.jobs.tasks / backend.scheduler 杀主进程,还要揪出用【同一个解释器】起的
    --multiprocessing-fork 子进程里【父进程已死(孤儿)或父是 dramatiq 主进程(陈旧)】的那些。
    限定同一解释器,避免误伤别的用 multiprocessing 的程序。启动时全清,保证全场只剩这一个新 worker。"""
    if os.name != "nt":
        return
    query = ("Get-CimInstance Win32_Process | "
             "Select-Object ProcessId,ParentProcessId,CommandLine,ExecutablePath | "
             "ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", query],
                             capture_output=True, text=True, timeout=25).stdout
        import json
        procs = json.loads(out) if out.strip() else []
        if isinstance(procs, dict):
            procs = [procs]
    except Exception:
        return

    def _pid(p, key="ProcessId"):
        try:
            return int(p.get(key))
        except (TypeError, ValueError):
            return None

    def _is_py(p):                                     # 只认 python 进程,别误伤命令行里恰好提到这些字样的 shell/编辑器
        return os.path.basename(p.get("ExecutablePath") or "").lower() in ("python.exe", "pythonw.exe")

    alive = {_pid(p) for p in procs if _pid(p) is not None}
    mains = {_pid(p) for p in procs
             if _is_py(p) and "backend.jobs.tasks" in (p.get("CommandLine") or "")}
    self_pid = os.getpid()
    py_norm = os.path.normcase(py)
    targets = set()
    for p in procs:
        pid, cl = _pid(p), (p.get("CommandLine") or "")
        if pid is None or pid == self_pid or not _is_py(p):
            continue
        if "backend.jobs.tasks" in cl or "backend.scheduler" in cl:
            targets.add(pid)
        elif "multiprocessing-fork" in cl:
            ppid = _pid(p, "ParentProcessId")
            same_py = os.path.normcase(p.get("ExecutablePath") or "") == py_norm
            if same_py and (ppid not in alive or ppid in mains):
                targets.add(pid)                       # 孤儿 or 陈旧主进程的工作子进程
    for pid in targets:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)
        except Exception:
            pass


def main() -> int:
    py = _find_py()
    if py is None:
        print("找不到装了依赖的 python。请在 conda313 里跑,或 pip install -r backend/requirements.txt")
        return 2
    # 当前解释器没装依赖 → 换成对的那个重跑本脚本
    if os.path.abspath(py) != os.path.abspath(sys.executable):
        print(f"-> 切换解释器: {py}")
        return subprocess.call([py, os.path.abspath(__file__)] + sys.argv[1:])

    _load_env_file()          # 先把 .env 灌进环境,worker/scheduler/API 全继承(DRYRUN 等)
    port = int(os.getenv("MATRIXCAT_PORT", "8799"))
    if _port_up(port):
        print(f"[!] 端口 {port} 已被占用 —— 多半是老的 `python run.py`(旧测试台)还开着。")
        print(f"    先去那个窗口按 Ctrl+C 关掉它,再跑本脚本;")
        print(f"    或换个端口:  set MATRIXCAT_PORT=8788  然后重跑(正式台就在 http://127.0.0.1:8788/app)。")
        return 2

    # KILL_ON_JOB_CLOSE 的 job:worker/scheduler/redis/API 全塞进去,主程序一退 OS 连带杀光(见函数注释)。
    job = _make_kill_on_close_job()

    def _spawn_in_job(args, cwd=None):
        p = _spawn(args, cwd=cwd)
        _assign_to_job(job, p)                   # Popen 后立刻并入,赶在它 fork 子进程之前
        return p

    procs = []
    # 1) Redis(已在跑就不重复起)
    if _port_up(6379):
        print("[1/4] Redis 6379 已在跑,复用")
    elif REDIS_EXE.exists():
        print("[1/4] 起 Redis 6379 ...")
        procs.append(_spawn_in_job([str(REDIS_EXE), "--port", "6379", "--save", "",
                                    "--appendonly", "no", "--maxmemory", "128mb"]))
        for _ in range(20):
            if _port_up(6379):
                break
            time.sleep(0.3)
    else:
        print(f"[!] 没找到便携版 Redis: {REDIS_EXE}(worker 会连不上;先装 Redis)")

    # 2) 发布 worker  3) 定时调度
    print("[2/4] 清理上一轮遗留 worker/scheduler(含 --multiprocessing-fork 孤儿)→ 起发布 worker(新窗口)...")
    _kill_stale_workers(py)                       # 先杀旧的:避免旧 DRY_RUN worker/僵尸子进程抢任务模拟掉
    procs.append(_spawn_in_job([py, "-m", "dramatiq", "backend.jobs.tasks", "--processes", "1", "--threads", "4"], cwd=str(HERE)))
    print("[3/4] 起定时调度(新窗口)...")
    procs.append(_spawn_in_job([py, "-m", "backend.scheduler"], cwd=str(HERE)))

    # 4) API(当前窗口,前台)。也塞进 job:关主程序时它跟着一起收。
    print(f"[4/4] 起后端 API → http://127.0.0.1:{port}/app (正式台) · / (测试台) · /docs")
    api = subprocess.Popen([py, "-m", "backend"], cwd=str(HERE))
    _assign_to_job(job, api)
    procs.append(api)
    try:
        return api.wait()
    finally:
        # job 会兜底连带杀;这里再显式收一遍(含子进程树),double-kill 无害。
        for p in procs:
            try:
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                               capture_output=True, timeout=10)
            except Exception:
                try:
                    p.terminate()
                except Exception:
                    pass
        if job:
            try:
                ctypes.WinDLL("kernel32").CloseHandle(job)   # 关 job 句柄 → KILL_ON_JOB_CLOSE 生效
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
