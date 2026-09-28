import datetime
import os
from pathlib import Path

from media_management.media_meta import FileMeta, day_offsets, effective_date, extract, photo_meta, video_meta
from tests.media_factory import make_photo, make_video, needs_ffmpeg


@needs_ffmpeg
def test_video_date_duration_and_size_from_mvhd(tmp_path: Path) -> None:
    v = make_video(tmp_path / "DJI_20310310104500_0001_D.MP4", "2031-03-10T09:45:01Z", seconds=2)
    assert video_meta(str(v)) == {"captured_at_utc": "2031-03-10T09:45:01Z", "duration_s": 2.0,
                                  "width": 64, "height": 36}


@needs_ffmpeg
def test_video_without_creation_time_has_no_date(tmp_path: Path) -> None:
    v = make_video(tmp_path / "x.MP4", None)
    assert "captured_at_utc" not in video_meta(str(v))


@needs_ffmpeg
def test_photo_local_time_and_size_from_exif(tmp_path: Path) -> None:
    p = make_photo(tmp_path / "x.JPG", datetime.datetime(2031, 3, 10, 11, 0, 0))
    assert photo_meta(str(p)) == {"captured_at_local": datetime.datetime(2031, 3, 10, 11, 0, 0),
                                  "width": 80, "height": 60}


def test_unreadable_file_is_reported_not_fatal(tmp_path: Path) -> None:
    bad = tmp_path / "roto.MP4"
    bad.write_bytes(b"\x00\x00\x00\x10moov\x00\x00\x00\x08mvhd")
    meta = extract(str(bad), bad.name, "video", "dji", 16, 0)
    assert meta.captured_at_utc is None


def _video(name: str, utc: str) -> FileMeta:
    return FileMeta(name=name, kind="video", size_bytes=1, mtime_ns=0, captured_at_utc=utc)


def test_photo_offset_comes_from_videos_of_the_same_day() -> None:
    files = [_video("DJI_20310310104500_0001_D.MP4", "2031-03-10T09:45:00Z"),
             _video("DJI_20310310120000_0002_D.MP4", "2031-03-10T11:00:00Z"),
             _video("DJI_20310720090000_0003_D.MP4", "2031-07-20T07:00:00Z")]
    offsets = day_offsets(files)
    assert offsets == {datetime.date(2031, 3, 10): 3600, datetime.date(2031, 7, 20): 7200}
    winter = FileMeta(name="DJI_20310310110000_0004_D.JPG", kind="photo", size_bytes=1, mtime_ns=0,
                      captured_at_local=datetime.datetime(2031, 3, 10, 11, 0))
    summer = FileMeta(name="DJI_20310721100000_0005_D.JPG", kind="photo", size_bytes=1, mtime_ns=0,
                      captured_at_local=datetime.datetime(2031, 7, 21, 10, 0))
    assert effective_date(winter, offsets) == ("2031-03-10T10:00:00Z", "exif+desfase_de_2031-03-10")
    assert effective_date(summer, offsets) == ("2031-07-21T08:00:00Z", "exif+desfase_de_2031-07-20")


def test_photo_without_any_video_has_no_date() -> None:
    photo = FileMeta(name="DJI_20310310110000_0004_D.JPG", kind="photo", size_bytes=1, mtime_ns=0,
                     captured_at_local=datetime.datetime(2031, 3, 10, 11, 0))
    assert effective_date(photo, {}) == (None, None)


@needs_ffmpeg
def test_generic_profile_falls_back_to_exif_then_mtime(tmp_path: Path) -> None:
    photo = make_photo(tmp_path / "cena.jpg", datetime.datetime(2031, 1, 15, 21, 30))
    meta = extract(str(photo), photo.name, "photo", "generic", 1, 0)
    assert (meta.captured_at_utc, meta.captured_at_source) == ("2031-01-15T20:30:00Z", "exif_hora_local_madrid")
    doc = tmp_path / "notas.pdf"
    doc.write_bytes(b"%PDF")
    os.utime(doc, (1_900_000_000, 1_900_000_000))
    meta = extract(str(doc), doc.name, "other", "generic", 4, doc.stat().st_mtime_ns)
    assert (meta.captured_at_utc, meta.captured_at_source) == ("2030-03-17T17:46:40Z", "mtime")


@needs_ffmpeg
def test_dji_profile_does_not_invent_dates(tmp_path: Path) -> None:
    v = make_video(tmp_path / "DJI_20310310104500_0001_D.MP4", None)
    meta = extract(str(v), v.name, "video", "dji", v.stat().st_size, v.stat().st_mtime_ns)
    assert meta.captured_at_utc is None and meta.captured_at_source is None
