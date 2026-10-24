import json
import os
from pathlib import Path

import dropbox
import requests
from dropbox.common import PathRoot
from dropbox.exceptions import AuthError, DropboxException

TOKEN_FILE = Path.home() / ".config/dbx-sync-manager/token.json"


def load_creds(file=TOKEN_FILE):
    try:
        return json.loads(file.read_text())
    except (OSError, ValueError):
        return None


def save_creds(creds, file=TOKEN_FILE):
    file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(creds, f)


def make_client(creds):
    dbx = dropbox.Dropbox(oauth2_refresh_token=creds["refresh_token"], app_key=creds["app_key"],
                          max_retries_on_rate_limit=5)
    return dbx.with_path_root(PathRoot.root(creds["root_ns"]))


def login():
    app_key = os.environ.get("DBX_APP_KEY") or input("Dropbox app key: ").strip()
    flow = dropbox.DropboxOAuth2FlowNoRedirect(app_key, use_pkce=True, token_access_type="offline")
    print("1. Open:", flow.start())
    print("2. Click Allow, then copy the code.")
    try:
        result = flow.finish(input("3. Paste code: ").strip())
        dbx = dropbox.Dropbox(oauth2_refresh_token=result.refresh_token, app_key=app_key)
        root_ns = dbx.users_get_current_account().root_info.root_namespace_id
    except (DropboxException, requests.exceptions.HTTPError) as e:
        raise SystemExit(f"Dropbox login failed: {e}\n"
                         "If your team admin blocks third-party apps, remote listing is not possible.")
    creds = {"app_key": app_key, "refresh_token": result.refresh_token, "root_ns": root_ns}
    save_creds(creds)
    return creds


def get_client():
    dbx = make_client(load_creds() or login())
    try:
        dbx.users_get_current_account()
    except AuthError:
        dbx = make_client(login())
    except requests.exceptions.ConnectionError:
        pass  # offline: the app browses the cache
    return dbx
