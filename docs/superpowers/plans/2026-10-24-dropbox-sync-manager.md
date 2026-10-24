# Dropbox Sync Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Textual TUI that lists every folder in the Dropbox Business team space with sizes and toggles selective sync on any folder via `dropbox-cli exclude`.

**Architecture:** One recursive `files_list_folder` crawl builds a folder tree (with per-file sizes for delta correctness), cached as JSON and updated with cursor deltas. `sync.py` wraps `dropbox-cli` for the exclusion list. `app.py` renders three Miller columns and drives both.

**Tech Stack:** Python 3.14, `textual`, `dropbox` SDK, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-24-dropbox-sync-manager-design.md`

## Global Constraints

- Python 3.14 (`/usr/bin/python3`), project venv at `.venv`; no `uv`.
- Runtime dependencies only `textual` and `dropbox`. Dev: `pytest`, `pytest-asyncio`.
- Entry point `dbx-sync-manager` → `dbx_sync_manager.__main__:main`.
- Token file `~/.config/dbx-sync-manager/token.json`, mode 0600. Cache `~/.cache/dbx-sync-manager/tree.json`.
- Local root from `~/.dropbox/info.json` → `business.root_path`.
- Paths are compared lowercase (team-relative, e.g. `/projects/2023`; root is `""`). Display names come from `path_display`.
- Folders only; files are never shown.
- `dropbox-cli` prints errors to stdout and may exit 0; success is judged by re-reading the exclude list, not by exit code or output text.
- App scopes: `files.metadata.read`, `account_info.read`.

## Review Focus

- Case/Unicode mismatch between `dropbox-cli` paths and API `path_lower` (e.g. `AREA - HR` vs `/area - hr`, `Données`): must still match. Test in Task 3.
- Toggle while the daemon is down (`Dropbox isn't running!` on stdout): app must show that text, not claim success. Test in Task 5.
- Delta containing a file or folder whose ancestors were never seen: ancestors are created, no `KeyError`. Test in Task 1.
- Delta deleting a path that is already gone: no crash, no change. Test in Task 1.
- Network down at startup with a valid cache: app browses the cache and shows `offline`. Test in Task 5.

---

### Task 1: Scaffold and tree model

**Files:**
- Create: `pyproject.toml`, `dbx_sync_manager/__init__.py` (empty), `dbx_sync_manager/tree.py`
- Create: `tests/fakes.py`, `tests/test_tree.py`

**Interfaces:**
- Produces (`tree.py`):
  - `CACHE_FILE: Path = Path.home() / ".cache/dbx-sync-manager/tree.json"`
  - `class Tree` with attributes `cursor: str | None`, `crawled_at: float`, `folders: dict[str, dict]` where each value is `{"name": str, "files": dict[str, int]}`; root key `""` always present.
  - `Tree.apply(entries: Iterable[dropbox.files.Metadata]) -> None`
  - `Tree.children(path: str) -> list[str]`: lowercase paths of direct child folders.
  - `Tree.name(path: str) -> str`
  - `Tree.size(path: str) -> int`: own files plus all descendants.
  - `Tree.save(file: Path = CACHE_FILE) -> None`; `Tree.load(file: Path = CACHE_FILE) -> Tree | None` (None when missing or unparseable).
- Produces (`tests/fakes.py`): `folder(path_display) -> FolderMetadata`, `file(path_display, size) -> FileMetadata`, `deleted(path_display) -> DeletedMetadata` (real SDK classes; `path_lower = path_display.lower()`, `name` = last segment; FileMetadata needs `id`, `client_modified`, `server_modified`, `rev` of ≥9 hex chars, `size`).

- [ ] **Step 1: Scaffold.** Write `pyproject.toml` (setuptools, deps above, script entry, `[tool.pytest.ini_options] asyncio_mode = "auto"`). Run `python3 -m venv .venv && .venv/bin/pip install -e . pytest pytest-asyncio`. Expected: installs cleanly. If `dropbox` fails on 3.14, STOP and report (spec risk).

- [ ] **Step 2: Write failing tests in `tests/test_tree.py`**

