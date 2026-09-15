import json

from .db import SessionLocal
from .models import AuditLog


async def audit(action: str, user_id: str | None = None, ip: str | None = None, detail=None):
    """写安全审计日志。独立事务,不受主请求回滚影响(失败事件也能留痕)。"""
    try:
        async with SessionLocal() as s:
            s.add(AuditLog(
                user_id=user_id, action=action, ip=ip,
                detail=json.dumps(detail, ensure_ascii=False) if detail is not None else None,
            ))
            await s.commit()
    except Exception:
        # 审计失败不能影响主流程
        pass
