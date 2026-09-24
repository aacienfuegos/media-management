import json
import logging
import sys
from typing import Any

import aiosqlite

from media_management.db import now_iso

AUDIT = logging.getLogger("media_management.audit")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            out.update(extra)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


def setup_logging(process: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    logging.getLogger("media_management").info("arranque", extra={"fields": {"process": process}})


def log_event(action: str, result: str, **fields: Any) -> None:
    AUDIT.info(action, extra={"fields": {"action": action, "result": result, **fields}})


async def audit(conn: aiosqlite.Connection, actor: str, action: str, result: str,
                target: str | None = None, ip: str | None = None, **detail: Any) -> None:
    """Registro de un cambio de estado en `main.db` y en el log estructurado.

    Nunca recibe secretos: los tokens llegan ya como referencia truncada de su hash."""
    await conn.execute(
        "INSERT INTO audit (ts, actor, action, target, result, ip, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (now_iso(), actor, action, target, result, ip,
         json.dumps(detail, ensure_ascii=False, default=str) if detail else None))
    log_event(action, result, actor=actor, target=target, ip=ip, **detail)


async def public_event(conn: aiosqlite.Connection, action: str, result: str,
                       link_id: int | None = None, ref: str | None = None,
                       ip: str | None = None, **detail: Any) -> None:
    await conn.execute(
        "INSERT INTO events (ts, action, result, link_id, ref, ip, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (now_iso(), action, result, link_id, ref, ip,
         json.dumps(detail, ensure_ascii=False, default=str) if detail else None))
    log_event(action, result, link_id=link_id, ref=ref, ip=ip, **detail)
