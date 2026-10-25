import asyncio
import threading
from pathlib import Path

import pytest
import requests
from dropbox.exceptions import AuthError

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
        await pilot.pause(ConfirmScreen.ARM_DELAY)
        await pilot.press("y")
        await pilot.pause()
    assert daemon["calls"] == [("/A", False)]


async def test_toggle_failure_shows_output(daemon, tmp_path):
    daemon["output"], daemon["apply"] = "Dropbox isn't running!", False
    app = make_app(tmp_path)
    shown = []
    app.notify = lambda msg, **kw: shown.append(msg)
    async with app.run_test() as pilot:
        await pilot.press("space")
        await pilot.pause(ConfirmScreen.ARM_DELAY)
        await pilot.press("y")
        await pilot.pause()
    assert any("Dropbox isn't running!" in m for m in shown)


async def test_confirm_ignores_y_sent_with_opening_key(daemon, tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("space", "y")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
    assert daemon["calls"] == []


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


class RevokedDbx(FakeDbx):
    def files_list_folder_continue(self, cursor):
        raise AuthError("req", None)


async def test_rejected_token_exits_to_relogin(daemon, tmp_path):
    t = crawl(FakeDbx([[folder("/A")]]))
    app = DbxApp(RevokedDbx([[folder("/A")]]), t, Path("/r"), tmp_path / "t.json")
    assert await asyncio.wait_for(app.run_async(headless=True), 5) == "relogin"


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
        await pilot.pause(ConfirmScreen.ARM_DELAY)
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


def test_icon():
    from dbx_sync_manager.app import icon
    ex = {"/a", "/b/c"}
    assert icon("/a", ex) == ""      # excluded
    assert icon("/a/x", ex) == ""    # inside excluded parent
    assert icon("/b", ex) == ""      # excluded folder inside
    assert icon("/d", ex) == ""      # fully synced


async def test_footer_shows_shortcuts(daemon, tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(120, 20)) as pilot:
        await pilot.pause()
        shown = {k.description for k in app.query("FooterKey")}
        assert {"Toggle sync", "Sort", "Refresh", "Recrawl", "Quit"} <= shown


async def test_columns_fit_widest_name(daemon, tmp_path):
    long = "a-much-longer-folder-name"
    app = make_app(tmp_path, FakeDbx([[file("/A/B/x", 1), file(f"/{long}/x", 1)]]))
    async with app.run_test(size=(200, 20)) as pilot:
        await pilot.pause()
        cur, pre = app.query_one("#current"), app.query_one("#preview")
        # border 2 + padding 2 + "icon name" + gap 2 + "1 B" (+ scrollbar, if any)
        assert len(long) + 11 <= cur.outer_size.width <= len(long) + 13
        assert pre.outer_size.width < cur.outer_size.width
        assert cur.region.right <= 200


async def test_columns_show_every_level_that_fits(daemon, tmp_path):
    app = make_app(tmp_path, FakeDbx([[file("/A/B/C/D/E/x", 1)]]))
    async with app.run_test(size=(200, 20)) as pilot:
        for _ in range(4):
            await pilot.press("l")
        await pilot.pause()
        shown = lambda: [ol.get_option_at_index(0).id for ol in app.query(".ancestor") if ol.display]
        assert shown() == ["/a", "/a/b", "/a/b/c", "/a/b/c/d"]
        assert app.query_one("#current").get_option_at_index(0).id == "/a/b/c/d/e"
        await pilot.resize_terminal(30, 20)  # columns are 12 wide, preview 4: room for one ancestor
        await pilot.pause()
        assert shown() == ["/a/b/c/d"]
        assert all(ol.region.right <= 30 for ol in app.query("#cols OptionList") if ol.display)
        await pilot.resize_terminal(200, 20)
        await pilot.pause()
        assert shown() == ["/a", "/a/b", "/a/b/c", "/a/b/c/d"]
        await pilot.press("h", "h")
        await pilot.pause()
        assert shown() == ["/a", "/a/b"]
