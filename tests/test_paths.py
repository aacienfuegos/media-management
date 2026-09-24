import os
from pathlib import Path

import pytest

from media_management.roots import PathRejected, load_roots, media_problem, resolve_in_root
from tests.conftest import Env, make_settings


@pytest.mark.parametrize("bad", [
    "../fuera.txt", "sub/../../fuera.txt", "/etc/passwd", "", ".", "sub/./a", "a\x00b",
    "..", "sub//a",
])
def test_rejects_traversal_and_absolute(env: Env, bad: str) -> None:
    root = load_roots(env.settings)["send"]
    with pytest.raises(PathRejected):
        resolve_in_root(root, bad)


def test_percent_encoded_dots_are_just_a_name(env: Env) -> None:
    root = load_roots(env.settings)["send"]
    (env.root("send") / "%2e%2e").mkdir()
    (env.root("send") / "%2e%2e" / "a.txt").write_text("x")
    assert resolve_in_root(root, "%2e%2e/a.txt") == env.root("send") / "%2e%2e" / "a.txt"


def test_symlink_leaving_the_root_is_rejected(env: Env) -> None:
    root = load_roots(env.settings)["send"]
    secret = env.base.parent / "pasaporte.jpg"
    secret.write_text("secreto")
    os.symlink(secret, env.root("send") / "foto.jpg")
    os.symlink(env.base.parent, env.root("send") / "arriba")
    with pytest.raises(PathRejected):
        resolve_in_root(root, "foto.jpg")
    with pytest.raises(PathRejected):
        resolve_in_root(root, "arriba/pasaporte.jpg")


def test_symlink_to_sibling_root_with_common_prefix_is_rejected(tmp_path: Path) -> None:
    base = tmp_path
    settings = make_settings(base)
    root = load_roots(settings)["send"]
    evil = settings.media_base / "send-privado"
    evil.mkdir()
    (evil / "x.txt").write_text("x")
    os.symlink(evil / "x.txt", settings.media_base / "send" / "x.txt")
    with pytest.raises(PathRejected):
        resolve_in_root(root, "x.txt")


def test_legit_paths_resolve(env: Env) -> None:
    root = load_roots(env.settings)["send"]
    (env.root("send") / "viaje").mkdir()
    (env.root("send") / "viaje" / "a b ñ.mp4").write_text("x")
    (env.root("send") / "enlace.mp4").symlink_to(env.root("send") / "viaje" / "a b ñ.mp4")
    assert resolve_in_root(root, "viaje/a b ñ.mp4") == env.root("send") / "viaje" / "a b ñ.mp4"
    assert resolve_in_root(root, "enlace.mp4") == env.root("send") / "viaje" / "a b ñ.mp4"


def test_missing_sentinel_means_not_mounted(env: Env) -> None:
    roots = load_roots(env.settings)
    assert media_problem(env.settings, roots) is None
    env.settings.sentinel.unlink()
    problem = media_problem(env.settings, roots)
    assert problem is not None and "centinela" in problem


def test_roots_outside_base_or_overlapping_are_rejected(tmp_path: Path) -> None:
    base = tmp_path
    settings = make_settings(base)
    settings.roots_file.write_text(
        f'[[roots]]\nname = "a"\npath = "{base}/otra"\n')
    with pytest.raises(ValueError, match="dentro de"):
        load_roots(settings)
    settings.roots_file.write_text(
        f'[[roots]]\nname = "a"\npath = "{settings.media_base}/x"\n'
        f'[[roots]]\nname = "b"\npath = "{settings.media_base}/x/y"\n')
    with pytest.raises(ValueError, match="solapan"):
        load_roots(settings)
    settings.roots_file.write_text(
        f'[[roots]]\nname = "a"\npath = "{settings.media_base}/x"\nsynced = true\nrenamable = true\n')
    with pytest.raises(ValueError, match="no se puede renombrar"):
        load_roots(settings)
