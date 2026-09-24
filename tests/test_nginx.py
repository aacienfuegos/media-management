"""nginx de verdad delante del proceso público: IP real, X-Accel-Redirect, Range y
límites por IP. Necesita Docker; se salta sin él."""
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from media_management.public.app import create_app as create_public
from tests.conftest import Env
from tests.helpers import create_link, link_file_ids, send_files, sql

pytestmark = pytest.mark.docker
TEMPLATE = Path(__file__).parent.parent / "deploy" / "nginx" / "share.conf.template"
IMAGE = "nginx:1.28-alpine"
DOCKER_NETS = ["172.16.0.0/12", "10.0.0.0/8"]


def docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def stack(env: Env, panel: TestClient, request: pytest.FixtureRequest) -> Iterator[tuple[Env, TestClient, str]]:
    traefik = getattr(request, "param", "172.16.0.0/12")
    if not docker_ok():
        pytest.skip("hace falta Docker para levantar nginx")
    settings = env.settings.model_copy(update={"trusted_nginx": DOCKER_NETS})
    app_port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_public(settings), host="0.0.0.0", port=app_port,
                                           log_level="warning", proxy_headers=False))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    nginx_port = free_port()
    name = f"mm-nginx-test-{uuid.uuid4().hex[:8]}"
    templates = env.base.parent / "nginx-templates"
    templates.mkdir()
    shutil.copy(TEMPLATE, templates / "default.conf.template")
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, "--add-host", "host.docker.internal:host-gateway",
         "-p", f"127.0.0.1:{nginx_port}:8080",
         "-v", f"{templates}:/etc/nginx/templates:ro", "-v", f"{env.base}:/media:ro",
         "-e", f"TRAEFIK_IP={traefik}", "-e", f"PUBLIC_UPSTREAM=host.docker.internal:{app_port}",
         "-e", "MEDIA_DIR=/media", "-e", "DOWNLOAD_RATE=256k", "-e", "DOWNLOAD_CONN_PER_IP=1",
         "-e", "API_RATE=600r/m", IMAGE], check=True, capture_output=True)
    base = f"http://127.0.0.1:{nginx_port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{base}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
            pytest.fail("nginx no arranca: " + logs.stdout + logs.stderr)
        yield env, panel, base
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        server.should_exit = True


def test_download_through_nginx(stack: tuple[Env, TestClient, str]) -> None:
    env, panel, base = stack
    data = os.urandom(300_000)
    ids = send_files(env, {"viaje/clip grande ñ.mp4": data})
    token, _ = create_link(panel, [ids["viaje/clip grande ñ.mp4"]])
    client = httpx.Client(base_url=base, headers={"X-Forwarded-For": "203.0.113.5", "X-Real-IP": "6.6.6.6"})
    share = client.post("/api/share", json={"token": token}).json()
    url = client.post("/api/ticket", json={"token": token, "file": share["files"][0]["id"]}).json()["url"]
    full = client.get(url)
    assert full.status_code == 200 and full.content == data
    assert full.headers["content-type"] == "application/octet-stream"
    assert "filename*=UTF-8''clip%20grande%20%C3%B1.mp4" in full.headers["content-disposition"]
    assert full.headers["x-content-type-options"] == "nosniff"
    assert "x-accel-redirect" not in full.headers
    part = client.get(url, headers={"Range": "bytes=1000-1999"})
    assert part.status_code == 206 and part.content == data[1000:2000]
    assert [r[0] for r in sql(env, "SELECT ip FROM tickets", db="public")] == ["203.0.113.5"]
    assert sql(env, "SELECT COUNT(*) FROM downloads", db="public")[0][0] == 2
    assert client.get("/_protected/viaje/" + quote("clip grande ñ.mp4")).status_code == 404
    assert client.get("/_protected/../roots.toml").status_code in (400, 404)


def test_connection_limit_is_per_client_ip(stack: tuple[Env, TestClient, str]) -> None:
    env, panel, base = stack
    ids = send_files(env, {"grande.bin": os.urandom(4_000_000)})
    token, _ = create_link(panel, [ids["grande.bin"]])
    helper = httpx.Client(base_url=base, headers={"X-Forwarded-For": "203.0.113.20"})
    fid = link_file_ids(TestClient(create_public(env.settings)), token)[0]
    url = helper.post("/api/ticket", json={"token": token, "file": fid}).json()["url"]
    started = threading.Event()
    done = threading.Event()

    def slow_download() -> None:
        try:
            with httpx.stream("GET", base + url, headers={"X-Forwarded-For": "203.0.113.20"}, timeout=60) as r:
                for _ in r.iter_bytes(chunk_size=16_384):
                    started.set()
                    if done.is_set():
                        break
        except httpx.HTTPError:
            pass  # el contenedor se retira mientras la descarga lenta sigue abierta

    t = threading.Thread(target=slow_download, daemon=True)
    t.start()
    assert started.wait(10)
    same_ip = httpx.get(base + url, headers={"X-Forwarded-For": "203.0.113.20", "Range": "bytes=0-10"})
    other_ip = httpx.get(base + url, headers={"X-Forwarded-For": "203.0.113.21", "Range": "bytes=0-10"})
    done.set()
    assert same_ip.status_code == 429
    assert other_ip.status_code == 206


@pytest.mark.parametrize("stack", ["192.0.2.1"], indirect=True)
def test_forwarded_for_is_ignored_when_not_from_traefik(stack: tuple[Env, TestClient, str]) -> None:
    env, panel, base = stack
    ids = send_files(env, {"a.txt": b"hola"})
    token, _ = create_link(panel, [ids["a.txt"]])
    client = httpx.Client(base_url=base, headers={"X-Forwarded-For": "203.0.113.5"})
    fid = client.post("/api/share", json={"token": token}).json()["files"][0]["id"]
    assert client.post("/api/ticket", json={"token": token, "file": fid}).status_code == 200
    recorded = sql(env, "SELECT ip FROM tickets", db="public")[0][0]
    assert recorded != "203.0.113.5" and recorded.startswith("172.")
