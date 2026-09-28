import datetime
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

import aiosqlite

from media_management.settings import Settings

ISO = "%Y-%m-%dT%H:%M:%SZ"


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(microsecond=0)


def iso(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.UTC).strftime(ISO)


def now_iso() -> str:
    return iso(utcnow())


def parse_iso(s: str) -> datetime.datetime:
    return datetime.datetime.strptime(s, ISO).replace(tzinfo=datetime.UTC)


async def _open(path: Path, readonly: bool) -> aiosqlite.Connection:
    if readonly:
        conn = await aiosqlite.connect(f"file:{path}?mode=ro", uri=True)
    else:
        conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA busy_timeout = 5000")
    if not readonly:
        await conn.execute("PRAGMA foreign_keys = ON")
    return conn


async def init_db(path: Path, schema: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(path)
    try:
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.executescript(files("media_management").joinpath(schema).read_text())
        await conn.commit()
    finally:
        await conn.close()


async def init_main(settings: Settings) -> None:
    await init_db(settings.main_db, "schema_main.sql")


async def init_public(settings: Settings) -> None:
    await init_db(settings.public_db, "schema_public.sql")


@asynccontextmanager
async def connect(path: Path, readonly: bool = False) -> AsyncGenerator[aiosqlite.Connection]:
    conn = await _open(path, readonly)
    try:
        yield conn
    finally:
        await conn.close()


async def open_persistent(path: Path, readonly: bool = False) -> aiosqlite.Connection:
    return await _open(path, readonly)


async def get_state(conn: aiosqlite.Connection, key: str) -> str | None:
    async with conn.execute("SELECT value FROM state WHERE key = ?", (key,)) as cur:
        row = await cur.fetchone()
    return None if row is None else str(row[0])


async def set_state(conn: aiosqlite.Connection, key: str, value: str) -> None:
    await conn.execute(
        "INSERT INTO state (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, now_iso()))
