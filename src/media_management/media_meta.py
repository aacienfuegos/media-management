"""Lectura de fechas y dimensiones de los ficheros de la cámara.

Portado del generador de manifiesto que corría en el host, validado contra la
biblioteca real: el parseo tiene que dar exactamente lo mismo, así que la lógica
se conserva tal cual aunque haya formas más generales de leer EXIF."""
import datetime
import os
import re
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, BinaryIO
from zoneinfo import ZoneInfo

from media_management.db import iso
from media_management.settings import DISPLAY_TZ

MP4_EPOCH = datetime.datetime(1904, 1, 1, tzinfo=datetime.UTC)
NAME_DATE = re.compile(r"DJI_(\d{8})(\d{6})")
ISO_BMFF_EXT = frozenset({".mp4", ".mov", ".m4v"})
JPEG_EXT = frozenset({".jpg", ".jpeg"})


def boxes(f: BinaryIO, start: int, end: int) -> Iterator[tuple[str, int, int]]:
    """Cajas ISO-BMFF de un nivel: (tipo, offset, tamaño)."""
    off = start
    while off < end:
        f.seek(off)
        head = f.read(16)
        if len(head) < 8:
            return
        size = struct.unpack(">I", head[:4])[0]
        kind = head[4:8].decode("latin1", "replace")
        if size == 1:
            size = struct.unpack(">Q", head[8:16])[0]
        elif size == 0:
            size = end - off
        if size < 8:
            return
        yield kind, off, size
        off += size


def video_meta(path: str) -> dict[str, Any]:
    """creation_time (UTC, del mvhd), duración y dimensiones, leyendo solo cabeceras."""
    out: dict[str, Any] = {}
    total = os.path.getsize(path)
    with open(path, "rb") as f:
        for kind, off, length in boxes(f, 0, total):
            if kind != "moov":
                continue
            for k2, o2, l2 in boxes(f, off + 8, off + length):
                if k2 == "mvhd":
                    f.seek(o2 + 8)
                    ver = f.read(1)[0]
                    f.seek(o2 + 12)
                    if ver == 1:
                        created, _mod, scale, dur = struct.unpack(">QQIQ", f.read(28))
                    else:
                        created, _mod, scale, dur = struct.unpack(">IIII", f.read(16))
                    # creation_time 0 = la cámara no la puso; no vale como fecha.
                    if created:
                        out["captured_at_utc"] = iso(MP4_EPOCH + datetime.timedelta(seconds=created))
                    if scale:
                        out["duration_s"] = round(dur / scale, 3)
                elif k2 == "trak":
                    for k3, o3, _l3 in boxes(f, o2 + 8, o2 + l2):
                        if k3 != "tkhd":
                            continue
                        f.seek(o3 + 8)
                        ver = f.read(1)[0]
                        f.seek(o3 + (96 if ver == 1 else 84))
                        width, height = struct.unpack(">II", f.read(8))
                        if width >> 16 and height >> 16:
                            out.setdefault("width", width >> 16)
                            out.setdefault("height", height >> 16)
    return out


def photo_meta(path: str) -> dict[str, Any]:
    """Fecha del EXIF (hora LOCAL, sin huso) y dimensiones del SOF."""
    out: dict[str, Any] = {}
    with open(path, "rb") as f:
        head = f.read(200_000)
    exif = head.find(b"Exif\x00\x00")
    if exif != -1:
        m = re.search(rb"(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})",
                      head[exif:exif + 70_000])
        if m:
            y, mo, d, h, mi, s = (int(x) for x in m.groups())
            out["captured_at_local"] = datetime.datetime(y, mo, d, h, mi, s)
    i = 2
    while i < len(head) - 9:
        if head[i] != 0xFF:
            break
        marker = head[i + 1]
        length = int.from_bytes(head[i + 2:i + 4], "big")
        if length < 2:
            break
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            out["height"] = int.from_bytes(head[i + 5:i + 7], "big")
            out["width"] = int.from_bytes(head[i + 7:i + 9], "big")
            break
        if marker == 0xDA:
            break
        i += 2 + length
    return out


