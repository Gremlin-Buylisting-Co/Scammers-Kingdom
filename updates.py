"""Checking whether a newer version has been published. It only ever shows a line
in the page: nothing is downloaded or installed."""
import re

import netutil
from appinfo import REPO

RELEASES_URL = "https://api.github.com/repos/%s/releases/latest" % REPO
RELEASE_PAGE_PREFIX = "https://github.com/%s/" % REPO


def parse_version(tag):
    m = re.match(r"^[vV]?(\d+(?:\.\d+){0,3})$", str(tag or "").strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def is_newer(tag, current):
    a, b = parse_version(tag), parse_version(current)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def latest(get_json=None):
    """{'tag', 'url'} of the newest release, or None when the answer is not usable."""
    get_json = get_json or netutil.get_json
    data = get_json(RELEASES_URL, {"Accept": "application/vnd.github+json"})
    if not isinstance(data, dict):
        return None
    tag, url = data.get("tag_name"), data.get("html_url")
    if parse_version(tag) is None or not str(url or "").startswith(RELEASE_PAGE_PREFIX):
        return None
    return {"tag": str(tag), "url": str(url)}
