import copy
import time

import requests
from dropbox.exceptions import AuthError
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import App
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from dbx_sync_manager import sync
from dbx_sync_manager.tree import CACHE_FILE, crawl, parent, refresh

UNITS = ["B", "KB", "MB", "GB", "TB"]
STYLES = {"synced": "bold blue", "excluded": "", "parent_excluded": ""}  # blue = ansi-dark theme's primary
# Nerd Font glyphs: nf-fa-circle_o, nf-fa-adjust, nf-fa-circle
EXCLUDED_ICON, PARTIAL_ICON, SYNCED_ICON = "\uf10c", "\uf042", "\uf111"


def human(n):
    i = 0
    while n >= 1000 and i < len(UNITS) - 1:
        n /= 1000
        i += 1
    if i == 0:
        return f"{n} B"
    return f"{n:.1f} {UNITS[i]}" if n < 100 else f"{n:.0f} {UNITS[i]}"


def age(seconds):
    for unit, span in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= span:
            return f"{int(seconds // span)}{unit}"
    return f"{int(seconds)}s"


def icon(path, excluded):
    if sync.state(path, excluded) != "synced":
        return EXCLUDED_ICON
    return PARTIAL_ICON if sync.has_excluded_inside(path, excluded) else SYNCED_ICON


def synced_size(tree, excluded):
    return tree.size("") - sum(tree.size(p) for p in excluded
                               if p in tree.folders and sync.state(parent(p), excluded) == "synced")


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [("y", "answer(True)"), ("n", "answer(False)"), ("escape", "answer(False)")]
    # a "y" this soon arrived with the key that opened the dialog (e.g. a forged terminal reply), not from the user
    ARM_DELAY = 0.3

    def __init__(self, message):
        super().__init__()
        self.message, self.opened = message, time.monotonic()

    def compose(self):
        yield Static(Text(f"{self.message}  [y/n]"), id="dialog")

    def action_answer(self, ok):
        if not ok or time.monotonic() - self.opened >= self.ARM_DELAY:
            self.dismiss(ok)


