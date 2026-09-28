import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from media_management.db import init_main

from tests.conftest import CSRF_KEY, TICKET_KEY, make_settings


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_public_process_does_not_log_the_ticket_path(tmp_path: Path) -> None:
    """El ticket va en la ruta: el access log de uvicorn lo escribiría en claro."""
    s = make_settings(tmp_path)
    env = {**os.environ, "MM_ROOTS_FILE": str(s.roots_file), "MM_MEDIA_BASE": str(s.media_base),
           "MM_DATA_DIR": str(s.data_dir), "MM_TICKET_KEY": TICKET_KEY, "MM_CSRF_KEY": CSRF_KEY}
    asyncio.run(init_main(s))
    port = free_port()
    proc = subprocess.Popen([str(Path(sys.executable).parent / "media-management"), "serve", "public",
                             "--host", "127.0.0.1", "--port", str(port)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    ticket = "SECRETO" + "x" * 40
    try:
        for _ in range(100):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        assert httpx.get(f"http://127.0.0.1:{port}/download/{ticket}").status_code in (403, 404)
    finally:
        proc.terminate()
        out, _ = proc.communicate(timeout=10)
    assert '"arranque"' in out
    assert ticket not in out