```python
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
    t = build(); t.apply([deleted("/PROJECTS/a.pdf")])
    assert t.size("/projects") == 50

def test_delete_folder_removes_subtree():
    t = build(); t.apply([deleted("/PROJECTS")])
    assert t.children("") == [] and t.size("") == 7
    assert "/projects/2023" not in t.folders

def test_move_is_delete_plus_add():
    t = build(); t.apply([deleted("/PROJECTS/2023"), folder("/PROJECTS/2024"), file("/PROJECTS/2024/b.tif", 50)])
    assert t.children("/projects") == ["/projects/2024"]
    assert t.size("/projects") == 150

def test_missing_ancestors_created():
    t = Tree(); t.apply([file("/A/B/c.txt", 5)])
    assert t.children("") == ["/a"] and t.size("/a") == 5 and t.name("/a/b") == "B"

def test_delete_unknown_path_is_noop():
    t = build(); t.apply([deleted("/nope/x.txt"), deleted("/nope")])
    assert t.size("") == 157

def test_cache_roundtrip(tmp_path):
    t = build(); t.cursor = "c1"; t.save(tmp_path / "tree.json")
    u = Tree.load(tmp_path / "tree.json")
    assert u.cursor == "c1" and u.size("") == 157

def test_load_corrupt_returns_none(tmp_path):
    (tmp_path / "tree.json").write_text("{nope")
    assert Tree.load(tmp_path / "tree.json") is None
    assert Tree.load(tmp_path / "missing.json") is None
```

- [ ] **Step 3: Run** `.venv/bin/pytest tests/test_tree.py -v`. Expected: FAIL (import error).

- [ ] **Step 4: Implement `tree.py`.** Parent of a path is `path.rsplit("/", 1)[0]` (`""` for top level). Sizes: compute lazily into a `dict` cache, cleared by `apply`; one pass over folders sorted by depth descending, adding each folder's total into its parent. `save` writes `{cursor, crawled_at, folders}` to a temp file in the same dir, then `os.replace`; creates the parent dir.

- [ ] **Step 5: Run** `.venv/bin/pytest tests/test_tree.py -v`. Expected: all PASS.

- [ ] **Step 6: Commit** `git add -A && git commit -m "feat: tree model with size rollup and cache"`

---

### Task 2: Crawl and delta against the API

**Files:**
- Modify: `dbx_sync_manager/tree.py`
- Modify: `tests/fakes.py`, `tests/test_tree.py`

**Interfaces:**
- Consumes: `Tree`, fakes from Task 1.
- Produces (`tree.py`):
  - `crawl(dbx, on_progress: Callable[[int], None] = lambda n: None) -> Tree`: `files_list_folder("", recursive=True)` then `files_list_folder_continue` while `has_more`; calls `on_progress(total_entries_so_far)` after each page; sets `cursor` and `crawled_at = time.time()`.
  - `refresh(dbx, tree: Tree, on_progress=...) -> Tree`: continues from `tree.cursor` until `has_more` is false and returns the same tree updated. On `ApiError` whose `error` is a `ListFolderContinueError` with `is_reset()`, returns `crawl(dbx, on_progress)`. Other errors propagate.
- Produces (`tests/fakes.py`): `FakeDbx(pages: list[list[Metadata]], deltas: list[Metadata] = [], reset: bool = False)`. The crawl pages are chained by cursors `c0, c1, …`. Continuing from the last cursor returns `deltas` once (then empty), or raises the reset `ApiError` if `reset`. Returns real `ListFolderResult` objects.

- [ ] **Step 1: Write failing tests**

```python
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
    t = crawl(dbx); t.folders["/stale"] = {"name": "stale", "files": {}}
    assert "/stale" not in refresh(dbx, t).folders
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_tree.py -v`. Expected: the 3 new tests FAIL.
- [ ] **Step 3: Implement** `crawl`, `refresh` and `FakeDbx`.
- [ ] **Step 4: Run** `.venv/bin/pytest -v`. Expected: all PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: crawl and cursor delta"` (add `tests/fakes.py` if new).

---

### Task 3: Selective sync wrapper

**Files:**
- Create: `dbx_sync_manager/sync.py`, `tests/test_sync.py`

**Interfaces:**
- Produces (`sync.py`):
  - `local_root(info_file: Path = Path.home() / ".dropbox/info.json") -> Path`
  - `parse_exclude_list(output: str, cwd: Path, root: Path) -> set[str]`: skip lines that are blank or equal to `Excluded:` after `strip()`; resolve each other line with `os.path.normpath(cwd / line)`; keep only paths under `root`; return `"/" + relative.lower()`. `No directories are being ignored.` resolves outside root and is dropped naturally. Only strip the trailing newline from path lines, not spaces in names.
  - `exclude_list(root: Path) -> set[str]`: runs `dropbox-cli exclude list` with `cwd=root`.
  - `state(path: str, excluded: set[str]) -> Literal["synced", "excluded", "parent_excluded"]`
  - `set_excluded(root: Path, path_display: str, exclude: bool) -> str`: runs `dropbox-cli exclude add|remove <root + path_display>`; returns combined stdout+stderr stripped.
  - `status() -> str`: first line of `dropbox-cli status` (or `"dropbox-cli not found"` on `FileNotFoundError`).

