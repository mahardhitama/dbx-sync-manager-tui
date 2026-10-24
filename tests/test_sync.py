import subprocess
from pathlib import Path

import pytest

from dbx_sync_manager.sync import local_root, parse_exclude_list, set_excluded, state

ROOT = Path("/home/u/Hexa Conservation Dropbox")
REAL = ("Excluded: \n../../Hexa Conservation Dropbox/ARCHIVES\n../../Hexa Conservation Dropbox/AREA - HR\n"
        "../../Hexa Conservation Dropbox/PROJECTS/Données\n")


def test_parse_real_output():
    got = parse_exclude_list(REAL, Path("/home/u/Work/x"), ROOT)
    assert got == {"/archives", "/area - hr", "/projects/données"}


def test_parse_none_ignored():
    assert parse_exclude_list("No directories are being ignored.\n", ROOT, ROOT) == set()


def test_state():
    ex = {"/projects"}
    assert state("/projects", ex) == "excluded"
    assert state("/projects/2023", ex) == "parent_excluded"
    assert state("/projectsx", ex) == "synced"
    assert state("", ex) == "synced"


def test_local_root(tmp_path):
    f = tmp_path / "info.json"
    f.write_text('{"business": {"root_path": "/home/u/Hexa Conservation Dropbox"}}')
    assert local_root(f) == ROOT


def test_set_excluded_command(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append(args) or subprocess.CompletedProcess(args, 0, "Excluded: \n", ""))
    set_excluded(ROOT, "/PROJECTS/2023", True)
    set_excluded(ROOT, "/PROJECTS/2023", False)
    assert calls == [["dropbox-cli", "exclude", "add", str(ROOT) + "/PROJECTS/2023"],
                     ["dropbox-cli", "exclude", "remove", str(ROOT) + "/PROJECTS/2023"]]


def test_parse_daemon_error_raises():
    with pytest.raises(RuntimeError, match="Dropbox isn't running!"):
        parse_exclude_list("Dropbox isn't running!\n", ROOT, ROOT)


def test_run_timeout_returns_message(monkeypatch):
    def hang(args, **kw):
        assert kw["timeout"]
        raise subprocess.TimeoutExpired(args, kw["timeout"])
    monkeypatch.setattr(subprocess, "run", hang)
    assert "timed out" in set_excluded(ROOT, "/A", True)
