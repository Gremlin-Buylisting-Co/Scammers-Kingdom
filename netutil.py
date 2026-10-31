"""Small helpers for the few web requests the program makes."""
import gzip
import json
import socket
import urllib.error
import urllib.request

from appinfo import USER_AGENT


class NetError(Exception):
    """A request failed. The message is plain words for the person using the tool."""


def explain(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return "the server answered with an error (HTTP %s)" % exc.code
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return "it took too long (timed out)"
        return "no internet connection, or the site could not be reached"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "it took too long (timed out)"
    if isinstance(exc, ValueError):
        return "the answer was not in the format expected"
    if isinstance(exc, OSError):
        return "the connection was interrupted"
    return "an unexpected problem (%s)" % type(exc).__name__


def open_url(req, timeout=60, opener=None):
    opener = opener or urllib.request.urlopen
    try:
        return opener(req, timeout=timeout)
    except NetError:
        raise
    except Exception as exc:
        raise NetError(explain(exc)) from exc


def decoded(resp):
    """The response body as a stream, unzipped when the server zipped it."""
    enc = ""
    try:
        enc = (resp.headers.get("Content-Encoding") or "").lower()
    except Exception:
        pass
    if "gzip" in enc:
        return gzip.GzipFile(fileobj=resp)
    return resp


def http_get(url, headers=None, timeout=60, opener=None):
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    return open_url(urllib.request.Request(url, headers=h), timeout, opener)


def get_json(url, headers=None, timeout=20, opener=None):
    resp = http_get(url, headers, timeout, opener)
    try:
        with resp:
            return json.loads(decoded(resp).read().decode("utf-8"))
    except NetError:
        raise
    except Exception as exc:
        raise NetError(explain(exc)) from exc


def post_json(url, payload, timeout=30, opener=None):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    resp = open_url(req, timeout, opener)
    try:
        with resp:
            return json.loads(decoded(resp).read().decode("utf-8"))
    except NetError:
        raise
    except Exception as exc:
        raise NetError(explain(exc)) from exc