- [ ] **Step 1: Write failing tests**

```python
ROOT = Path("/home/u/Hexa Conservation Dropbox")
REAL = "Excluded: \n../../Hexa Conservation Dropbox/ARCHIVES\n../../Hexa Conservation Dropbox/AREA - HR\n../../Hexa Conservation Dropbox/PROJECTS/Données\n"

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
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_sync.py -v`. Expected: FAIL (import error).
- [ ] **Step 3: Implement `sync.py`.** Use `subprocess.run(args, capture_output=True, text=True)`.
- [ ] **Step 4: Run** `.venv/bin/pytest -v`. Expected: all PASS.
- [ ] **Step 5: Sanity check against the real daemon:** `.venv/bin/python -c "from dbx_sync_manager import sync; r=sync.local_root(); print(sorted(sync.exclude_list(r)))"`. Expected: the 16 currently excluded top-level folders, lowercase.
- [ ] **Step 6: Commit** `git add -A && git commit -m "feat: dropbox-cli exclude wrapper"`

---

### Task 4: Auth

**Files:**
- Create: `dbx_sync_manager/auth.py`, `tests/test_auth.py`

**Interfaces:**
- Produces (`auth.py`):
  - `TOKEN_FILE: Path = Path.home() / ".config/dbx-sync-manager/token.json"`
  - `load_creds(file: Path = TOKEN_FILE) -> dict | None`: keys `app_key`, `refresh_token`, `root_ns`.
  - `save_creds(creds: dict, file: Path = TOKEN_FILE) -> None`: creates the dir and writes with mode 0600, opening via `os.open(..., 0o600)` so the file is never world-readable.
  - `make_client(creds: dict) -> dropbox.Dropbox`: `Dropbox(oauth2_refresh_token=..., app_key=..., max_retries_on_rate_limit=5).with_path_root(PathRoot.root(creds["root_ns"]))`. No network call, so it works offline.
  - `login() -> dict`: app key from `$DBX_APP_KEY` or `input()`. PKCE via `DropboxOAuth2FlowNoRedirect(app_key, use_pkce=True, token_access_type="offline")`: print the URL, `input()` the code, `finish()`. Then `users_get_current_account().root_info.root_namespace_id` → `root_ns`; `save_creds`; return creds. On any `ApiError`/`AuthError`/`BadInputException` during login, raise `SystemExit` with a message that includes the error and the line "If your team admin blocks third-party apps, remote listing is not possible."
  - `get_client() -> dropbox.Dropbox`: `load_creds()` or `login()`. Then validate with `users_get_current_account()`: on `AuthError`, `login()` again; on `requests.exceptions.ConnectionError`, return the client anyway (offline).

- [ ] **Step 1: Write failing tests**

```python
def test_creds_roundtrip_mode(tmp_path):
    f = tmp_path / "d" / "token.json"
    save_creds({"app_key": "k", "refresh_token": "r", "root_ns": "1"}, f)
    assert load_creds(f)["refresh_token"] == "r"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600

def test_load_missing(tmp_path):
    assert load_creds(tmp_path / "none.json") is None
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_auth.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement `auth.py`.**
- [ ] **Step 4: Run** `.venv/bin/pytest -v`. Expected: all PASS.
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat: PKCE auth with team path root"`

---

### Task 5: Textual app and entry point

