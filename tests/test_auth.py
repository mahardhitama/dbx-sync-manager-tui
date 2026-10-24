import stat

import pytest

from dbx_sync_manager import auth

from dbx_sync_manager.auth import load_creds, save_creds


def test_creds_roundtrip_mode(tmp_path):
    f = tmp_path / "d" / "token.json"
    save_creds({"app_key": "k", "refresh_token": "r", "root_ns": "1"}, f)
    assert load_creds(f)["refresh_token"] == "r"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_load_missing(tmp_path):
    assert load_creds(tmp_path / "none.json") is None


def test_login_requests_only_user_scopes(monkeypatch):
    seen = {}

    class Flow:
        def __init__(self, app_key, **kw):
            seen.update(kw)
            raise SystemExit  # stop before any network call

    monkeypatch.setattr(auth.dropbox, "DropboxOAuth2FlowNoRedirect", Flow)
    monkeypatch.setenv("DBX_APP_KEY", "k")
    with pytest.raises(SystemExit):
        auth.login()
    assert seen["scope"] == ["files.metadata.read", "account_info.read"]
