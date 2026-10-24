import threading
from pathlib import Path

import pytest
import requests

from dbx_sync_manager import sync
from dbx_sync_manager.app import ConfirmScreen, DbxApp, human, synced_size
from dbx_sync_manager.tree import Tree, crawl
from tests.fakes import FakeDbx, file, folder


def test_human():
    assert human(0) == "0 B"
    assert human(120_000_000) == "120 MB"
    assert human(41_200_000_000) == "41.2 GB"


def test_synced_size():
    t = Tree()
    t.apply([file("/A/x", 10), file("/A/B/y", 5), file("/C/z", 1)])
    assert synced_size(t, {"/a", "/a/b"}) == 1


@pytest.fixture
def daemon(monkeypatch):
    """Fake dropbox-cli: `excluded` is the daemon's state; `calls` records toggles."""
    d = {"excluded": {"/a"}, "calls": [], "output": "No longer excluded:", "apply": True}
    monkeypatch.setattr(sync, "exclude_list", lambda root: set(d["excluded"]))
    monkeypatch.setattr(sync, "status", lambda: "Up to date")

    def fake_set(root, path, exclude):
        d["calls"].append((path, exclude))
        if d["apply"]:
            (d["excluded"].add if exclude else d["excluded"].discard)(path.lower())
        return d["output"]

    monkeypatch.setattr(sync, "set_excluded", fake_set)
    return d


def make_app(tmp_path, dbx=None):
    dbx = dbx or FakeDbx([[folder("/A"), file("/A/x", 10)]])
    return DbxApp(dbx, crawl(dbx), Path("/r"), tmp_path / "t.json")


async def test_toggle_include_confirms_and_calls(daemon, tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("space")
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("y")
        await pilot.pause()
    assert daemon["calls"] == [("/A", False)]


async def test_toggle_failure_shows_output(daemon, tmp_path):
    daemon["output"], daemon["apply"] = "Dropbox isn't running!", False
    app = make_app(tmp_path)
    shown = []
    app.notify = lambda msg, **kw: shown.append(msg)
    async with app.run_test() as pilot:
        await pilot.press("space", "y")
        await pilot.pause()
    assert any("Dropbox isn't running!" in m for m in shown)


class OfflineDbx(FakeDbx):
    def files_list_folder_continue(self, cursor):
        raise requests.exceptions.ConnectionError("down")


async def test_offline_keeps_cache(daemon, tmp_path):
    t = crawl(FakeDbx([[folder("/A")]]))
    app = DbxApp(OfflineDbx([[folder("/A")]]), t, Path("/r"), tmp_path / "t.json")
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "/a" in app.folder_tree.folders
        assert "offline" in str(app.query_one("#status").content)


class GatedDbx(FakeDbx):
    """Crawl blocks until `gate` is set, so the progress screen is really shown."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.gate = threading.Event()

    def files_list_folder(self, path, recursive=False):
        self.gate.wait(5)
        return super().files_list_folder(path, recursive)


async def test_focus_survives_recrawl(daemon, tmp_path):
    dbx = GatedDbx([[folder("/A"), folder("/B")]])
    dbx.gate.set()
    app = make_app(tmp_path, dbx)
    dbx.gate.clear()
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("R")
        await pilot.pause()
        dbx.gate.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("down")
        assert app.query_one("#current").highlighted == 1


async def test_toggle_after_folder_vanished(daemon, tmp_path):
    app = make_app(tmp_path)
    shown = []
    app.notify = lambda msg, **kw: shown.append(msg)
    async with app.run_test() as pilot:
        await pilot.press("space")
        app._loaded(crawl(FakeDbx([[folder("/B")]])))  # delta removed /A while the dialog is open
        await pilot.press("y")
        await pilot.pause()
    assert daemon["calls"] == [] and any("no longer exists" in m for m in shown)


async def test_poll_picks_up_external_exclusion_change(daemon, tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        daemon["excluded"].clear()
        app.poll_status()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.excluded == set()


async def test_default_sort_is_size_descending(daemon, tmp_path):
    app = make_app(tmp_path, FakeDbx([[file("/A/x", 1), file("/B/x", 9), folder("/C")]]))
    async with app.run_test():
        ol = app.query_one("#current")
        assert [ol.get_option_at_index(i).id for i in range(ol.option_count)] == ["/b", "/a", "/c"]
