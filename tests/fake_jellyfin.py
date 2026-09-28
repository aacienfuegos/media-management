"""Jellyfin falso: un servidor HTTP de verdad en un hilo. Sustituye a un servicio
externo; el disco y la BD de los tests siguen siendo reales."""
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

JPEG = b"\xff\xd8\xff\xe0" + b"fake-jpeg" * 20 + b"\xff\xd9"


class FakeJellyfin:
    def __init__(self) -> None:
        self.images: dict[str, bytes] = {}
        self.requests: list[str] = []
        self.fail_all = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                fake.requests.append(self.path)
                parts = urlsplit(self.path)
                if parts.path == "/System/Info/Public" and not fake.fail_all:
                    self._send(200, b'{"ServerName":"fake"}', "application/json")
                    return
                segs = parts.path.strip("/").split("/")
                if (len(segs) == 4 and segs[0] == "Items" and segs[2:] == ["Images", "Primary"]
                        and not fake.fail_all and segs[1] in fake.images):
                    self._send(200, fake.images[segs[1]], "image/jpeg")
                    return
                # Jellyfin contesta 500, no 404, a un ID que no existe.
                self._send(500, b"error", "text/plain")

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler

    def start(self) -> None:
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def jellyfin() -> Iterator[FakeJellyfin]:
    fake = FakeJellyfin()
    fake.start()
    yield fake
    fake.stop()
