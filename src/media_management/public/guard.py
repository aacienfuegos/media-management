import asyncio
import datetime
import ipaddress

import aiosqlite
from fastapi import Request

from media_management.db import iso, now_iso, utcnow
from media_management.settings import Settings, ip_in


def client_ip(request: Request, settings: Settings) -> str | None:
    """IP real del cliente. Solo se cree la X-Real-IP que pone nginx, y solo si la
    petición llega desde nginx; nginx a su vez solo se fía de lo que manda Traefik."""
    peer = request.client.host if request.client else None
    if ip_in(peer, settings.trusted_nginx):
        real = request.headers.get("x-real-ip", "").strip()
        try:
            return str(ipaddress.ip_address(real))
        except ValueError:
            return peer
    return peer


def _since(settings: Settings) -> str:
    return iso(utcnow() - datetime.timedelta(seconds=settings.auth_window_s))


async def ip_blocked(pconn: aiosqlite.Connection, settings: Settings, ip: str | None) -> bool:
    """Bloqueo por IP tras demasiados fallos de credencial en la ventana, en cualquier
    enlace. El bloqueo es por IP y nunca por enlace: si no, cualquiera con el enlace lo
    inutilizaría para los demás fallando a propósito."""
    if ip is None:
        return False
    async with pconn.execute("SELECT COUNT(*) FROM auth_attempts WHERE ip = ? AND ok = 0 AND ts >= ?",
                             (ip, _since(settings))) as cur:
        row = await cur.fetchone()
    return bool(row and row[0] >= settings.auth_max_failures_per_ip)


async def link_delay(pconn: aiosqlite.Connection, settings: Settings, link_id: int) -> None:
    """Retardo creciente por enlace según los fallos recientes: frena la fuerza bruta
    repartida entre muchas IPs sin cerrarle la puerta a quien tiene la credencial."""
    async with pconn.execute("SELECT COUNT(*) FROM auth_attempts WHERE link_id = ? AND ok = 0 AND ts >= ?",
                             (link_id, _since(settings))) as cur:
        row = await cur.fetchone()
    failures = row[0] if row else 0
    delay = min(failures * settings.auth_delay_step_s, settings.auth_delay_max_s)
    if delay > 0:
        await asyncio.sleep(delay)


async def record_attempt(pconn: aiosqlite.Connection, link_id: int, kind: str, ip: str | None, ok: bool) -> None:
    await pconn.execute("INSERT INTO auth_attempts (link_id, kind, ip, ts, ok) VALUES (?, ?, ?, ?, ?)",
                        (link_id, kind, ip, now_iso(), int(ok)))
