import json
import os
import subprocess
import sys
from pathlib import Path

from media_management.jellyfin import load_ids_export

EXPORTER = Path(__file__).parent.parent / "deploy" / "host" / "jellyfin-ids-export.py"
FAKE_PCT = """
import os, sys
if os.environ.get("FAKE_FAIL"):
    sys.stderr.write("CT is not running\\n")
    sys.exit(2)
sys.stdout.write(open(os.environ["FAKE_ROWS"]).read())
"""


def run(tmp_path: Path, rows: str, fail: bool = False) -> tuple[int, Path]:
    (tmp_path / "rows.txt").write_text(rows)
    (tmp_path / "pct.py").write_text(FAKE_PCT)
    out = tmp_path / "jellyfin-ids.json"
    env = {**os.environ, "MM_EXPORT_CTID": "900", "MM_EXPORT_LIBRARY_DIRS": "/library/buceo",
           "MM_EXPORT_OUT": str(out), "MM_EXPORT_PCT": f"{sys.executable} {tmp_path / 'pct.py'}",
           "FAKE_ROWS": str(tmp_path / "rows.txt")}
    if fail:
        env["FAKE_FAIL"] = "1"
    r = subprocess.run([sys.executable, str(EXPORTER)], env=env, capture_output=True, text=True)
    return r.returncode, out


def test_exports_only_the_library_with_normalized_ids(tmp_path: Path) -> None:
    rows = ("0A1B2C3D-4E5F-6071-8293-A4B5C6D7E8F9|/library/buceo/A.MP4\n"
            "0123456789abcdef0123456789abcdef|/library/buceo/B.JPG\n"
            "ffffffffffffffffffffffffffffffff|/library/peliculas/C.mkv\n"
            "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee|/library/buceo-otra/D.MP4\n"
            "basura sin separador\n")
    code, out = run(tmp_path, rows)
    assert code == 0
    doc = json.loads(out.read_text())
    assert doc["items"] == {"/library/buceo/A.MP4": "0a1b2c3d4e5f60718293a4b5c6d7e8f9",
                            "/library/buceo/B.JPG": "0123456789abcdef0123456789abcdef"}
    status = load_ids_export(out, 3600)
    assert status.problem is None and status.ids is not None and len(status.ids) == 2


def test_failure_or_empty_keeps_previous_export(tmp_path: Path) -> None:
    code, out = run(tmp_path, "aaaa|/library/buceo/A.MP4\n")
    assert code == 0
    before = out.read_bytes()
    assert run(tmp_path, "", fail=True)[0] != 0
    assert out.read_bytes() == before
    assert run(tmp_path, "bbbb|/library/otra/X.MP4\n")[0] != 0
    assert out.read_bytes() == before
