import datetime
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import aiosqlite
import jinja2
from fastapi import Depends, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates

from media_management.db import connect, parse_iso
from media_management.roots import Root
from media_management.security import check_csrf, csrf_token
from media_management.settings import DISPLAY_TZ, Settings, ip_in

TEMPLATES = Jinja2Templates(env=jinja2.Environment(
    loader=jinja2.FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True))
_TZ = ZoneInfo(DISPLAY_TZ)


def _local(value: str | None, fmt: str = "%d/%m/%Y %H:%M") -> str:
    if not value:
        return "—"
    return parse_iso(value).astimezone(_TZ).strftime(fmt)


def _size(n: int | None) -> str:
    if n is None:
        return "—"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return str(n)


def _duration(s: float | None) -> str:
    if s is None:
        return "—"
    total = int(round(s))
    return f"{total // 60}:{total % 60:02d}"


def _ago(value: str | None) -> str:
    if not value:
        return "nunca"
    delta = datetime.datetime.now(datetime.UTC) - parse_iso(value)
    secs = int(delta.total_seconds())
    if secs < 0:
        secs = -secs
        prefix = "dentro de "
    else:
        prefix = "hace "
    if secs < 90:
        return prefix + f"{secs} s"
    if secs < 5400:
        return prefix + f"{secs // 60} min"
    if secs < 172800:
        return prefix + f"{secs // 3600} h"
    return prefix + f"{secs // 86400} días"


TEMPLATES.env.filters["local"] = _local
TEMPLATES.env.filters["size"] = _size
TEMPLATES.env.filters["duration"] = _duration
TEMPLATES.env.filters["ago"] = _ago


def settings_of(request: Request) -> Settings:
    s: Settings = request.app.state.settings
    return s


def roots_of(request: Request) -> dict[str, Root]:
    r: dict[str, Root] = request.app.state.roots
    return r


def identity(request: Request) -> str:
    """Usuario que Authentik ha autenticado, leído de la cabecera que pone el outpost.

    Solo vale si la petición llega desde Traefik: cualquiera que alcance el puerto
    directamente puede inventarse la cabecera."""
    settings = settings_of(request)
    client = request.client.host if request.client else None
    if not ip_in(client, settings.trusted_proxies):
        raise HTTPException(403, "origen no permitido")
    user = request.headers.get("x-authentik-username", "")
    if not user or user not in settings.panel_allowed_users:
        raise HTTPException(403, "usuario no permitido")
    return user


User = Annotated[str, Depends(identity)]


def client_ip(request: Request) -> str | None:
    """IP del cliente para el registro. Solo se llega aquí desde Traefik (identity lo
    exige), que añade la IP con la que le conectaron al final de X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else None


async def main_db(request: Request) -> AsyncGenerator[aiosqlite.Connection]:
    async with connect(settings_of(request).main_db) as conn:
        yield conn


async def public_db(request: Request) -> AsyncGenerator[aiosqlite.Connection]:
    async with connect(settings_of(request).public_db) as conn:
        yield conn


MainDb = Annotated[aiosqlite.Connection, Depends(main_db)]
PublicDb = Annotated[aiosqlite.Connection, Depends(public_db)]


async def require_csrf(request: Request, user: User) -> str:
    """Toda petición que cambia estado lleva un token ligado al usuario y viene del
    propio panel: la cookie de sesión de Authentik viaja en cualquier petición al
    dominio, incluidas las que dispara otra web."""
    if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        raise HTTPException(403, "petición de otro origen")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "petición de otro origen")
    form = await request.form()
    given = form.get("csrf")
    if not isinstance(given, str) or not check_csrf(settings_of(request).secret_key, user, given):
        raise HTTPException(403, "token CSRF inválido")
    return user


CsrfUser = Annotated[str, Depends(require_csrf)]


def render(request: Request, user: str, template: str, status_code: int = 200, **ctx: Any) -> Response:
    ctx.setdefault("roots", roots_of(request))
    return TEMPLATES.TemplateResponse(
        request, template,
        {"user": user, "csrf": csrf_token(settings_of(request).secret_key, user), **ctx},
        status_code=status_code)
