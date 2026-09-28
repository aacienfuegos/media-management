import base64
import datetime
import os
import secrets
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from media_management.db import connect, iso, now_iso, parse_iso
from media_management.links import active_link_by_hash, is_active, link_by_id, link_file, link_files
from media_management.logs import public_event
from media_management.public.credentials import Denied, authorize, grant_still_valid
from media_management.public.guard import client_ip
from media_management.roots import PathRejected, Root, resolve_in_root
from media_management.notify import notify_access_request
from media_management.security import (
    Ticket, format_code, hash_code, hash_token, new_request_code, sign_ticket, token_ref, verify_ticket)
from media_management.settings import Settings

router = APIRouter()
TOKEN = r"^[A-Za-z0-9_-]{20,100}$"
NOT_FOUND = {"error": "not_found"}


class ShareIn(BaseModel):
    token: str = Field(pattern=TOKEN)
    password: str | None = Field(None, max_length=256)
    code: str | None = Field(None, max_length=32)


class TicketIn(ShareIn):
    file: int = Field(ge=1)


def settings_of(request: Request) -> Settings:
    s: Settings = request.app.state.settings
    return s


async def main_ro(request: Request) -> aiosqlite.Connection | None:
    conn: aiosqlite.Connection | None = await request.app.state.main.get()
    return conn


async def public_db(request: Request) -> AsyncGenerator[aiosqlite.Connection]:
    async with connect(settings_of(request).public_db) as conn:
        yield conn


MainRo = Annotated[aiosqlite.Connection | None, Depends(main_ro)]
PublicDb = Annotated[aiosqlite.Connection, Depends(public_db)]


def unavailable() -> JSONResponse:
    return JSONResponse({"error": "unavailable"}, status_code=503)


def not_found() -> JSONResponse:
    return JSONResponse(NOT_FOUND, status_code=404)


def denied_response(d: Denied) -> JSONResponse:
    body: dict[str, Any] = {"need": d.need} if d.need else {}
    if d.error:
        body["error"] = d.error
    if d.status:
        body["status"] = d.status
    return JSONResponse(body, status_code=d.http)


def _thumb(data: bytes | None) -> str | None:
    return None if data is None else "data:image/jpeg;base64," + base64.b64encode(data).decode()


@router.post("/api/share")
async def share(request: Request, body: ShareIn, main: MainRo, pconn: PublicDb) -> Response:
    """Contenido de un enlace. Abrirlo no consume nada: solo se cuenta al pedir el
    ticket de un fichero concreto."""
    if main is None:
        return unavailable()
    settings = settings_of(request)
    ip = client_ip(request, settings)
    token_hash = hash_token(body.token)
    link = await active_link_by_hash(main, token_hash)
    if link is None:
        await public_event(pconn, "share_not_found", "denied", ref=token_ref(token_hash), ip=ip)
        await pconn.commit()
        return not_found()
    result = await authorize(main, pconn, settings, link, body.password, body.code, ip)
    await pconn.commit()
    if isinstance(result, Denied):
        return denied_response(result)
    files = [lf for lf in await link_files(main, link["id"]) if lf.present]
    return JSONResponse({
        "title": link["title"], "mode": link["mode"],
        "expires_at": result.expires_at,
        "files": [{"id": lf.id, "name": lf.name, "size": lf.size_bytes, "kind": lf.kind,
                   "thumb": _thumb(lf.thumb_jpeg)} for lf in files],
    })


def _servable(root: Root | None, relpath: str) -> Path | None:
    if root is None or not root.shareable:
        return None
    try:
        real = resolve_in_root(root, relpath)
    except PathRejected:
        return None
    return real if real.is_file() else None


@router.post("/api/ticket")
async def ticket(request: Request, body: TicketIn, main: MainRo, pconn: PublicDb) -> Response:
    """Ticket de descarga de un fichero. Firmado, ligado al enlace, al fichero y a la
    credencial, y con caducidad propia para poder reanudar."""
    if main is None:
        return unavailable()
    settings = settings_of(request)
    ip = client_ip(request, settings)
    token_hash = hash_token(body.token)
    link = await active_link_by_hash(main, token_hash)
    if link is None:
        return not_found()
    result = await authorize(main, pconn, settings, link, body.password, body.code, ip)
    if isinstance(result, Denied):
        await pconn.commit()
        return denied_response(result)
    lf = await link_file(main, link["id"], body.file)
    roots: dict[str, Root] = request.app.state.roots
    if lf is None or not lf.present or _servable(roots.get(lf.root), lf.relpath) is None:
        await pconn.commit()
        return not_found()
    expires = min(int(time.time()) + settings.ticket_ttl_s, int(parse_iso(result.expires_at).timestamp()))
    t = Ticket(secrets.token_hex(16), link["id"], lf.id, result.grant_id, expires)
    await pconn.execute(
        "INSERT INTO tickets (id, link_id, link_file_id, grant_id, issued_at, expires_at, ip, user_agent) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (t.ticket_id, t.link_id, t.link_file_id, t.grant_id, now_iso(),
         iso(datetime.datetime.fromtimestamp(expires, datetime.UTC)),
         ip, request.headers.get("user-agent", "")[:300]))
    await public_event(pconn, "ticket_issued", "ok", link_id=link["id"], ref=t.ticket_id[:12], ip=ip,
                       link_file_id=lf.id, grant_id=result.grant_id)
    await pconn.commit()
    return JSONResponse({"url": f"/download/{sign_ticket(settings.secret_key, t)}"})


