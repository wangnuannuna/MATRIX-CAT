# -*- coding: utf-8 -*-
r"""
Matrix_Cat 整体启动入口(最外层运行程序)。默认起「总控测试台」网页；也能直接转到某平台的命令行。

  python run.py                     # 起网页总控台(默认) → http://127.0.0.1:8799  (= backend/server.py)
  python run.py web                 # 同上
  python run.py xhs login  myacc    # 直接跑小红书命令行(= collectors/xhs_publisher/run.py login)
  python run.py xhs publish myacc --images a.jpg --visibility self_only
  python run.py xhs accounts

自己会找对解释器：当前 Python 若没装 cloakbrowser(比如双击时用的是系统 Python)，
会自动切到装了它的那个(本机 = HuiMei\conda313)再重跑，不用手动激活环境。
"""
import importlib.util
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # 项目根 Matrix_Cat/

# 找不到 cloakbrowser 时的候选解释器(可用环境变量 MATRIXCAT_PY 覆盖)
_KNOWN_PY = [
    os.environ.get('MATRIXCAT_PY'),
    r'E:\Wangnuannuan\王丛宇\Wangnunannuan\HuiMei\conda313\python.exe',
]


def _py_has_cloak(py: str) -> bool:
    try:
        return subprocess.run([py, '-c', 'import cloakbrowser'],
                              capture_output=True).returncode == 0
    except Exception:
        return False


def _find_cloak_python():
    """返回一个装了 cloakbrowser 的 python 路径；当前这个就有就直接用它。"""
    if importlib.util.find_spec('cloakbrowser') is not None:
        return sys.executable
    for c in _KNOWN_PY:
        if c and os.path.exists(c) and _py_has_cloak(c):
            return c
    return None


def _usage():
    print(__doc__.strip())


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] in ('-h', '--help', 'help'):
        _usage()
        return 0

    py = _find_cloak_python()
    if py is None:
        print('!! 找不到装了 cloakbrowser 的 Python。')
        print('   请用装了它的解释器跑(本机 = E:\\Wangnuannuan\\王丛宇\\Wangnunannuan\\HuiMei\\conda313\\python.exe)，')
        print('   或设环境变量 MATRIXCAT_PY 指向它，或 pip install -r collectors\\xhs_publisher\\requirements.txt')
        return 2
    # 当前解释器没 cloakbrowser → 换成对的那个重跑一遍(Python 处理中文路径没有编码坑)
    if os.path.abspath(py) != os.path.abspath(sys.executable):
        print(f'-> 切换到解释器: {py}')
        return subprocess.call([py, os.path.abspath(__file__)] + args)

    # 走到这：当前解释器就装了 cloakbrowser
    if not args or args[0] in ('web', 'console', 'webconsole'):
        target = os.path.join(HERE, 'backend', 'server.py')
        return subprocess.call([sys.executable, target])

    plat = args[0]
    run_py = os.path.join(HERE, 'collectors', f'{plat}_publisher', 'run.py')
    if os.path.exists(run_py):
        return subprocess.call([sys.executable, run_py] + args[1:])

    print(f'未知子命令/平台: {plat}\n')
    _usage()
    return 2


if __name__ == '__main__':
    sys.exit(main())
