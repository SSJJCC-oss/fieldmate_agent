"""The audit log: a record of every tool any agent called, and what happened.

`@audited("intake")` wraps a tool function. It changes nothing about how the
tool behaves; it just writes a row to audit_log after each call. functools.wraps
keeps the original name, docstring and signature, which matters because the
LLM reads exactly those to decide when and how to call the tool.
"""
from __future__ import annotations

import functools
import json
import logging

from ..db import AuditLog, SessionLocal

logger = logging.getLogger("fieldmate.audit")


def audited(agent: str):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            result = fn(*args, **kwargs)
            logger.info("      🔧 [%s agent] %s(%s) -> %s", agent, fn.__name__, kwargs, str(result)[:160])
            with SessionLocal() as db:
                db.add(
                    AuditLog(
                        agent=agent,
                        tool=fn.__name__,
                        args_json=json.dumps(kwargs, default=str),
                        result_json=json.dumps(result, default=str)[:4000],
                    )
                )
                db.commit()
            return result

        return wrapper

    return decorator
