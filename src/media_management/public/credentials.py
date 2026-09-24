import datetime
from dataclasses import dataclass

import aiosqlite

from media_management.db import iso, now_iso, utcnow
from media_management.logs import public_event
from media_management.public.guard import ip_blocked, link_delay, record_attempt
from media_management.security import hash_code, normalize_code, verify_password
from media_management.settings import Settings


@dataclass(frozen=True)
class Granted:
    grant_id: int | None
    expires_at: str


@dataclass(frozen=True)
class Denied:
    http: int
    need: str | None = None
    error: str | None = None
    status: str | None = None


RATE_LIMITED = Denied(429, error="rate_limited")


async def grant_still_valid(main: aiosqlite.Connection, grant_id: int, link_id: int) -> bool:
    async with main.execute("SELECT * FROM grants WHERE id = ? AND link_id = ?", (grant_id, link_id)) as cur:
        grant = await cur.fetchone()
    return grant is not None and grant["revoked_at"] is None and grant["expires_at"] > now_iso()


async def authorize(main: aiosqlite.Connection, pconn: aiosqlite.Connection, settings: Settings,
                    link: aiosqlite.Row, password: str | None, code: str | None,
                    ip: str | None) -> Granted | Denied:
    if link["mode"] == "open":
        return Granted(None, link["expires_at"])
    if await ip_blocked(pconn, settings, ip):
        await public_event(pconn, "auth_rate_limited", "denied", link_id=link["id"], ip=ip)
        return RATE_LIMITED
    if link["mode"] == "password":
        return await _password(pconn, settings, link, password, ip)
    return await _code(main, pconn, settings, link, code, ip)


async def _password(pconn: aiosqlite.Connection, settings: Settings, link: aiosqlite.Row,
                    password: str | None, ip: str | None) -> Granted | Denied:
    if not password:
        return Denied(401, need="password")
    await link_delay(pconn, settings, link["id"])
    ok = verify_password(link["password_hash"], password)
    await record_attempt(pconn, link["id"], "password", ip, ok)
    if not ok:
        await public_event(pconn, "password_failed", "denied", link_id=link["id"], ip=ip)
        return Denied(401, need="password", error="bad_credentials")
    return Granted(None, link["expires_at"])


async def _code(main: aiosqlite.Connection, pconn: aiosqlite.Connection, settings: Settings,
                link: aiosqlite.Row, raw: str | None, ip: str | None) -> Granted | Denied:
    """Código personal de una solicitud. Rechazada, revocada, caducada o inexistente
    dan la misma respuesta: hacia fuera no se distingue."""
    if not raw:
        return Denied(401, need="code")
    bad = Denied(401, need="code", error="bad_credentials")
    await link_delay(pconn, settings, link["id"])
    code = normalize_code(raw)
    req = None
    if code is not None:
        async with pconn.execute("SELECT * FROM access_requests WHERE code_hash = ? AND link_id = ?",
                                 (hash_code(link["id"], code), link["id"])) as cur:
            req = await cur.fetchone()
    if req is None:
        await record_attempt(pconn, link["id"], "code", ip, False)
        await public_event(pconn, "code_failed", "denied", link_id=link["id"], ip=ip)
        return bad
    async with main.execute("SELECT * FROM grants WHERE request_id = ? AND link_id = ?",
                            (req["id"], link["id"])) as cur:
        grant = await cur.fetchone()
    if grant is not None:
        if grant["revoked_at"] is None and grant["expires_at"] > now_iso():
            await record_attempt(pconn, link["id"], "code", ip, True)
            return Granted(grant["id"], min(grant["expires_at"], link["expires_at"]))
    elif req["status"] == "pending" and req["created_at"] > _request_cutoff(settings):
        await record_attempt(pconn, link["id"], "code", ip, True)
        return Denied(202, status="pending")
    await record_attempt(pconn, link["id"], "code", ip, False)
    await public_event(pconn, "code_failed", "denied", link_id=link["id"], ip=ip, request_id=req["id"])
    return bad


def _request_cutoff(settings: Settings) -> str:
    return iso(utcnow() - datetime.timedelta(days=settings.request_ttl_days))
