import asyncio
from typing import Annotated, Any, Literal

import aiosqlite
from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response

from media_management.db import now_iso
from media_management.jellyfin import Jellyfin
from media_management.links import expiry, is_active, link_by_id, link_files, strip_jpeg_metadata
from media_management.logs import audit
from media_management.panel.deps import CsrfUser, MainDb, PublicDb, User, client_ip, render, roots_of, settings_of
from media_management.roots import Root
from media_management.security import hash_password, hash_token, new_token, token_ref
from media_management.thumbs import get_thumb, thumb_item_id

router = APIRouter()
MAX_FILES = 500


async def _selectable(conn: aiosqlite.Connection, roots: dict[str, Root], ids: list[int]) -> list[aiosqlite.Row]:
    """Ficheros presentes de raíces compartibles, en el orden pedido y sin repetir."""
    out: list[aiosqlite.Row] = []
    seen: set[int] = set()
    for fid in ids[:MAX_FILES]:
        if fid in seen:
            continue
        seen.add(fid)
        async with conn.execute("SELECT * FROM files WHERE id = ? AND present = 1", (fid,)) as cur:
            row = await cur.fetchone()
        if row is not None and (root := roots.get(row["root"])) is not None and root.shareable:
            out.append(row)
    return out


@router.get("/links/new")
async def new_link(request: Request, user: User, conn: MainDb,
                   file_id: Annotated[list[int], Query()] = []) -> Response:  # noqa: B006
    files = await _selectable(conn, roots_of(request), file_id)
    settings = settings_of(request)
    return render(request, user, "link_new.html", files=files, error=None,
                  default_days=settings.link_default_days, max_days=settings.link_max_days,
                  title="", mode="open")


@router.post("/links")
async def create_link(request: Request, user: CsrfUser, conn: MainDb,
                      file_id: Annotated[list[int], Form()],
                      title: Annotated[str, Form(max_length=120)] = "",
                      mode: Annotated[Literal["open", "password", "request"], Form()] = "open",
                      days: Annotated[int, Form(ge=1)] = 7,
                      password: Annotated[str, Form(max_length=256)] = "") -> Response:
    settings = settings_of(request)
    roots = roots_of(request)
    files = await _selectable(conn, roots, file_id)
    error = None
    if not files:
        error = "No hay ningún fichero compartible seleccionado."
    elif days > settings.link_max_days:
        error = f"La caducidad máxima es de {settings.link_max_days} días."
    elif mode == "password" and len(password) < 8:
        error = "La contraseña tiene que tener al menos 8 caracteres."
    if error:
        return render(request, user, "link_new.html", status_code=400, files=files, error=error,
                      default_days=days, max_days=settings.link_max_days, title=title, mode=mode)
    token = new_token()
    token_hash = hash_token(token)
    title = title.strip() or (files[0]["name"] if len(files) == 1 else f"{len(files)} ficheros")
    cur = await conn.execute(
        "INSERT INTO links (token_hash, title, mode, password_hash, created_at, created_by, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (token_hash, title, mode, await asyncio.to_thread(hash_password, password) if mode == "password" else None,
         now_iso(), user, expiry(days, settings.link_max_days)))
    link_id = cur.lastrowid
    jellyfin = Jellyfin(settings.jellyfin_url, settings.jellyfin_timeout_s)
    for pos, f in enumerate(files):
        # La miniatura se copia ahora: el proceso público no habla con Jellyfin.
        item_id = await thumb_item_id(conn, roots, f)
        raw = await get_thumb(settings.cache_dir, jellyfin, item_id) if item_id else None
        thumb = strip_jpeg_metadata(raw) if raw else None
        await conn.execute("INSERT INTO link_files (link_id, file_id, position, thumb_jpeg) VALUES (?, ?, ?, ?)",
                           (link_id, f["id"], pos, thumb))
    await audit(conn, user, "link_created", "ok", target=token_ref(token_hash), ip=client_ip(request),
                link_id=link_id, mode=mode, files=len(files), days=days)
    await conn.commit()
    link = await link_by_id(conn, int(link_id or 0))
    return render(request, user, "link_created.html", link=link, files=files,
                  url=f"{settings.public_url.rstrip('/')}/#{token}")


async def _download_stats(pconn: aiosqlite.Connection, link_ids: list[int]) -> dict[int, dict[str, Any]]:
    stats: dict[int, dict[str, Any]] = {i: {"tickets": 0, "last": None} for i in link_ids}
    async with pconn.execute("SELECT link_id, COUNT(*), MAX(issued_at) FROM tickets GROUP BY link_id") as cur:
        for lid, n, last in await cur.fetchall():
            if lid in stats:
                stats[lid] = {"tickets": n, "last": last}
    return stats


