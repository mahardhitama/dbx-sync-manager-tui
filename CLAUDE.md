# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Textual TUI that browses every folder in the Dropbox Business team space (with sizes) and toggles selective sync. Design: `docs/superpowers/specs/2026-10-24-dropbox-sync-manager-design.md`.

## Commands

```bash
uv sync                                                  # setup (dev group included)
uv run pytest -q                                         # all tests
uv run pytest tests/test_tree.py::test_size_rollup -v    # single test
DBX_APP_KEY=<key> uv run dbx-sync-manager                # run (needs a Dropbox app key, see README)
```

## Architecture

Two independent data sources, joined only in `app.py`:

- **Remote tree (`tree.py`)**: one recursive `files_list_folder("")` crawl, then `files_list_folder_continue(cursor)` deltas. Cached at `~/.cache/dbx-sync-manager/tree.json`. Keys are lowercase team paths (`/projects/2023`, root is `""`); display names come from `path_display`. Per-file sizes are stored because `DeletedMetadata` has no size, so deletions must subtract the recorded value. Folder sizes are rolled up lazily and the cache is cleared on `apply`.
- **Exclusion list (`sync.py`)**: shells out to `dropbox-cli exclude list|add|remove`, run with `cwd=<local root>`. `dropbox-cli` prints errors to stdout with exit 0, so:
  - `parse_exclude_list` raises `RuntimeError` on any output not headed `Excluded:`.
  - Toggle success is verified by re-reading the list, never by output text.
  - The local root is `business.root_path` in `~/.dropbox/info.json`.
- **Auth (`auth.py`)**: PKCE with a refresh token in `~/.config/dbx-sync-manager/token.json` (0600). The client must be scoped with `with_path_root(PathRoot.root(root_ns))`, or it only sees the personal folder. Login requests only `files.metadata.read` + `account_info.read`; if team scopes are requested, Dropbox issues a team-linked token and `users_get_current_account` fails.

`app.py` details that aren't obvious:
- Crawl and refresh run in thread workers on a `copy.deepcopy` of the tree, then swap it in via `call_from_thread`. Never mutate `folder_tree` from a worker.
- Startup makes no network call; the token is first checked by the background refresh. On `AuthError`, the app exits with `"relogin"` and `main()` reruns `login()` before restarting it.
- `r`/`R` are ignored while a load runs (a `loading` flag, not exclusive workers).
- The attribute is `folder_tree`, not `tree`: `App.tree` is a Textual property. Check new private names against `App` attributes (`_display` already collided).
- `poll_status` re-reads `dropbox-cli status` and the exclude list every 5 s.
- Folder state is `sync.state()` → `synced | excluded | parent_excluded`. The row style and Nerd Font icon (`icon()`) derive from it, plus `has_excluded_inside` for the half circle.

## Testing

- `tests/fakes.py` builds real SDK metadata objects and provides `FakeDbx`, which chains crawl pages by cursors `c0, c1, …` and serves deltas after the last one.
- App tests monkeypatch `sync.exclude_list/set_excluded/status` (the `daemon` fixture) and drive the UI with `run_test()`.
- A crawl that finishes instantly can hide UI-state bugs; use a gated fake (`GatedDbx`) when testing progress screens.
- Never run `dropbox-cli exclude add|remove` against the real daemon in tests or checks. It deletes or downloads real files.