@dataclass
class FileMeta:
    name: str
    kind: str
    size_bytes: int
    mtime_ns: int
    captured_at_utc: str | None = None
    captured_at_local: datetime.datetime | None = None
    captured_at_source: str | None = None
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    meta_error: str | None = None

    @property
    def mtime_s(self) -> int:
        return self.mtime_ns // 1_000_000_000


def day_offsets(files: list[FileMeta]) -> dict[datetime.date, int]:
    """{día: segundos} entre la hora del NOMBRE (local de la cámara) y el UTC real,
    sacado de los vídeos. Las fotos no traen huso y el desfase cambia con el viaje y
    con el horario de verano: una hora en invierno en casa, varias en un viaje lejano."""
    per_day: dict[datetime.date, list[int]] = {}
    for f in files:
        if f.kind != "video" or not f.captured_at_utc:
            continue
        m = NAME_DATE.search(f.name)
        if not m:
            continue
        local = datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        utc = datetime.datetime.strptime(f.captured_at_utc, "%Y-%m-%dT%H:%M:%SZ")
        per_day.setdefault(local.date(), []).append(round((local - utc).total_seconds()))
    return {d: sorted(v)[len(v) // 2] for d, v in per_day.items()}


def effective_date(f: FileMeta, offsets: dict[datetime.date, int]) -> tuple[str | None, str | None]:
    """Fecha UTC con la que se ordena y se publica un fichero, y de dónde sale."""
    if not f.captured_at_utc and f.captured_at_local and offsets:
        local = f.captured_at_local
        nearest = min(offsets, key=lambda d: abs((d - local.date()).days))
        utc = iso((local - datetime.timedelta(seconds=offsets[nearest])).replace(tzinfo=datetime.UTC))
        return utc, f"exif+desfase_de_{nearest}"
    if f.captured_at_utc:
        return f.captured_at_utc, f.captured_at_source or "mp4_mvhd"
    return None, None


def extract(path: str, name: str, kind: str, profile: str, size: int, mtime_ns: int) -> FileMeta:
    """Metadatos de un fichero según el perfil de su raíz.

    `dji`: fecha del mvhd en vídeo y hora local del EXIF en foto (el UTC de la foto se
    deriva después, con el desfase de los vídeos del mismo día). `generic`: lo que
    haya, y si no hay nada, la fecha de modificación, diciendo de dónde sale."""
    meta = FileMeta(name=name, kind=kind, size_bytes=size, mtime_ns=mtime_ns)
    ext = os.path.splitext(name)[1].lower()
    raw: dict[str, Any] = {}
    try:
        if kind == "video" and (profile == "dji" or ext in ISO_BMFF_EXT):
            raw = video_meta(path)
        elif kind == "photo" and (profile == "dji" or ext in JPEG_EXT):
            raw = photo_meta(path)
    except Exception as e:  # noqa: BLE001 — un fichero ilegible no puede tumbar el escaneo
        meta.meta_error = f"{type(e).__name__}: {e}"[:200]
    meta.captured_at_utc = raw.get("captured_at_utc")
    meta.captured_at_local = raw.get("captured_at_local")
    meta.duration_s = raw.get("duration_s")
    meta.width = raw.get("width")
    meta.height = raw.get("height")
    if meta.captured_at_utc:
        meta.captured_at_source = "mp4_mvhd"
    if profile == "generic" and not meta.captured_at_utc:
        if meta.captured_at_local:
            local = meta.captured_at_local.replace(tzinfo=ZoneInfo(DISPLAY_TZ))
            meta.captured_at_utc = iso(local)
            meta.captured_at_source = "exif_hora_local_madrid"
        else:
            meta.captured_at_utc = iso(datetime.datetime.fromtimestamp(
                mtime_ns / 1_000_000_000, datetime.UTC))
            meta.captured_at_source = "mtime"
    return meta
