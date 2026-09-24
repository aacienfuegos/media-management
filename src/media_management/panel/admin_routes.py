import json
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response

from media_management.db import now_iso
from media_management.logs import audit
from media_management.panel.deps import CsrfUser, MainDb, PublicDb, User, client_ip, render
from media_management.security import hash_token, new_token, token_ref

router = APIRouter()


@router.get("/tokens")
async def tokens_view(request: Request, user: User, conn: MainDb) -> Response:
    async with conn.execute("SELECT * FROM api_tokens ORDER BY revoked_at IS NOT NULL, created_at DESC") as cur:
        tokens = await cur.fetchall()
    return render(request, user, "tokens.html", tokens=tokens, new_token=None)


@router.post("/tokens")
async def create_token(request: Request, user: CsrfUser, conn: MainDb,
                       name: Annotated[str, Form(min_length=1, max_length=80)]) -> Response:
    """Token de solo lectura para integraciones. Se enseña una vez y se guarda hasheado."""
    token = new_token()
    token_hash = hash_token(token)
    await conn.execute(
        "INSERT INTO api_tokens (name, token_hash, created_at, created_by) VALUES (?, ?, ?, ?)",
        (name.strip(), token_hash, now_iso(), user))
    await audit(conn, user, "api_token_created", "ok", target=token_ref(token_hash),
                ip=client_ip(request), name=name.strip())
    await conn.commit()
    async with conn.execute("SELECT * FROM api_tokens ORDER BY revoked_at IS NOT NULL, created_at DESC") as cur:
        tokens = await cur.fetchall()
    return render(request, user, "tokens.html", tokens=tokens, new_token=token, new_name=name.strip())


@router.post("/tokens/{token_id}/revoke")
async def revoke_token(request: Request, token_id: int, user: CsrfUser, conn: MainDb) -> Response:
    async with conn.execute("SELECT token_hash FROM api_tokens WHERE id = ?", (token_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(404)
    await conn.execute("UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                       (now_iso(), token_id))
    await audit(conn, user, "api_token_revoked", "ok", target=token_ref(row[0]), ip=client_ip(request))
    await conn.commit()
    return RedirectResponse("/tokens", status_code=303)


@router.get("/audit")
async def audit_view(request: Request, user: User, conn: MainDb, pconn: PublicDb,
                     action: str = Query("", max_length=64), page: int = Query(1, ge=1)) -> Response:
    """Registro de lo que cambia estado: el del panel, la API y el worker, y el del
    proceso público (que escribe en su propia BD)."""
    per_page = 100
    limit = per_page * page + 1
    params: list[Any] = [action] if action else []
    where = "WHERE action = ?" if action else ""
    async with conn.execute(
            f"SELECT ts, actor, action, target, result, ip, detail FROM audit {where} "
            "ORDER BY ts DESC, id DESC LIMIT ?", (*params, limit)) as cur:
        rows: list[dict[str, Any]] = [dict(r) | {"source": "panel"} for r in await cur.fetchall()]
    async with pconn.execute(
            f"SELECT ts, action, result, link_id, ref, ip, detail FROM events {where} "
            "ORDER BY ts DESC, id DESC LIMIT ?", (*params, limit)) as cur:
        for ev in await cur.fetchall():
            rows.append({"ts": ev["ts"], "actor": "público", "action": ev["action"],
                         "target": f"enlace {ev['link_id']}" if ev["link_id"] else ev["ref"],
                         "result": ev["result"], "ip": ev["ip"], "detail": ev["detail"],
                         "source": "public", "link_id": ev["link_id"]})
    rows.sort(key=lambda r: r["ts"], reverse=True)
    start = per_page * (page - 1)
    shown = rows[start:start + per_page]
    for entry in shown:
        entry["detail_obj"] = json.loads(entry["detail"]) if entry["detail"] else None
    async with conn.execute("SELECT DISTINCT action FROM audit") as cur:
        actions = {r[0] for r in await cur.fetchall()}
    async with pconn.execute("SELECT DISTINCT action FROM events") as cur:
        actions |= {r[0] for r in await cur.fetchall()}
    return render(request, user, "audit.html", rows=shown, action=action, actions=sorted(actions),
                  page=page, more=len(rows) > start + per_page)
