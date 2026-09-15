# -*- coding: utf-8 -*-
"""起后端:  python -m backend   (默认 http://127.0.0.1:8799)

环境变量:MATRIXCAT_PORT 换端口;MATRIXCAT_RELOAD=1 开热重载;
生产切库 MATRIXCAT_DATABASE_URL=postgresql+psycopg2://...
"""
import logging
import os

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("MATRIXCAT_PORT", "8799"))
    # 干净日志:只留我们自己打的关键事件(启动横幅 / 登录 / 发布),时间戳 + 内容。
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("uvicorn.access").disabled = True     # 关掉每条请求的 access log
    for _noisy in ("httpx", "httpcore", "urllib3"):          # AI/网络库逐条请求 INFO 也压掉
        logging.getLogger(_noisy).setLevel(logging.WARNING)
    # access_log=False + log_level=warning:不再打逐条请求、也不打 uvicorn 自己的 INFO 启动横幅。
    uvicorn.run("backend.app:app", host="127.0.0.1", port=port,
                reload=bool(os.getenv("MATRIXCAT_RELOAD")),
                access_log=False, log_level="warning")
