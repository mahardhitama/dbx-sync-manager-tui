import json
import os
import subprocess
from pathlib import Path

from dbx_sync_manager.tree import parent


def local_root(info_file=Path.home() / ".dropbox/info.json"):
    return Path(json.loads(info_file.read_text())["business"]["root_path"])


def parse_exclude_list(output, cwd, root):
    """dropbox-cli prints excluded dirs relative to its cwd; return lowercase team paths."""
    if output.startswith("No directories are being ignored."):
        return set()
    lines = output.splitlines()
    if not lines or lines[0].strip() != "Excluded:":
        raise RuntimeError(output)
    result = set()
    for line in lines[1:]:
        if not line.strip():
            continue
        path = Path(os.path.normpath(cwd / line))
        if path.is_relative_to(root) and path != root:
            result.add("/" + str(path.relative_to(root)).lower())
    return result


def _run(*args, cwd=None):
    p = subprocess.run(["dropbox-cli", *args], capture_output=True, text=True, cwd=cwd)
    return (p.stdout + p.stderr).strip()


def exclude_list(root):
    return parse_exclude_list(_run("exclude", "list", cwd=root), root, root)


def state(path, excluded):
    if path in excluded:
        return "excluded"
    while path:
        path = parent(path)
        if path in excluded:
            return "parent_excluded"
    return "synced"


def set_excluded(root, path_display, exclude):
    # dropbox-cli reports errors on stdout with exit 0; callers verify via exclude_list()
    return _run("exclude", "add" if exclude else "remove", str(root) + path_display)


def status():
    try:
        return _run("status").splitlines()[0]
    except FileNotFoundError:
        return "dropbox-cli not found"
    except IndexError:
        return ""
