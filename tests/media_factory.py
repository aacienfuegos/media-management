"""Media sintética para los tests: vídeos con creation_time en el mvhd y fotos con
EXIF escrito aquí mismo. Nada de esto sale de la biblioteca real."""
import datetime
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="hace falta ffmpeg para generar vídeos de prueba")


def make_video(path: Path, creation_utc: str | None, seconds: float = 1.0,
               size: str = "64x36", fps: int = 30) -> Path:
    assert FFMPEG is not None
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=navy:s={size}:d={seconds}:r={fps}",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "mp4"]
    if creation_utc:
        cmd += ["-metadata", f"creation_time={creation_utc}"]
    subprocess.run([*cmd, str(path)], check=True)
    return path


def _jpeg(size: str) -> bytes:
    assert FFMPEG is not None
    return subprocess.run(
        [FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"color=c=teal:s={size}:d=1", "-frames:v", "1",
         "-f", "image2pipe", "-c:v", "mjpeg", "-"], check=True, capture_output=True).stdout


def exif_app1(original: datetime.datetime) -> bytes:
    """Segmento APP1 con un IFD0 que apunta a un sub-IFD Exif con DateTimeOriginal."""
    when = original.strftime("%Y:%m:%d %H:%M:%S").encode() + b"\x00"
    ifd0_off = 8
    exif_off = ifd0_off + 2 + 12 + 4
    date_off = exif_off + 2 + 12 + 4
    tiff = b"II*\x00" + struct.pack("<I", ifd0_off)
    tiff += struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, exif_off) + struct.pack("<I", 0)
    tiff += struct.pack("<H", 1) + struct.pack("<HHII", 0x9003, 2, len(when), date_off) + struct.pack("<I", 0)
    tiff += when
    body = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body


def make_photo(path: Path, local: datetime.datetime | None, size: str = "80x60") -> Path:
    jpeg = _jpeg(size)
    assert jpeg[:2] == b"\xff\xd8"
    data = jpeg[:2] + (exif_app1(local) if local else b"") + jpeg[2:]
    path.write_bytes(data)
    return path
