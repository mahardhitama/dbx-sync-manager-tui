# Dropbox Sync Manager — Design

Date: 2026-10-24
Status: Approved (design), pending spec review

## Goal

A personal TUI for browsing every folder in the Hexa Conservation Dropbox team space and toggling Dropbox selective sync (the exclusion list) on any folder at any depth. Today the local filesystem only shows synced folders, so there is no way to see what exists remotely or how big it is.

Success: from one screen, see all remote folders with their sizes, see which are synced, and flip sync for a folder with a keypress.

## Requirements

From the user:

- Yazi-style file-manager browsing of all Dropbox folders (remote, not only local).
- Folders only; files are never listed.
- Synced folders (not in the exclusion list) render **bold**; excluded folders render light gray.
- Toggle any folder, at any depth, in or out of the exclusion list. Excluded folders do not sync.
- Show folder sizes.
- `r` refreshes the cache (delta), `R` rebuilds it from scratch.

Assumptions:

- Single user, local machine, Linux, official Dropbox daemon (`/opt/dropbox`, `dropbox-cli` 2026.09.28).
- The app is a front end over `dropbox-cli exclude`; it does not sync files itself.
- Account is Dropbox Business; team root is read from `~/.dropbox/info.json` (`business.root_path`, currently `/home/prasetya/Hexa Conservation Dropbox`).

Constraints:

- The daemon's local databases are encrypted, so remote listing requires the Dropbox HTTP API with a user-created app key (PKCE, no secret). A team admin may block third-party apps; if so, remote listing is not possible.
- Dropbox only allows excluding a folder whose parent is synced. A folder inside an excluded parent cannot be toggled on its own.
- Re-including a folder triggers a download of its full size.

## Architecture

Python 3.14 + Textual, packaged with `pyproject.toml`, entry point `dbx-sync-manager`, installed into a project `.venv`. Dependencies: `textual`, `dropbox`.

```
dbx_sync_manager/
  auth.py    PKCE OAuth, token storage, team path-root client
  tree.py    crawl, delta, cache, size rollup (no UI, no subprocess)
  sync.py    dropbox-cli exclude list/add/remove, path mapping
  app.py     Textual UI; depends on tree.py and sync.py only
```

### auth.py

- App key from `$DBX_APP_KEY`; if unset, prompt once on first run and store it alongside the token.
- PKCE flow via `dropbox.DropboxOAuth2FlowNoRedirect(app_key, use_pkce=True, token_access_type="offline")`: print the authorize URL, read the pasted code.
- Store the refresh token and app key in `~/.config/dbx-sync-manager/token.json`, file mode 0600.
- Build the client with the refresh token, then scope it to the team space: `dbx.with_path_root(PathRoot.root(account.root_info.root_namespace_id))` using `users_get_current_account()`.
- Required app scopes: `files.metadata.read`, `account_info.read`.

### tree.py

- One recursive crawl: `files_list_folder("", recursive=True)` then `files_list_folder_continue(cursor)` until `has_more` is false.
- In-memory model, keyed by `path_lower`:
  - `folders: dict[path_lower, {"name": path_display, "files": {name_lower: size}}]`
  - Children are derived from the folder keys (parent of each path).
- Per-file sizes are kept because `DeletedMetadata` in a delta carries no size; a deletion must subtract the size previously recorded.
- Size rollup: a folder's size is its own files plus all descendant folders, computed once after load or after a delta, not stored in the cache.
- Delta (`r`): `files_list_folder_continue(saved_cursor)`, applying:
  - `FolderMetadata` → add folder if missing.
  - `FileMetadata` → set `files[name] = size` in its parent folder.
  - `DeletedMetadata` → if the path is a known folder, drop it and its whole subtree; else drop the file entry from its parent.
  - Moves and renames arrive as delete + add and need no special handling.
- Full rebuild (`R`): discard state and crawl again.
- Cache: `~/.cache/dbx-sync-manager/tree.json` holding `{cursor, crawled_at, folders}`. Written atomically (write temp file, `os.replace`). Written only after a crawl or delta completes, so an interrupted crawl leaves the previous cache intact.
- Startup: load the cache if present and valid and apply a delta; otherwise crawl.
- Path comparison always uses lowercase paths; `path_display` is used for rendering.

