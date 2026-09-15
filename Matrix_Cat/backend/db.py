# -*- coding: utf-8 -*-
"""数据库:引擎 + 会话 + Base。SQLAlchemy 2.0,SQLite/Postgres 通用。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
# SQLite 多进程(API + worker + scheduler 同时读写):timeout=30 给 busy 等待,避免 "database is locked"。
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {},
    pool_pre_ping=True,          # 连接健壮性:Postgres 断线自动重连
    future=True,
)
if _is_sqlite:
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sqlite_wal(dbapi_conn, _rec):     # WAL:读写并发更顺,减少锁冲突
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from . import models  # noqa: F401  先 import 让模型注册到 Base
    Base.metadata.create_all(engine)
    _migrate()


def _migrate() -> None:
    # create_all 不会给已存在的表加新列。老库按需补列(SQLite ADD COLUMN 是安全的)。
    if not _is_sqlite:
        return
    add_cols = {
        "accounts": {
            "owner_id": "INTEGER",                       # SaaS 账号归属
            "proxy": "VARCHAR(256)",                     # 绑定的固定独立代理
            "status": "VARCHAR(16) DEFAULT 'active'",    # active/disabled
            "weight_manual": "INTEGER",                  # 养号权重人工覆盖(0-100),空=自动算
        },
        "tasks": {
            "options": "TEXT DEFAULT '{}'",              # 平台专属参数(B站 tid/source 等),JSON 存 TEXT
        },
    }
    with engine.begin() as conn:
        for table, cols_ddl in add_cols.items():
            cols = [row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")]
            if not cols:
                continue                             # 表还没建(全新库),交给 create_all
            for name, ddl in cols_ddl.items():
                if name not in cols:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