**Files:**
- Create: `dbx_sync_manager/app.py`, `dbx_sync_manager/__main__.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `Tree`, `crawl`, `refresh`, `CACHE_FILE` (Task 1–2); `exclude_list`, `state`, `set_excluded`, `status` (Task 3; call them as `sync.<fn>` so tests can monkeypatch); `get_client` (Task 4).
- Produces (`app.py`):
  - `human(n: int) -> str`: base 1000; units `B KB MB GB TB`; bytes as an integer; otherwise one decimal below 100, integer at 100 or above.
  - `synced_size(tree: Tree, excluded: set[str]) -> int`: `tree.size("")` minus `tree.size(p)` for each `p` in `excluded` that is in `tree.folders` and has `state == "excluded"`.
  - `class DbxApp(App)`: `__init__(self, dbx, tree: Tree | None, root: Path, cache_file: Path = CACHE_FILE)`.
  - `class ConfirmScreen(ModalScreen[bool])`: message text, `y` → True, `n`/`escape` → False.
- Produces (`__main__.py`): `main()`: `DbxApp(auth.get_client(), Tree.load(), sync.local_root()).run()`.

Behavior, from the spec's `app.py` section and key table:
- Three `OptionList` columns (parent, current, preview) plus a status bar `Static`. Option labels are Rich `Text`: name left, `human(size)` right, styled `bold` (synced), `grey70` (excluded), `grey50 dim` (parent_excluded).
- State: `cwd: str` (start `""`), `sort_by_size: bool`. Title = `root.name` + display path of `cwd`.
- Keys: `h`/`left` up a level (the old cwd stays highlighted); `l`/`right` into the highlighted folder; `j`/`k`/arrows move; `g`/`G` jump to top/bottom; `space` toggle; `s` sort; `r` refresh; `R` recrawl; `q` quit.
- Toggle: `parent_excluded` → `notify("parent excluded: include <ancestor display name> first")`. Otherwise push `ConfirmScreen` with the spec's exact copy: `Remove <size> local copy of <name>? (stays in cloud)` / `Download <size> for <name>?`. On yes, call `sync.set_excluded`, then re-read `sync.exclude_list`. If the folder's state did not change, `notify(output, severity="error")`. Redraw.
- Data: on mount, if `tree is None`, run `crawl` in a `@work(thread=True, exclusive=True)` worker. While no tree exists (first run, or during `R`), show a centered `crawling… N entries` label in place of the columns (the spec's progress screen), updated via `call_from_thread`; otherwise render the cache immediately, then run `refresh` in the same kind of worker. `R` crawls; `r` refreshes. Save to `cache_file` after each success. On `requests.exceptions.ConnectionError`, keep the current tree and show `offline` in the status bar.
- Status bar: `synced {human(synced)} / total {human(total)} · cache {age} ago · Dropbox: {sync.status()}`, polled every 5 s via `set_interval` (call `status` inside a thread worker).

- [ ] **Step 1: Write failing tests** (monkeypatch `sync.exclude_list`, `sync.set_excluded` and `sync.status`; use `FakeDbx` and the `tmp_path` cache)

```python
def test_human():
    assert human(0) == "0 B"
    assert human(120_000_000) == "120 MB"
    assert human(41_200_000_000) == "41.2 GB"

def test_synced_size():
    t = Tree(); t.apply([file("/A/x", 10), file("/A/B/y", 5), file("/C/z", 1)])
    assert synced_size(t, {"/a", "/a/b"}) == 1

async def test_toggle_include_confirms_and_calls(monkeypatch, tmp_path):
    excluded = {"/a"}
    calls = []
    monkeypatch.setattr(sync, "exclude_list", lambda root: set(excluded))
    monkeypatch.setattr(sync, "status", lambda: "Up to date")
    def fake_set(root, path, exclude):
        calls.append((path, exclude)); excluded.discard("/a"); return "No longer excluded:"
    monkeypatch.setattr(sync, "set_excluded", fake_set)
    dbx = FakeDbx([[folder("/A"), file("/A/x", 10)]])
    app = DbxApp(dbx, crawl(dbx), Path("/r"), tmp_path / "t.json")
    async with app.run_test() as pilot:
        await pilot.press("space")
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("y")
    assert calls == [("/A", False)]

async def test_toggle_failure_shows_output(monkeypatch, tmp_path):
    # set_excluded returns "Dropbox isn't running!" and the exclude list is unchanged
    ...  # assert a notification containing "Dropbox isn't running!" was posted (app._notifications)

async def test_offline_keeps_cache(monkeypatch, tmp_path):
    # FakeDbx whose files_list_folder_continue raises requests.exceptions.ConnectionError
    ...  # after startup the tree still has "/a" and the status bar text contains "offline"
```

The last two follow the same setup as the toggle test. Write them out fully with the assertions named in the comments.

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_app.py -v`. Expected: FAIL (import error).
- [ ] **Step 3: Implement `app.py` and `__main__.py`.**
- [ ] **Step 4: Run** `.venv/bin/pytest -v`. Expected: all PASS.
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat: Miller-column TUI with sync toggle"`

---

### Task 6: Real-account check (manual, with the user)

- [ ] **Step 1:** The user creates a Dropbox app in the App Console (scoped access, full Dropbox, scopes `files.metadata.read` and `account_info.read`) and exports `DBX_APP_KEY`.
- [ ] **Step 2:** Run `.venv/bin/dbx-sync-manager`, complete PKCE, and watch the first crawl finish. Expected: top level shows the team folders, the 16 excluded ones in gray, sizes present.
- [ ] **Step 3:** Toggle one small nested folder inside a synced parent, out and back in. Expected: `dropbox-cli exclude list` reflects each change and the styling updates.
- [ ] **Step 4:** Quit and rerun. Expected: instant start from cache, then a delta. Press `r` and `R` once each.
- [ ] **Step 5:** Add a short `README.md` covering app creation, install and keys. Commit `git add -A && git commit -m "docs: readme"`.