### sync.py

- Local root from `~/.dropbox/info.json` → `business.root_path`.
- `exclude_list()`: run `dropbox-cli exclude list`, skip the `Excluded:` header, resolve each line (paths are printed relative to the current directory) to an absolute path, strip the local root, and return a set of lowercase team paths (e.g. `/projects/2023`).
- `state(path, excluded) -> "synced" | "excluded" | "parent_excluded"`: `excluded` if the path is in the set, `parent_excluded` if any ancestor is, else `synced`.
- `exclude(path)` / `include(path)`: run `dropbox-cli exclude add|remove "<local_root><path_display>"`; return success and stderr.
- `status()`: first line of `dropbox-cli status`.
- Exclude entries with no matching remote folder are ignored.

### app.py

```
┌ Hexa Conservation Dropbox / PROJECTS ──────────────────────────────┐
│ ARCHIVES        │ ▸ 2023              12.4 GB │ Field Data   3.1 GB │
│ AREA - Finance  │ ▸ 2024               8.0 GB │ Reports      0.9 GB │
│▶PROJECTS        │ ▶ 2025              41.2 GB │ Maps        37.2 GB │
│ RESOURCES       │ ▸ Templates        120 MB   │                     │
├─────────────────────────────────────────────────────────────────────┤
│ synced 61.7 GB / total 412 GB · cache 3m ago · Dropbox: Up to date  │
└─────────────────────────────────────────────────────────────────────┘
```

- Three Miller columns: parent, current, preview of the selected folder's children. Folders only, each with a human-readable size, sorted by name by default.
- Styles:
  - `synced`: bold.
  - `excluded`: light gray.
  - `parent_excluded`: light gray, dimmed; not toggleable.
- Status bar: total synced size / total size, cache age, `dropbox-cli status` (polled every 5 s).
- Keys:

  | Key | Action |
  |-----|--------|
  | `h` `j` `k` `l`, arrows | navigate (up a level, down, up, into) |
  | `g` / `G` | top / bottom of column |
  | `space` | toggle exclusion for the selected folder |
  | `s` | toggle sort: name ↔ size (descending) |
  | `r` | apply delta |
  | `R` | full recrawl |
  | `q` | quit |

- Toggle always confirms:
  - Excluding: "Remove <size> local copy of <name>? (stays in cloud)"
  - Including: "Download <size> for <name>?"
  - `parent_excluded`: no dialog; toast "parent excluded: include <ancestor> first".
- After a toggle, re-read `exclude_list()` and redraw.
- First run and `R` show a progress screen with a running count of entries crawled. Crawl and delta run in a Textual worker so the UI stays responsive.

## Error handling

| Situation | Behavior |
|-----------|----------|
| Daemon not running / `dropbox-cli` fails | Red status bar message; toggling disabled. |
| Toggle command fails | Toast with `dropbox-cli` stderr verbatim; re-read exclude list. |
| `AuthError` | Re-run PKCE flow. |
| App blocked by team admin | Clear message naming the cause; exit. |
| Network error | Browse from cache; status bar shows "offline"; `r` retries. |
| `ListFolderContinueError.reset` | Automatic full recrawl. |
| Rate limiting | SDK `max_retries_on_rate_limit`. |
| Cache missing or unparseable | Full crawl. |
| Crawl interrupted | Nothing written; previous cache kept. |

## Testing

pytest.

- `tree.py`: fake SDK metadata objects (`FolderMetadata`, `FileMetadata`, `DeletedMetadata`) covering crawl, size rollup, delta file add, delta file delete, delta folder delete removing its subtree, move as delete + add, cache round-trip.
- `sync.py`: parse a fixture captured from real `dropbox-cli exclude list` output; path mapping; `state()` including ancestor exclusion; `exclude`/`include` with `subprocess` mocked.
- `app.py`: one Textual `run_test` pilot smoke test: navigate, toggle, confirm dialog appears, sync call issued.
- Manual: run against the real account.

## Risks

- The `dropbox` SDK may not install cleanly on Python 3.14. The first implementation task verifies this.
- The first crawl of a large team space may take minutes and produce a multi-MB cache file. Acceptable; it happens once.

## Out of scope

Listing files, file operations (copy/move/delete), multiple accounts, non-Business accounts, running as a daemon.
