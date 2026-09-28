import pytest

from media_management.settings import Settings


def test_empty_env_vars_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {"MM_ROOTS_FILE": "/r.toml", "MM_MEDIA_BASE": "/m", "MM_DATA_DIR": "/d",
                       "MM_SECRET_KEY": "k" * 40, "MM_MANIFEST_COMPARE_WITH": "",
                       "MM_JELLYFIN_IDS_FILE": "", "MM_MANIFEST_PATH": "",
                       "MM_PANEL_ALLOWED_USERS": '["andres"]'}.items():
        monkeypatch.setenv(key, value)
    s = Settings()  # type: ignore[call-arg]
    assert s.manifest_compare_with is None and s.jellyfin_ids_file is None and s.manifest_path is None
    assert s.panel_allowed_users == ["andres"]


def test_short_secret_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {"MM_ROOTS_FILE": "/r.toml", "MM_MEDIA_BASE": "/m", "MM_DATA_DIR": "/d",
                       "MM_SECRET_KEY": "corta"}.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        Settings()  # type: ignore[call-arg]
