# dbx-sync-manager

TUI for Dropbox selective sync. Lists every folder in the team space with its size; bold = synced, gray = excluded. Toggles exclusion via `dropbox-cli exclude`.

Icons (need a [Nerd Font](https://www.nerdfonts.com)):  folder and everything inside synced,  synced but has an excluded folder inside,  excluded (or inside an excluded folder).

## Setup

1. Create a Dropbox app at https://www.dropbox.com/developers/apps: *Scoped access*, *Full Dropbox*. On the **Permissions** tab enable `files.metadata.read` and `account_info.read`, then submit. Copy the **App key** (no secret needed).
2. Install as a global command (into `~/.local/bin`; run `uv tool update-shell` if it isn't on `PATH`):
   ```bash
   uv tool install git+ssh://git@github.com/mahardhitama/dbx-sync-manager-tui.git
   # or from a checkout: uv tool install .   (add -e to pick up code changes)
   ```
   Upgrade with `uv tool upgrade dbx-sync-manager`. To run from a checkout without installing: `uv run dbx-sync-manager`.
3. Run (first run asks you to authorize in the browser and paste the code):
   ```bash
   DBX_APP_KEY=<app key> dbx-sync-manager
   ```

The first run crawls the whole team space (can take minutes); later runs start from the cache and fetch only changes.

## Keys

| Key | Action |
|-----|--------|
| `h` `j` `k` `l` / arrows | navigate |
| `g` / `G` | top / bottom |
| `space` | toggle sync (asks first) |
| `s` | sort by size (default) / name |
| `r` | refresh (changes only) |
| `R` | full recrawl |
| `q` | quit |

## Files

- `~/.config/dbx-sync-manager/token.json`: refresh token (mode 0600). Delete to log in again.
- `~/.cache/dbx-sync-manager/tree.json`: folder tree cache. Delete or press `R` to rebuild.

## Tests

```bash
uv run pytest
```
