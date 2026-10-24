import os

from dbx_sync_manager.tree import Tree, crawl, refresh
from tests.fakes import FakeDbx, deleted, file, folder


def build():
    t = Tree()
    t.apply([folder("/PROJECTS"), folder("/PROJECTS/2023"),
             file("/PROJECTS/a.pdf", 100), file("/PROJECTS/2023/b.tif", 50),
             file("/root.txt", 7)])
    return t


def test_children_and_names():
    t = build()
    assert t.children("") == ["/projects"]
    assert t.children("/projects") == ["/projects/2023"]
    assert t.name("/projects/2023") == "2023"


def test_size_rollup():
    t = build()
    assert t.size("/projects/2023") == 50
    assert t.size("/projects") == 150
    assert t.size("") == 157


def test_delete_file():
    t = build()
    t.apply([deleted("/PROJECTS/a.pdf")])
    assert t.size("/projects") == 50


def test_delete_folder_removes_subtree():
    t = build()
    t.apply([deleted("/PROJECTS")])
    assert t.children("") == [] and t.size("") == 7
    assert "/projects/2023" not in t.folders


def test_move_is_delete_plus_add():
    t = build()
    t.apply([deleted("/PROJECTS/2023"), folder("/PROJECTS/2024"), file("/PROJECTS/2024/b.tif", 50)])
    assert t.children("/projects") == ["/projects/2024"]
    assert t.size("/projects") == 150


def test_missing_ancestors_created():
    t = Tree()
    t.apply([file("/A/B/c.txt", 5)])
    assert t.children("") == ["/a"] and t.size("/a") == 5 and t.name("/a/b") == "B"


def test_delete_unknown_path_is_noop():
    t = build()
    t.apply([deleted("/nope/x.txt"), deleted("/nope")])
    assert t.size("") == 157


def test_cache_roundtrip(tmp_path):
    t = build()
    t.cursor = "c1"
    t.save(tmp_path / "tree.json")
    u = Tree.load(tmp_path / "tree.json")
    assert u.cursor == "c1" and u.size("") == 157


def test_cache_private_after_resave(tmp_path):
    old = os.umask(0o022)
    try:
        f = tmp_path / "cache" / "tree.json"
        build().save(f)
        f.chmod(0o644)
        f.with_suffix(".tmp").write_text("")  # leftover tmp from a crash
        build().save(f)
    finally:
        os.umask(old)
    assert f.stat().st_mode & 0o777 == 0o600 and f.parent.stat().st_mode & 0o777 == 0o700


def test_name_control_chars_escaped():
    t = Tree()
    t.apply([folder("/a\x1b]52;c;aGk=\x07\tb")])
    assert t.name(t.children("")[0]) == "a\\x1b]52;c;aGk=\\x07\\tb"


def test_load_corrupt_returns_none(tmp_path):
    (tmp_path / "tree.json").write_text("{nope")
    assert Tree.load(tmp_path / "tree.json") is None
    assert Tree.load(tmp_path / "missing.json") is None


def test_load_without_root_entry(tmp_path):
    (tmp_path / "tree.json").write_text('{"cursor": "c", "crawled_at": 0, "folders": {"/a": {"name": "A", "files": {}}}}')
    t = Tree.load(tmp_path / "tree.json")
    assert t.size("") == 0 and t.children("") == ["/a"]


def test_crawl_pages_and_progress():
    seen = []
    t = crawl(FakeDbx([[folder("/A")], [file("/A/x", 3)]]), seen.append)
    assert t.size("/a") == 3 and seen == [1, 2] and t.cursor


def test_refresh_applies_delta():
    dbx = FakeDbx([[folder("/A"), file("/A/x", 3)]], deltas=[file("/A/y", 4)])
    t = crawl(dbx)
    assert refresh(dbx, t).size("/a") == 7


def test_refresh_reset_recrawls():
    dbx = FakeDbx([[folder("/A")]], reset=True)
    t = crawl(dbx)
    t.folders["/stale"] = {"name": "stale", "files": {}}
    assert "/stale" not in refresh(dbx, t).folders
