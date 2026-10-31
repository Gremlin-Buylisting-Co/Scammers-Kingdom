"""Where files live, and how they are written safely.

Every write goes to a temporary file first and is then moved over the real
file, so a crash or a power cut never leaves a half-written file behind.
On Windows the move can fail for a moment (another program is looking at the
file), so it is tried again a few times.
"""
import json
import os
import sys
import threading
import time

DATA_FOLDER_NAME = "ScammersKingdom-data"


def _here():
    return os.path.dirname(os.path.abspath(__file__))


def is_frozen():
    """True when running as the packaged .exe."""
    return bool(getattr(sys, "frozen", False))


def resource_dir(frozen=None, meipass=None, here=None):
    """Folder that holds the page (static/index.html)."""
    if frozen is None:
        frozen = is_frozen()
    if frozen:
        base = meipass if meipass is not None else getattr(sys, "_MEIPASS", None)
        if base:
            return base
    return here or _here()


def data_dir(frozen=None, executable=None, here=None):
    """Folder that holds the user's data: next to the .exe, or next to server.py."""
    if frozen is None:
        frozen = is_frozen()
    if frozen:
        base = os.path.dirname(os.path.abspath(executable or sys.executable))
    else:
        base = here or _here()
    return os.path.join(base, DATA_FOLDER_NAME)


def replace_retry(src, dst, retries=3, replace=os.replace, sleep=time.sleep):
    """os.replace, tried again up to `retries` more times if the first try fails."""
    for attempt in range(retries + 1):
        try:
            replace(src, dst)
            return
        except OSError:
            if attempt >= retries:
                raise
            sleep(0.1 * (attempt + 1))


def write_atomic(path, data, retries=3, replace=os.replace, sleep=time.sleep):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
    try:
        replace_retry(tmp, path, retries, replace, sleep)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_json(path, obj, retries=3, replace=os.replace, sleep=time.sleep):
    data = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    write_atomic(path, data, retries, replace, sleep)


def read_json(path, default=None):
    """Read a JSON file. A missing file gives `default`. A damaged file is set
    aside as <name>.broken (never deleted) and `default` is returned."""
    try:
        with open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8"))
    except FileNotFoundError:
        return default
    except (ValueError, UnicodeDecodeError):
        try:
            replace_retry(path, path + ".broken")
        except OSError:
            pass
        return default
