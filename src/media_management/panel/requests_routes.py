import datetime
import sqlite3
from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from media_management.db import iso, now_iso, parse_iso, utcnow
from media_management.links import expiry, is_active, link_by_id
from media_management.logs import audit
from media_management.panel.deps import CsrfUser, MainDb, PublicDb, User, client_ip, render, settings_of

router = APIRouter()


def _cutoff(days: int) -> str:
    return iso(utcnow() - datetime.timedelta(days=days))


async def _pending_request(pconn: aiosqlite.Connection, request_id: int, ttl_days: int) -> aiosqlite.Row:
    async with pconn.execute("SELECT * FROM access_requests WHERE id = ?", (request_id,)) as cur:
        req = await cur.fetchone()
    if req is None or req["status"] != "pending" or req["created_at"] < _cutoff(ttl_days):
        raise HTTPException(404, "la solicitud no existe, ya está resuelta o ha caducado")
    return req


@router.get("/requests")
async def requests_view(request: Request, user: User, conn: MainDb, pconn: PublicDb) -> Response:
    settings = settings_of(request)
    cutoff = _cutoff(settings.request_ttl_days)
    async with pconn.execute("SELECT * FROM access_requests WHERE status = 'pending' AND created_at >= ? "
                             "ORDER BY created_at", (cutoff,)) as cur:
        pending = await cur.fetchall()
    async with pconn.execute("SELECT * FROM access_requests WHERE status != 'pending' OR created_at < ? "
                             "ORDER BY created_at DESC LIMIT 100", (cutoff,)) as cur:
        recent = await cur.fetchall()
    titles: dict[int, str] = {}
    for r in [*pending, *recent]:
        if r["link_id"] not in titles:
            link = await link_by_id(conn, r["link_id"])
            titles[r["link_id"]] = link["title"] if link else "?"
    return render(request, user, "requests.html", pending=pending, recent=recent, titles=titles, cutoff=cutoff)


@router.get("/requests/{request_id}")
async def request_view(request: Request, request_id: int, user: User, conn: MainDb, pconn: PublicDb) -> Response:
    settings = settings_of(request)
    req = await _pending_request(pconn, request_id, settings.request_ttl_days)
    link = await link_by_id(conn, req["link_id"])
    if link is None:
        raise HTTPException(404)
    remaining = max(1, (parse_iso(link["expires_at"]) - utcnow()).days + 1)
    async with pconn.execute("SELECT COUNT(*) FROM access_requests WHERE ip = ? AND id != ?",
                             (req["ip"], request_id)) as cur:
        row = await cur.fetchone()
    return render(request, user, "request.html", req=req, link=link, active=is_active(link),
                  default_days=min(remaining, settings.link_max_days), max_days=settings.link_max_days,
                  same_ip=row[0] if row else 0)


@router.post("/requests/{request_id}/approve")
async def approve(request: Request, request_id: int, user: CsrfUser, conn: MainDb, pconn: PublicDb,
                  days: Annotated[int, Form(ge=1)]) -> Response:
    settings = settings_of(request)
    req = await _pending_request(pconn, request_id, settings.request_ttl_days)
    link = await link_by_id(conn, req["link_id"])
    if link is None or not is_active(link) or link["mode"] != "request":
        raise HTTPException(409, "el enlace ya no está activo")
    if days > settings.link_max_days:
        raise HTTPException(400, f"máximo {settings.link_max_days} días")
    # La concesión en main.db es la fuente de verdad: si se escribe y falla lo de
    # public.db, el público ve la concesión igualmente.
    try:
        cur = await conn.execute(
            "INSERT INTO grants (link_id, request_id, name, created_at, approved_by, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (link["id"], req["id"], req["name"], now_iso(), user, expiry(days, settings.link_max_days)))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "la solicitud ya está aprobada") from None
    await audit(conn, user, "access_approved", "ok", target=f"enlace {link['id']}", ip=client_ip(request),
                link_id=link["id"], request_id=req["id"], grant_id=cur.lastrowid, days=days)
    await conn.commit()
    await pconn.execute("UPDATE access_requests SET status = 'approved', resolved_at = ?, resolved_by = ? "
                        "WHERE id = ?", (now_iso(), user, req["id"]))
    await pconn.commit()
    return RedirectResponse(f"/links/{link['id']}", status_code=303)


@router.post("/requests/{request_id}/reject")
async def reject(request: Request, request_id: int, user: CsrfUser, conn: MainDb, pconn: PublicDb) -> Response:
    settings = settings_of(request)
    req = await _pending_request(pconn, request_id, settings.request_ttl_days)
    await pconn.execute("UPDATE access_requests SET status = 'rejected', resolved_at = ?, resolved_by = ? "
                        "WHERE id = ?", (now_iso(), user, req["id"]))
    await pconn.commit()
    await audit(conn, user, "access_rejected", "ok", target=f"enlace {req['link_id']}", ip=client_ip(request),
                link_id=req["link_id"], request_id=req["id"])
    await conn.commit()
    return RedirectResponse("/requests", status_code=303)


@router.post("/grants/{grant_id}/revoke")
async def revoke_grant(request: Request, grant_id: int, user: CsrfUser, conn: MainDb) -> Response:
    async with conn.execute("SELECT * FROM grants WHERE id = ?", (grant_id,)) as cur:
        grant = await cur.fetchone()
    if grant is None:
        raise HTTPException(404)
    await conn.execute("UPDATE grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (now_iso(), grant_id))
    await audit(conn, user, "grant_revoked", "ok", target=f"enlace {grant['link_id']}", ip=client_ip(request),
                link_id=grant["link_id"], grant_id=grant_id)
    await conn.commit()
    return RedirectResponse(f"/links/{grant['link_id']}", status_code=303)
