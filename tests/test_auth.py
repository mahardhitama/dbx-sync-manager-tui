import stat

from dbx_sync_manager.auth import load_creds, save_creds


def test_creds_roundtrip_mode(tmp_path):
    f = tmp_path / "d" / "token.json"
    save_creds({"app_key": "k", "refresh_token": "r", "root_ns": "1"}, f)
    assert load_creds(f)["refresh_token"] == "r"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_load_missing(tmp_path):
    assert load_creds(tmp_path / "none.json") is None