class DbxApp(App):
    CSS = """
    #path { height: 1; padding: 0 1; background: $panel; }
    #cols { height: 1fr; }
    #cols OptionList { width: 1fr; height: 100%; border: round ansi_bright_black; }
    #cols #current { border: round $primary; }
    #current, #preview { width: 2fr; }
    #progress { display: none; height: 1fr; content-align: center middle; }
    #status { height: 1; padding: 0 1; background: $panel; }
    ConfirmScreen { align: center middle; }
    #dialog { width: auto; max-width: 80; padding: 1 2; border: thick $warning; background: $surface; }
    """
    BINDINGS = [
        Binding("h,left", "up", "Up"), Binding("l,right", "into", "Into"),
        Binding("j", "move('down')", show=False), Binding("k", "move('up')", show=False),
        Binding("g", "move('first')", show=False), Binding("G", "move('last')", show=False),
        Binding("space", "toggle", "Toggle sync"), Binding("s", "sort", "Sort"),
        Binding("r", "load(False)", "Refresh"), Binding("R", "load(True)", "Recrawl"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, dbx, tree, root, cache_file=CACHE_FILE):
        super().__init__()
        self.theme = "ansi-dark"  # use the terminal's own palette and background
        self.dbx, self.folder_tree, self.root, self.cache_file = dbx, tree, root, cache_file
        self.cwd, self.sort_by_size = "", True
        self.excluded, self.daemon_error, self.dropbox_status = set(), None, ""
        self.loading, self.offline, self.progress = False, False, None

    def compose(self):
        yield Static(id="path")
        with Horizontal(id="cols"):
            for name in ("parent", "current", "preview"):
                ol = OptionList(id=name)
                ol.can_focus = name == "current"
                yield ol
        yield Static(id="progress")
        yield Static(id="status")
        yield Footer()

    def on_mount(self):
        self.query_one("#current").focus()
        self.action_load(full=self.folder_tree is None)
        self.set_interval(5, self.poll_status)
        self.poll_status()

    # --- data -------------------------------------------------------------

    def _reload_excluded(self):
        try:
            self.excluded, self.daemon_error = sync.exclude_list(self.root), None
        except (RuntimeError, OSError) as e:
            self.daemon_error = str(e) or "dropbox-cli failed"

    def action_load(self, full):
        if self.loading:
            return
        self.loading, self.progress = True, 0
        self._show_progress(full or self.folder_tree is None)
        self.redraw()
        self.fetch(full)

    @work(thread=True)
    def fetch(self, full):
        report = lambda n: self.call_from_thread(self._on_progress, n)
        try:
            if full or self.folder_tree is None:
                tree = crawl(self.dbx, report)
            else:
                # refresh mutates in place; work on a copy so the UI never sees a half-applied delta
                tree = refresh(self.dbx, copy.deepcopy(self.folder_tree), report)
            tree.save(self.cache_file)
        except requests.exceptions.ConnectionError:
            self.call_from_thread(self._loaded, None, offline=True)
        except AuthError:
            self.call_from_thread(self.exit, "relogin")
        except Exception as e:
            self.call_from_thread(self._loaded, None, error=str(e))
        else:
            self.call_from_thread(self._loaded, tree)

    def _on_progress(self, n):
        self.progress = n
        self.query_one("#progress", Static).update(f"crawling… {n:,} entries")
        self._update_status()

    def _loaded(self, tree, offline=False, error=None):
        self.loading, self.progress, self.offline = False, None, offline
        if tree is not None:
            self.folder_tree = tree
        if error:
            self.notify(error, severity="error", markup=False)
        self._show_progress(False)
        self.redraw()

    def _show_progress(self, on):
        self.query_one("#cols").display = not on
        self.query_one("#progress").display = on
        if not on:
            self.query_one("#current").focus()  # hiding #cols dropped focus

    @work(thread=True, group="status")
    def poll_status(self):
        s = sync.status()
        try:
            excluded, error = sync.exclude_list(self.root), None
        except (RuntimeError, OSError) as e:
            excluded, error = None, str(e) or "dropbox-cli failed"
        self.call_from_thread(self._set_status, s, excluded, error)

    def _set_status(self, s, excluded, error):
        self.dropbox_status, self.daemon_error = s, error
        if excluded is not None and excluded != self.excluded:
            self.excluded = excluded
            self.redraw()
        else:
            self._update_status()

    # --- rendering --------------------------------------------------------

    def _path_display(self, path):
        parts = []
        while path:
            parts.append(self.folder_tree.name(path))
            path = parent(path)
        return "/" + "/".join(reversed(parts))

    def _sorted(self, paths):
        if self.sort_by_size:
            return sorted(paths, key=self.folder_tree.size, reverse=True)
        return sorted(paths, key=lambda p: self.folder_tree.name(p).casefold())

    def _option(self, path):
        style = STYLES[sync.state(path, self.excluded)]
        row = Table.grid(expand=True)
        row.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        row.add_column(justify="right")
        row.add_row(Text(f"{icon(path, self.excluded)} {self.folder_tree.name(path)}", style=style), Text(human(self.folder_tree.size(path)), style=style))
        return Option(row, id=path)

    def _fill(self, ol, paths, select=None):
        ol.set_options([self._option(p) for p in self._sorted(paths)])
        if paths:
            ol.highlighted = ol.get_option_index(select) if select in paths else 0

    def _selected(self):
        ol = self.query_one("#current", OptionList)
        return None if ol.highlighted is None else ol.get_option_at_index(ol.highlighted).id

    def redraw(self, select=None):
        if self.folder_tree is not None:
            while self.cwd not in self.folder_tree.folders:  # cwd deleted remotely
                self.cwd = parent(self.cwd)
            select = select or self._selected()
            self._fill(self.query_one("#current", OptionList), self.folder_tree.children(self.cwd), select)
            parent_ol = self.query_one("#parent", OptionList)
            if self.cwd:
                self._fill(parent_ol, self.folder_tree.children(parent(self.cwd)), self.cwd)
            else:
                parent_ol.clear_options()
            self._preview()
            self.query_one("#path", Static).update(Text(self.root.name + (self._path_display(self.cwd) if self.cwd else "")))
        self._update_status()

    @on(OptionList.OptionHighlighted, "#current")
    def _preview(self):
        sel = self._selected()
        self._fill(self.query_one("#preview", OptionList), self.folder_tree.children(sel) if sel else [])

    def _update_status(self):
        parts = []
        if self.progress is not None:
            parts.append(f"crawling… {self.progress:,} entries")
        if self.folder_tree is not None:
            parts.append(f"synced {human(synced_size(self.folder_tree, self.excluded))} / total {human(self.folder_tree.size(''))}")
            if self.folder_tree.crawled_at:
                parts.append(f"cache {age(time.time() - self.folder_tree.crawled_at)} ago")
        if self.offline:
            parts.append("offline")
        text = Text(" · ".join(parts))
        if self.daemon_error:
            text.append(f" · Dropbox: {self.daemon_error}", style="bold red")
        elif self.dropbox_status:
            text.append(f" · Dropbox: {self.dropbox_status}")
        self.query_one("#status", Static).update(text)

    # --- actions ----------------------------------------------------------

    def action_move(self, where):
        getattr(self.query_one("#current", OptionList), f"action_{'cursor_' if where in ('up', 'down') else ''}{where}")()

    def action_up(self):
        if self.folder_tree is not None and self.cwd:
            old, self.cwd = self.cwd, parent(self.cwd)
            self.redraw(select=old)

    def action_into(self):
        sel = self._selected()
        if sel:
            self.cwd = sel
            self.redraw()

    def action_sort(self):
        self.sort_by_size = not self.sort_by_size
        self.redraw()

    def action_toggle(self):
        path = self._selected()
        if path is None:
            return
        self._reload_excluded()
        if self.daemon_error:
            self.notify(f"Dropbox: {self.daemon_error}", severity="error", markup=False)
            return self._update_status()
        before = sync.state(path, self.excluded)
        name, size = self.folder_tree.name(path), human(self.folder_tree.size(path))
        if before == "parent_excluded":
            anc = parent(path)
            while anc not in self.excluded:
                anc = parent(anc)
            return self.notify(f"parent excluded: include {self.folder_tree.name(anc)} first", markup=False)
        exclude = before == "synced"
        message = f"Remove {size} local copy of {name}? (stays in cloud)" if exclude else f"Download {size} for {name}?"

        def done(ok):
            if not ok:
                return
            if path not in self.folder_tree.folders:
                return self.notify(f"{name} no longer exists in Dropbox", severity="error", markup=False)
            output = sync.set_excluded(self.root, self._path_display(path), exclude)
            self._reload_excluded()
            if sync.state(path, self.excluded) == before:
                self.notify(output or "dropbox-cli made no change", severity="error", markup=False)
            self.redraw(select=path)

        self.push_screen(ConfirmScreen(message), done)
