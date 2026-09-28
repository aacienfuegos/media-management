from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from media_management.api.deps import MainDb
from media_management.api.routes import router
from media_management.db import init_main
from media_management.health import health_detail
from media_management.roots import Root, load_roots
from media_management.settings import Settings, get_settings
from media_management.web import add_security_headers


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        await init_main(settings)
        yield

    app = FastAPI(title="media-management API", version="1", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url="/api/v1/openapi.json")
    app.state.settings = settings
    app.state.roots = load_roots(settings)
    add_security_headers(app)

    @app.get("/healthz")
    async def healthz(request: Request, conn: MainDb) -> JSONResponse:
        roots: dict[str, Root] = request.app.state.roots
        ok, detail = await health_detail(settings, roots, conn)
        return JSONResponse({"ok": ok, **detail}, status_code=200 if ok else 503)

    app.include_router(router)
    return app
