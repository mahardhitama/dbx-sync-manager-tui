import json
import os
from pathlib import Path

from dropbox.files import DeletedMetadata, FileMetadata, FolderMetadata

CACHE_FILE = Path.home() / ".cache/dbx-sync-manager/tree.json"


def parent(path):
    return path.rsplit("/", 1)[0]


class Tree:
    def __init__(self, cursor=None, crawled_at=0.0, folders=None):
        self.cursor = cursor
        self.crawled_at = crawled_at
        self.folders = folders or {}
        self.folders.setdefault("", {"name": "", "files": {}})
        self._sizes = None

    def _ensure(self, lower, display):
        """Create the folder and any missing ancestors."""
        while lower not in self.folders:
            self.folders[lower] = {"name": display.rsplit("/", 1)[-1], "files": {}}
            lower, display = parent(lower), parent(display)

    def apply(self, entries):
        for e in entries:
            if isinstance(e, FolderMetadata):
                self._ensure(e.path_lower, e.path_display)
            elif isinstance(e, FileMetadata):
                self._ensure(parent(e.path_lower), parent(e.path_display))
                self.folders[parent(e.path_lower)]["files"][e.name.lower()] = e.size
            elif isinstance(e, DeletedMetadata):
                p = e.path_lower
                if p in self.folders:
                    for k in [k for k in self.folders if k == p or k.startswith(p + "/")]:
                        del self.folders[k]
                elif parent(p) in self.folders:
                    self.folders[parent(p)]["files"].pop(p.rsplit("/", 1)[-1], None)
        self._sizes = None

    def children(self, path):
        return sorted(k for k in self.folders if k and parent(k) == path)

    def name(self, path):
        return self.folders[path]["name"]

    def size(self, path):
        if self._sizes is None:
            sizes = {k: sum(f["files"].values()) for k, f in self.folders.items()}
            for k in sorted(self.folders, key=lambda k: k.count("/"), reverse=True):
                if k:
                    sizes[parent(k)] += sizes[k]
            self._sizes = sizes
        return self._sizes[path]

    def save(self, file=CACHE_FILE):
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"cursor": self.cursor, "crawled_at": self.crawled_at, "folders": self.folders}))
        os.replace(tmp, file)

    @classmethod
    def load(cls, file=CACHE_FILE):
        try:
            d = json.loads(file.read_text())
            return cls(d["cursor"], d["crawled_at"], d["folders"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
