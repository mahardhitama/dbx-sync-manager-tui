from datetime import datetime

from dropbox.files import DeletedMetadata, FileMetadata, FolderMetadata


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
