"""Shared helpers for the tests: PASS / FAIL reporting, a guard against outside network use,
and a way to call the program's routes without opening a port."""
import json
import os
import shutil
import socket
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import fixtures  # noqa: E402


class H:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.section_name = "-"
        self.blocked = []
        self._tmp = []
        self.base = os.environ.get("SK_TEST_TMP") or tempfile.gettempdir()

    # ---- reporting
    def section(self, name):
        self.section_name = name
        print("--- %s" % name)

    def ok(self, cond, label):
        if cond:
            self.passed += 1
            print("PASS %s: %s" % (self.section_name, label))
        else:
            self.failed += 1
            print("FAIL %s: %s" % (self.section_name, label))
        return bool(cond)

    def eq(self, got, want, label):
        good = got == want
        if not self.ok(good, label):
            print("     got:  %r\n     want: %r" % (got, want))
        return good

    def skip(self, label):
        self.skipped += 1
        print("SKIP %s: %s" % (self.section_name, label))

    def run(self, fn):
        try:
            fn(self)
        except Exception:
            self.failed += 1
            print("FAIL %s: %s raised an error" % (self.section_name, fn.__name__))
            traceback.print_exc(file=sys.stdout)

    # ---- scratch folders
    def tmp(self):
        d = tempfile.mkdtemp(prefix="sk-test-", dir=self.base)
        self._tmp.append(d)
        return d

    def cleanup(self):
        for d in self._tmp:
            shutil.rmtree(d, ignore_errors=True)

    # ---- network guard
    def guard_network(self):
        h = self
        real_connect = socket.socket.connect
        real_getaddrinfo = socket.getaddrinfo
        local = ("127.0.0.1", "::1", "localhost")

        def connect(sock, address):
            host = address[0] if isinstance(address, tuple) else address
            if host not in local:
                h.blocked.append(host)
                raise OSError("network use outside 127.0.0.1 is blocked in tests")
            return real_connect(sock, address)

        def getaddrinfo(host, *a, **k):
            if host not in local and host is not None:
                h.blocked.append(host)
                raise OSError("name lookup is blocked in tests")
            return real_getaddrinfo(host, *a, **k)

        socket.socket.connect = connect
        socket.getaddrinfo = getaddrinfo

    def summary(self):
        print("%d passed, %d failed" % (self.passed, self.failed) + (", %d skipped" % self.skipped if self.skipped else ""))


# ---- calling the program

def make_app(h, folder=None, net=None, check_updates=False, auto_lookup=False):
    import server
    app = server.App(folder or h.tmp(), check_updates=check_updates)
    app.auto_lookup = auto_lookup      # most tests drive prices by hand; the LOOKUP tests turn this on
    net = net or fixtures.FakeNet()
    app.ck_opener, app.scryfall_post, app.sleep = net.ck_opener, net.scryfall_post, net.sleep
    return app, net


def call(app, method, path, body=None, query=None, raw=None, headers=None):
    """Returns (status, parsed answer). Answers that are CSV come back as text."""
    from urllib.parse import parse_qs, urlparse
    u = urlparse(path)
    q = parse_qs(u.query)
    if query:
        q.update({k: [str(v)] for k, v in query.items()})
    hdrs = {"host": "127.0.0.1:8901", "x-requested-with": "ScammersKingdom"}
    hdrs.update(headers or {})
    data = raw.encode("utf-8") if isinstance(raw, str) else raw if raw is not None else \
        (json.dumps(body).encode("utf-8") if body is not None else b"")
    r = app.route(method, u.path, q, data, hdrs)
    call.last = r
    if r.ctype.startswith("application/json"):
        return r.status, json.loads(r.body.decode("utf-8"))
    return r.status, r.body.decode("utf-8")


def loaded_app(h, shop=None, update=True, folder=None, net=None):
    """An app with a binder imported from the sample scan and prices downloaded."""
    app, net = make_app(h, folder, net)
    st, _ = call(app, "POST", "/api/import/shoptcg?mode=replace", raw=shop if shop is not None else fixtures.sample_shop())
    assert st == 200, st
    if update:
        app.run_update()
    return app, net


def rows_by_name(state, name, finish=None, cond=None):
    return [r for r in state["rows"] if r["name"] == name and (finish is None or r["finish"] == finish)
            and (cond is None or r["cond"] == cond)]
