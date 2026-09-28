"""Regenera los fixtures del modo demo (src/media_management/demo/fixtures).

    uv run python deploy/demo/make_fixtures.py

Necesita ffmpeg y exiv2. Los fixtures van versionados para que arrancar el demo no
dependa de tenerlos ni de su versión: regenerarlos cambia los bytes, así que después
hay que actualizar deploy/demo/manifest.expected.json (ver README).

Las fechas son las de la cámara: hora LOCAL en el nombre, UTC en el mvhd (invierno,
una hora menos) y hora local sin huso en el EXIF de las fotos."""
import datetime
import shutil
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "src" / "media_management" / "demo" / "fixtures"
HD, UHD = "1920x1080", "3840x2160"
WINTER = datetime.timedelta(hours=1)


def utc_of(name_local: str) -> str:
    local = datetime.datetime.strptime(name_local, "%Y%m%d%H%M%S")
    return (local - WINTER).strftime("%Y-%m-%dT%H:%M:%SZ")


def video(path: Path, source: str, size: str, seconds: float, fps: int,
          created_utc: str | None) -> None:
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
           "-i", f"{source}=s={size}:r={fps}:d={seconds}",
           "-f", "lavfi", "-i", f"sine=f=440:d={seconds}",
           "-c:v", "libx264", "-preset", "veryslow", "-crf", "42", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "16k", "-shortest",
           "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
           "-map_metadata", "-1"]
    if created_utc:
        cmd += ["-metadata", f"creation_time={created_utc}"]
    subprocess.run([*cmd, str(path)], check=True)


def photo(path: Path, source: str, size: str, exif_local: str | None) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{source}=s={size}",
                    "-frames:v", "1", "-q:v", "12", "-fflags", "+bitexact", "-flags:v", "+bitexact",
                    str(path)], check=True)
    if exif_local:
        when = datetime.datetime.strptime(exif_local, "%Y%m%d%H%M%S").strftime("%Y:%m:%d %H:%M:%S")
        subprocess.run(["exiv2", "-M", "set Exif.Image.Make DJI",
                        "-M", f"set Exif.Photo.DateTimeOriginal {when}",
                        "-M", f"set Exif.Photo.DateTimeDigitized {when}", "mo", str(path)], check=True)


def thumb(src: Path, dst: Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-frames:v", "1",
                    "-vf", "scale=320:-2", "-q:v", "6", "-fflags", "+bitexact", "-flags:v", "+bitexact",
                    str(dst)], check=True)


def dji(stamp: str, n: int, ext: str) -> str:
    return f"DJI_{stamp}_{n:04d}_D.{ext}"


def main() -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    buceo, orig, send, thumbs = (OUT / d for d in ("buceo", "originales-120fps", "send", "thumbs"))
    for d in (buceo, orig, send, thumbs):
        d.mkdir(parents=True)

    clips = [  # (sello local, n.º, fuente lavfi, tamaño, segundos, fps)
        ("20250118101500", 1, "testsrc2", HD, 3, 10),
        ("20250118102300", 2, "smptehdbars", UHD, 2, 5),
        ("20250118104500", 4, "rgbtestsrc", HD, 2, 10),
        ("20250119111000", 5, "testsrc", HD, 3, 10),
        # Aún sin indexar en Jellyfin: sale en el manifiesto con jellyfin_item_id null.
        ("20250119113000", 8, "yuvtestsrc", HD, 2, 10),
    ]
    for stamp, n, src, size, secs, fps in clips:
        video(buceo / dji(stamp, n, "MP4"), src, size, secs, fps, utc_of(stamp))
    # creation_time 0: se queda fuera del manifiesto por falta de fecha.
    video(buceo / dji("20250119120000", 9, "MP4"), "pal100bars", HD, 2, 10, None)
    # Fuera del patrón de cámara, pero con fecha.
    video(buceo / "GOPR0042.MP4", "smptebars", HD, 2, 10, utc_of("20250119123000"))
    shutil.copyfile(buceo / dji("20250118101500", 1, "MP4"), buceo / "DJI_20250118101500_0001_D(1).MP4")
    photo(buceo / dji("20250118103000", 3, "JPG"), "testsrc2", "4000x3000", "20250118103000")
    photo(buceo / dji("20250119111500", 6, "JPG"), "smptebars", "4000x3000", "20250119111500")
    # Día sin vídeos: toma el desfase del día con vídeos más cercano.
    photo(buceo / dji("20250120090000", 7, "JPG"), "colorspectrum", "4000x3000", "20250120090000")
    (buceo / dji("20250118101500", 1, "SRT")).write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nprofundidad 12 m\n\n")

    # El master a 120 fps de la conversión 0004, y uno sin conversión (sin miniatura).
    video(orig / dji("20250118104500", 4, "MP4"), "rgbtestsrc", HD, 1, 120, utc_of("20250118104500"))
    video(orig / dji("20250119114500", 10, "MP4"), "testsrc2", HD, 1, 120, utc_of("20250119114500"))

    video(send / "resumen-viaje.mp4", "testsrc2", "1280x720", 3, 10, "2025-01-21T18:00:00Z")
    photo(send / "foto-grupo.jpg", "rgbtestsrc", "1600x1200", "20250119180000")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "smptebars=s=800x600",
                    "-frames:v", "1", "-fflags", "+bitexact", str(send / "cartel.png")], check=True)

    for f in sorted(buceo.iterdir()):
        if f.suffix in (".MP4", ".JPG"):
            thumb(f, thumbs / f"{f.stem}.jpg")

    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"{total / 1e6:.2f} MB en {OUT}")


if __name__ == "__main__":
    main()
