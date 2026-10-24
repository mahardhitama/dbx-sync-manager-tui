import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dbx_sync_manager import sync
from dbx_sync_manager.sync import local_root, parse_exclude_list, set_excluded, state

ROOT = Path("/home/u/Hexa Conservation Dropbox")
CLI = "/usr/bin/dropbox-cli"
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


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name, path: CLI)


def test_set_excluded_command(monkeypatch, cli):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append(args) or subprocess.CompletedProcess(args, 0, "Excluded: \n", ""))
    set_excluded(ROOT, "/PROJECTS/2023", True)
    set_excluded(ROOT, "/PROJECTS/2023", False)
    assert calls == [[CLI, "exclude", "add", str(ROOT) + "/PROJECTS/2023"],
                     [CLI, "exclude", "remove", str(ROOT) + "/PROJECTS/2023"]]


@pytest.mark.parametrize("path", ["/..", "/../Dropbox (Personal)", "/A/./B", "/A//B", "/A/", "/", "", "/x\tFinance"])
def test_set_excluded_refuses_unsafe_path(monkeypatch, path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("dropbox-cli called"))
    assert set_excluded(ROOT, path, True).startswith("refused unsafe path")


def test_relative_path_entry_never_resolves_in_cwd(monkeypatch, tmp_path):
    stub = tmp_path / "dropbox-cli"
    stub.write_text("#!/bin/sh\n")
    stub.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep + ".")
    with pytest.raises(FileNotFoundError):
        sync.exclude_list(tmp_path)


def test_parse_daemon_error_raises():
    with pytest.raises(RuntimeError, match="Dropbox isn't running!"):
        parse_exclude_list("Dropbox isn't running!\n", ROOT, ROOT)
    with pytest.raises(RuntimeError, match=r"\\x1b\]52"):
        parse_exclude_list("\x1b]52;c;aGk=\x07", ROOT, ROOT)


def test_run_timeout_returns_message(monkeypatch, cli):
    def hang(args, **kw):
        assert kw["timeout"]
        raise subprocess.TimeoutExpired(args, kw["timeout"])
    monkeypatch.setattr(subprocess, "run", hang)
    assert "timed out" in set_excluded(ROOT, "/A", True)


def test_has_excluded_inside_any_depth():
    from dbx_sync_manager.sync import has_excluded_inside
    ex = {"/projects/2025/maps"}
    assert has_excluded_inside("/projects", ex)
    assert has_excluded_inside("", ex)
    assert not has_excluded_inside("/projects/2025/maps", ex)
    assert not has_excluded_inside("/proj", ex)
