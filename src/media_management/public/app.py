import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from media_management.db import init_public, open_persistent
from media_management.public.routes import router
from media_management.roots import load_roots, media_problem
from media_management.settings import Settings, get_settings
from media_management.web import add_security_headers

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class MainReader:
    """Conexión de solo lectura a `main.db`, abierta una vez y mantenida.

    `mode=ro` impide escribir desde aquí; el contenedor, además, ve el directorio
    montado en solo lectura. Una conexión viva impide que el último escritor que
    cierra borre el -wal/-shm, sin los que no se podría volver a abrir."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.conn: aiosqlite.Connection | None = None
        self.lock = asyncio.Lock()

    async def get(self) -> aiosqlite.Connection | None:
        if self.conn is not None:
            return self.conn
        async with self.lock:
            if self.conn is None:
                try:
                    conn = await open_persistent(self.path, readonly=True)
                    await conn.execute("SELECT 1 FROM links LIMIT 1")
                    self.conn = conn
                except Exception as e:  # noqa: BLE001 — main.db aún no existe o no se puede abrir
                    log.warning("main.db no disponible", extra={"fields": {"error": type(e).__name__}})
                    return None
        return self.conn

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    reader = MainReader(settings.main_db)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        await init_public(settings)
        await reader.get()
        yield
        await reader.close()

    app = FastAPI(title="share", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.roots = load_roots(settings)
    app.state.main = reader
    add_security_headers(app)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html; charset=utf-8")

    @app.get("/healthz", include_in_schema=False)
    async def healthz(request: Request) -> Response:
        """Solo sí o no: a internet no se le cuenta qué raíces hay ni por qué falla."""
        ok = media_problem(settings, request.app.state.roots) is None and await reader.get() is not None
        return PlainTextResponse("ok" if ok else "unavailable", status_code=200 if ok else 503)

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Sin eco de la entrada: el cuerpo lleva el token del enlace y credenciales.
        return JSONResponse({"error": "bad_request"}, status_code=400)

    app.include_router(router)
    return app
