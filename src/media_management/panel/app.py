import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from media_management.db import get_state, init_main, init_public, now_iso, set_state
from media_management.health import health_detail
from media_management.logs import audit
from media_management.panel import admin_routes, catalog_routes, links_routes, requests_routes, trash_routes
from media_management.panel.deps import (
    CsrfUser, MainDb, PublicDb, User, client_ip, render, roots_of, settings_of)
from media_management.roots import load_roots, media_problem
from media_management.settings import Settings, get_settings
from media_management.web import add_security_headers


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        await init_main(settings)
        await init_public(settings)
        yield

    app = FastAPI(title="media-management panel", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.roots = load_roots(settings)
    # same-origin y no no-referrer: con no-referrer el navegador manda `Origin: null` en
    # los POST de formulario y la comprobación de origen del CSRF no puede funcionar.
    add_security_headers(app, referrer_policy="same-origin")
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    @app.get("/healthz")
    async def healthz(request: Request, conn: MainDb) -> JSONResponse:
        ok, detail = await health_detail(settings_of(request), roots_of(request), conn)
        return JSONResponse({"ok": ok, **detail}, status_code=200 if ok else 503)

    @app.get("/")
    async def home(request: Request, user: User, conn: MainDb, pconn: PublicDb) -> Response:
        roots = roots_of(request)
        counts = {name: {"files": 0, "bytes": 0} for name in roots}
        async with conn.execute(
                "SELECT root, COUNT(*), COALESCE(SUM(size_bytes), 0) FROM files "
                "WHERE present = 1 GROUP BY root") as cur:
            for root, n, total in await cur.fetchall():
                if root in counts:
                    counts[root] = {"files": n, "bytes": total}
        now = now_iso()
        async with conn.execute(
                "SELECT COUNT(*) FROM links WHERE revoked_at IS NULL AND expires_at > ?", (now,)) as cur:
            row = await cur.fetchone()
        active_links = row[0] if row else 0
        async with pconn.execute(
                "SELECT COUNT(*) FROM access_requests WHERE status = 'pending'") as cur:
            row = await cur.fetchone()
        pending = row[0] if row else 0
        ok, health = await health_detail(settings_of(request), roots, conn)
        scan = await get_state(conn, "last_scan")
        manifest = await get_state(conn, "manifest_status")
        comparison = await get_state(conn, "manifest_comparison")
        return render(request, user, "home.html",
                      counts=counts, active_links=active_links, pending=pending,
                      health_ok=ok, health=health,
                      media_problem=media_problem(settings_of(request), roots),
                      scan=json.loads(scan) if scan else None,
                      manifest=json.loads(manifest) if manifest else None,
                      comparison=json.loads(comparison) if comparison else None)

    @app.post("/scan")
    async def request_scan(request: Request, user: CsrfUser, conn: MainDb) -> Response:
        await set_state(conn, "scan_requested", now_iso())
        await audit(conn, user, "scan_requested", "ok", ip=client_ip(request))
        await conn.commit()
        return RedirectResponse("/", status_code=303)

    app.include_router(catalog_routes.router)
    app.include_router(admin_routes.router)
    app.include_router(links_routes.router)
    app.include_router(requests_routes.router)
    app.include_router(trash_routes.router)
    return app
