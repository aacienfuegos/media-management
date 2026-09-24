import asyncio
import re
from typing import Any

from fastapi.testclient import TestClient

from media_management.db import connect
from media_management.security import csrf_token
from tests.conftest import SECRET, USER, Env
from tests.test_manifest import cycle

CSRF = csrf_token(SECRET, USER)


def sql(env: Env, query: str, params: tuple[Any, ...] = (), db: str = "main") -> list[Any]:
    path = env.settings.main_db if db == "main" else env.settings.public_db

    async def go() -> list[Any]:
        async with connect(path) as conn:
            async with conn.execute(query, params) as cur:
                rows = [tuple(r) for r in await cur.fetchall()]
            await conn.commit()
        return rows
    return asyncio.run(go())


def send_files(env: Env, names: dict[str, bytes]) -> dict[str, int]:
    for name, data in names.items():
        path = env.root("send") / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    cycle(env.settings)
    return {n: sql(env, "SELECT id FROM files WHERE root = 'send' AND relpath = ?", (n,))[0][0] for n in names}


def create_link(panel: TestClient, file_ids: list[int], mode: str = "open", password: str = "",
                days: int = 7, title: str = "") -> tuple[str, int]:
    r = panel.post("/links", data={"csrf": CSRF, "file_id": file_ids, "mode": mode, "password": password,
                                   "days": days, "title": title})
    assert r.status_code == 200, r.text
    m = re.search(r'id="link-url">[^<#]*#([A-Za-z0-9_-]+)<', r.text)
    assert m
    link_id = re.search(r'href="/links/(\d+)"', r.text)
    assert link_id
    return m.group(1), int(link_id.group(1))


def link_file_ids(public: TestClient, token: str, **cred: str) -> list[int]:
    r = public.post("/api/share", json={"token": token, **cred})
    assert r.status_code == 200, r.text
    return [f["id"] for f in r.json()["files"]]


def ticket_url(public: TestClient, token: str, file: int, **cred: str) -> str:
    r = public.post("/api/ticket", json={"token": token, "file": file, **cred})
    assert r.status_code == 200, r.text
    url: str = r.json()["url"]
    return url