@router.get("/links")
async def links_view(request: Request, user: User, conn: MainDb, pconn: PublicDb,
                     show: Literal["active", "all"] = "active") -> Response:
    now = now_iso()
    query = "SELECT l.*, (SELECT COUNT(*) FROM link_files WHERE link_id = l.id) AS nfiles FROM links l"
    if show == "active":
        query += " WHERE l.revoked_at IS NULL AND l.expires_at > ?"
        params: tuple[str, ...] = (now,)
    else:
        params = ()
    async with conn.execute(query + " ORDER BY l.created_at DESC", params) as cur:
        links = await cur.fetchall()
    async with pconn.execute("SELECT link_id, COUNT(*) FROM access_requests WHERE status = 'pending' "
                             "GROUP BY link_id") as cur:
        pending = {r[0]: r[1] for r in await cur.fetchall()}
    stats = await _download_stats(pconn, [link["id"] for link in links])
    return render(request, user, "links.html", links=links, stats=stats, pending=pending, now=now, show=show)


@router.get("/links/{link_id}")
async def link_view(request: Request, link_id: int, user: User, conn: MainDb, pconn: PublicDb) -> Response:
    link = await link_by_id(conn, link_id)
    if link is None:
        raise HTTPException(404)
    files = await link_files(conn, link_id)
    names = {lf.id: lf.name for lf in files}
    async with pconn.execute("SELECT link_file_id, COUNT(*) FROM tickets WHERE link_id = ? GROUP BY link_file_id",
                             (link_id,)) as cur:
        per_file = {r[0]: r[1] for r in await cur.fetchall()}
    async with pconn.execute(
            "SELECT t.*, (SELECT COUNT(*) FROM downloads d WHERE d.ticket_id = t.id) AS requests, "
            "(SELECT MAX(ts) FROM downloads d WHERE d.ticket_id = t.id) AS last_request "
            "FROM tickets t WHERE t.link_id = ? ORDER BY t.issued_at DESC LIMIT 500", (link_id,)) as cur:
        tickets = await cur.fetchall()
    async with pconn.execute(
            "SELECT COUNT(*) FROM auth_attempts WHERE link_id = ? AND ok = 0", (link_id,)) as cur:
        row = await cur.fetchone()
    failures = row[0] if row else 0
    async with pconn.execute("SELECT * FROM access_requests WHERE link_id = ? ORDER BY created_at DESC",
                             (link_id,)) as cur:
        requests = await cur.fetchall()
    async with conn.execute("SELECT * FROM grants WHERE link_id = ? ORDER BY created_at DESC", (link_id,)) as cur:
        grants = await cur.fetchall()
    grant_names = {g["id"]: g["name"] for g in grants}
    by_grant: dict[int, dict[str, Any]] = {}
    for t in tickets:
        if t["grant_id"] is not None:
            entry = by_grant.setdefault(t["grant_id"], {"downloads": 0, "ips": set(), "agents": set()})
            entry["downloads"] += 1
            entry["ips"].add(t["ip"])
            entry["agents"].add(t["user_agent"])
    settings = settings_of(request)
    return render(request, user, "link.html", link=link, files=files, names=names, per_file=per_file,
                  tickets=tickets, failures=failures, requests=requests, grants=grants,
                  grant_names=grant_names, by_grant=by_grant, now=now_iso(), active=is_active(link),
                  max_days=settings.link_max_days, request_ttl_days=settings.request_ttl_days)


@router.post("/links/{link_id}/renew")
async def renew_link(request: Request, link_id: int, user: CsrfUser, conn: MainDb,
                     days: Annotated[int, Form(ge=1)]) -> Response:
    settings = settings_of(request)
    link = await link_by_id(conn, link_id)
    if link is None or link["revoked_at"] is not None:
        raise HTTPException(404)
    if days > settings.link_max_days:
        raise HTTPException(400, f"máximo {settings.link_max_days} días")
    new_expiry = expiry(days, settings.link_max_days)
    await conn.execute("UPDATE links SET expires_at = ? WHERE id = ?", (new_expiry, link_id))
    await audit(conn, user, "link_renewed", "ok", target=token_ref(link["token_hash"]), ip=client_ip(request),
                link_id=link_id, expires_at=new_expiry)
    await conn.commit()
    return RedirectResponse(f"/links/{link_id}", status_code=303)


@router.post("/links/{link_id}/revoke")
async def revoke_link(request: Request, link_id: int, user: CsrfUser, conn: MainDb) -> Response:
    link = await link_by_id(conn, link_id)
    if link is None:
        raise HTTPException(404)
    await conn.execute("UPDATE links SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (now_iso(), link_id))
    await audit(conn, user, "link_revoked", "ok", target=token_ref(link["token_hash"]), ip=client_ip(request),
                link_id=link_id)
    await conn.commit()
    return RedirectResponse(f"/links/{link_id}", status_code=303)