def content_disposition(name: str) -> str:
    clean = "".join(c for c in name if c.isprintable() and c not in '"\\/').strip() or "descarga"
    ascii_name = "".join(c if 32 <= ord(c) < 127 else "_" for c in clean)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(clean, safe='')}"


@router.get("/download/{raw}")
async def download(request: Request, raw: str, main: MainRo, pconn: PublicDb) -> Response:
    """Valida el ticket en cada petición (también en cada reanudación con Range) y
    delega los bytes en nginx. Revocar el enlace o la concesión corta las peticiones
    siguientes; una transferencia ya en curso termina."""
    gone = PlainTextResponse("Descarga no disponible.", status_code=404)
    if main is None:
        return gone
    settings = settings_of(request)
    t = verify_ticket(settings.secret_key, raw)
    if t is None:
        return gone
    link = await link_by_id(main, t.link_id)
    if link is None or not is_active(link):
        return gone
    if t.grant_id is not None and not await grant_still_valid(main, t.grant_id, link["id"]):
        return gone
    lf = await link_file(main, link["id"], t.link_file_id)
    roots: dict[str, Root] = request.app.state.roots
    if lf is None or not lf.present:
        return gone
    real = _servable(roots.get(lf.root), lf.relpath)
    if real is None:
        return gone
    base = Path(os.path.realpath(settings.media_base))
    if not real.is_relative_to(base):
        return gone
    ip = client_ip(request, settings)
    range_header = request.headers.get("range")
    await pconn.execute(
        "INSERT INTO downloads (ticket_id, link_id, link_file_id, grant_id, ts, ip, user_agent, range_header) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (t.ticket_id, link["id"], lf.id, t.grant_id, now_iso(), ip,
         request.headers.get("user-agent", "")[:300], (range_header or "")[:100] or None))
    await public_event(pconn, "download_served", "ok", link_id=link["id"], ref=t.ticket_id[:12], ip=ip,
                       link_file_id=lf.id, grant_id=t.grant_id, range=range_header)
    await pconn.commit()
    return Response(status_code=200, headers={
        "X-Accel-Redirect": "/_protected/" + quote(str(real.relative_to(base)), safe="/"),
        "Content-Disposition": content_disposition(lf.name),
    })


class RequestIn(BaseModel):
    token: str = Field(pattern=TOKEN)
    name: str = Field(min_length=1, max_length=200)
    note: str = Field("", max_length=2000)


def plain(text: str, limit: int, multiline: bool = False) -> str:
    """Texto plano acotado: sin caracteres de control (salvo saltos de línea en la nota)."""
    lines = text.replace("\r\n", "\n").split("\n") if multiline else [text]
    clean = ["".join(c for c in line if c.isprintable()).strip() for line in lines]
    return "\n".join(clean).strip()[:limit]


@router.post("/api/request")
async def request_access(request: Request, body: RequestIn, main: MainRo, pconn: PublicDb) -> Response:
    """Solicitud de acceso a un enlace en modo `request`. Solo desde un enlace válido;
    el código personal se enseña una vez y se guarda hasheado."""
    if main is None:
        return unavailable()
    settings = settings_of(request)
    ip = client_ip(request, settings)
    link = await active_link_by_hash(main, hash_token(body.token))
    if link is None or link["mode"] != "request":
        return not_found()
    name = plain(body.name, 80)
    note = plain(body.note, 500, multiline=True)
    if not name:
        return JSONResponse({"error": "bad_request"}, status_code=400)
    hour_ago = iso(datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1))
    async with pconn.execute("SELECT COUNT(*) FROM access_requests WHERE ip = ? AND created_at >= ?",
                             (ip, hour_ago)) as cur:
        row = await cur.fetchone()
    if ip is not None and row and row[0] >= settings.max_requests_per_ip_per_hour:
        await public_event(pconn, "access_request", "rate_limited", link_id=link["id"], ip=ip)
        await pconn.commit()
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    cutoff = iso(datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=settings.request_ttl_days))
    async with pconn.execute("SELECT COUNT(*) FROM access_requests WHERE link_id = ? AND status = 'pending' "
                             "AND created_at >= ?", (link["id"], cutoff)) as cur:
        row = await cur.fetchone()
    if row and row[0] >= settings.max_pending_requests_per_link:
        await public_event(pconn, "access_request", "queue_full", link_id=link["id"], ip=ip)
        await pconn.commit()
        return JSONResponse({"error": "queue_full"}, status_code=429)
    code = new_request_code()
    cur2 = await pconn.execute(
        "INSERT INTO access_requests (link_id, name, note, code_hash, created_at, ip, user_agent) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (link["id"], name, note, hash_code(link["id"], code), now_iso(), ip,
         request.headers.get("user-agent", "")[:300]))
    await public_event(pconn, "access_request", "ok", link_id=link["id"], ip=ip, request_id=cur2.lastrowid)
    await pconn.commit()
    await notify_access_request(settings, link["id"], link["title"], name)
    return JSONResponse({"code": format_code(code)})
