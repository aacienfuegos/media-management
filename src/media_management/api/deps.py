from collections.abc import AsyncGenerator
from typing import Annotated

import aiosqlite
from fastapi import Depends, HTTPException, Request

from media_management.db import connect, now_iso
from media_management.security import hash_token


async def main_db(request: Request) -> AsyncGenerator[aiosqlite.Connection]:
    async with connect(request.app.state.settings.main_db) as conn:
        yield conn


MainDb = Annotated[aiosqlite.Connection, Depends(main_db)]


async def integration_token(request: Request, conn: MainDb) -> int:
    """Token de integración de solo lectura. Este proceso no conoce cabeceras de
    Authentik: aquí solo autentica un token."""
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "falta el token", headers={"WWW-Authenticate": "Bearer"})
    async with conn.execute(
            "SELECT id FROM api_tokens WHERE token_hash = ? AND revoked_at IS NULL",
            (hash_token(token.strip()),)) as cur:
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(401, "token inválido", headers={"WWW-Authenticate": "Bearer"})
    await conn.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (now_iso(), row[0]))
    await conn.commit()
    return int(row[0])


TokenId = Annotated[int, Depends(integration_token)]
