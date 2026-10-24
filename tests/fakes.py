from datetime import datetime

from dropbox.exceptions import ApiError
from dropbox.files import DeletedMetadata, FileMetadata, FolderMetadata, ListFolderContinueError, ListFolderResult


def _name(path_display):
    return path_display.rsplit("/", 1)[-1]


def folder(path_display):
    return FolderMetadata(name=_name(path_display), id="id:f", path_lower=path_display.lower(), path_display=path_display)


def file(path_display, size):
    return FileMetadata(
        name=_name(path_display), id="id:x", client_modified=datetime(2020, 1, 1),
        server_modified=datetime(2020, 1, 1), rev="0123456789abcdef", size=size,
        path_lower=path_display.lower(), path_display=path_display,
    )


def deleted(path_display):
    return DeletedMetadata(name=_name(path_display), path_lower=path_display.lower(), path_display=path_display)


class FakeDbx:
    """Crawl pages chained by cursors c0, c1, ...; continuing past the last returns `deltas` once."""

    def __init__(self, pages, deltas=(), reset=False):
        self.pages, self.deltas, self.reset = pages, list(deltas), reset

    def _page(self, i):
        return ListFolderResult(entries=self.pages[i], cursor=f"c{i}", has_more=i + 1 < len(self.pages))

    def files_list_folder(self, path, recursive=False):
        assert path == "" and recursive
        return self._page(0)

    def files_list_folder_continue(self, cursor):
        i = int(cursor[1:]) + 1
        if i < len(self.pages):
            return self._page(i)
        if self.reset:
            raise ApiError("req", ListFolderContinueError.reset, None, None)
        entries, self.deltas = self.deltas, []
        return ListFolderResult(entries=entries, cursor=cursor, has_more=False)
