"""Scammer's Kingdom: a small local program that shows which of your Magic cards to send to Card Kingdom.

Run it with `python server.py` (or double-click the .exe). It listens on 127.0.0.1 only
and opens the page in your browser. Your data lives in the ScammersKingdom-data folder.
"""
import argparse
import json
import os
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import ck_prices
import collection
import market as market_mod
import netutil
import plan as plan_mod
import storage
import updates
from appinfo import APP_NAME, APP_VERSION

START_PORT = 8901
PORT_TRIES = 11
MAX_BODY = 64 * 1024 * 1024
REMIND_DAYS = 14
UPDATE_CHECK_HOURS = 24
NO_PRICES_MESSAGE = "Press Update prices first: the card list comes with Card Kingdom's prices."
CSP = ("default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
       "img-src 'self' data: https://api.scryfall.com https://cards.scryfall.io; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class Response:
    def __init__(self, status=200, body=b"", ctype="application/json; charset=utf-8", headers=None):
        self.status, self.body, self.ctype, self.headers = status, body, ctype, headers or {}


def jresp(obj, status=200):
    return Response(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def fail(status, message):
    return jresp({"error": message}, status)


def _int(text, default=None):
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


class App:
    def __init__(self, data_folder, resources=None, check_updates=True):
        self.data_folder = data_folder
        self.prices_dir = os.path.join(data_folder, "prices")
        self.resources = resources or storage.resource_dir()
        self.check_updates = check_updates
        self.lock = threading.RLock()
        self.port = None
        # Where the web requests go. Tests replace these; nothing else touches the network.
        self.ck_opener = None
        self.scryfall_post = None
        self.github_get = None
        self.sleep = time.sleep
        self.clock = lambda: datetime.now().astimezone()
        self.update = {"running": False, "message": "", "result": None}
        self.warnings = []
        self._index = None
        self._load()

    # ---------- loading and saving ----------

    def _path(self, *parts):
        return os.path.join(self.data_folder, *parts)

    def _load(self):
        os.makedirs(self.prices_dir, exist_ok=True)
        self.settings = dict(plan_mod.DEFAULT_SETTINGS)
        saved = storage.read_json(self._path("settings.json"), {})
        if isinstance(saved, dict):
            for k in self.settings:
                if isinstance(saved.get(k), int) and not isinstance(saved.get(k), bool):
                    self.settings[k] = saved[k]
        self.state = {"last_export": None, "binder_since": None, "remind_dismissed_at": None,
                      "back_keys": [], "back_line_dismissed": False, "last_import": None,
                      "update_check": {"at": None, "tag": None, "url": None}, "history": []}
        st = storage.read_json(self._path("state.json"), {})
        if isinstance(st, dict):
            self.state.update(st)
        self.binder = collection.Binder(storage.read_json(self._path("collection.json"), {}),
                                        self.state.get("history"))
        self.ck = ck_prices.load(self.prices_dir)
        self.mk = market_mod.load(self.prices_dir)
        for name in ("collection.json", "state.json", "settings.json"):
            if os.path.exists(self._path(name + ".broken")):
                self.warnings.append("A damaged file (%s) was set aside as %s.broken." % (name, name))

    def _save_collection(self):
        storage.write_json(self._path("collection.json"), self.binder.to_json())

    def _save_state(self):
        self.state["history"] = self.binder.history
        storage.write_json(self._path("state.json"), self.state)

    def _after_binder_change(self):
        if self.state["binder_since"] is None and self.binder.rows:
            self.state["binder_since"] = self.clock().isoformat(timespec="seconds")
        self._save_collection()
        self._save_state()

    def ck_rows(self):
        return self.ck["rows"] if self.ck else {}

    # ---------- what the page gets ----------

    def remind_due(self):
        now = self.clock()
        base = self.state.get("last_export") or self.state.get("binder_since")
        if not base or not self.binder.rows:
            return False
        try:
            if now - datetime.fromisoformat(base) < timedelta(days=REMIND_DAYS):
                return False
            d = self.state.get("remind_dismissed_at")
            if d and now - datetime.fromisoformat(d) < timedelta(days=REMIND_DAYS):
                return False
        except ValueError:
            return False
        return True

    def new_version(self):
        c = self.state.get("update_check") or {}
        if c.get("tag") and updates.is_newer(c["tag"], APP_VERSION):
            return {"tag": c["tag"], "url": c["url"]}
        return None

    def snapshot(self, extra=None):
        with self.lock:
            rows = collection.view_rows(self.binder.rows, self.ck_rows(), self.mk, set(self.state["back_keys"]))
            hist = self.binder.history
            out = {
                "version": APP_VERSION, "settings": dict(self.settings),
                "defaults": dict(plan_mod.DEFAULT_SETTINGS),
                "prices": {"has_ck": self.ck is not None, "ck_as_of": self.ck["as_of"] if self.ck else None,
                           "market_as_of": self.mk.as_of},
                "rows": rows, "other_language": self.binder.other, "last_import": self.state.get("last_import"),
                "back": {"count": sum(1 for r in rows if r["back"]),
                         "line_dismissed": bool(self.state["back_line_dismissed"])},
                "undo": {"count": len(hist), "label": hist[-1]["label"] if hist else None},
                "remind": self.remind_due(), "new_version": self.new_version(),
                "update": dict(self.update), "warnings": list(self.warnings),
            }
        if extra:
            out.update(extra)
        return out

    def _ck_index(self):
        if self._index is None or self._index[0] is not self.ck:
            self._index = (self.ck, ck_prices.build_index(self.ck_rows()))
        return self._index[1]

    def search(self, q, name):
        with self.lock:
            if not self.ck:
                return {"ok": False, "message": NO_PRICES_MESSAGE, "items": []}
            if name is not None:
                found = ck_prices.search(self.ck_rows(), self._ck_index(), name=name, limit=300)
            else:
                found = ck_prices.search(self.ck_rows(), self._ck_index(), q=q, limit=40)
            by_id = self.mk.by_id
            items = []
            for key, e in found:
                sid, finish = key.split("|")
                m = by_id.get(sid)
                items.append({"sid": sid, "finish": finish, "name": e["name"], "edition": e["edition"],
                              "variation": e["variation"], "number": m["number"] if m else "",
                              "cash": e["cash"], "wants": e["wants"], "buying": ck_prices.is_buying(e)})
            return {"ok": True, "items": items}

    # ---------- changes ----------

    def edit(self, payload):
        with self.lock:
            label = self.binder.edit(payload, self.ck_rows(), self.mk)
            if label:
                self._after_binder_change()
            return self.snapshot({"label": label})

    def add(self, payload):
        if not isinstance(payload, dict):
            raise collection.EditError(400, "That was not a valid request.")
        with self.lock:
            label = self.binder.add_hand(payload.get("sid"), payload.get("finish"),
                                         payload.get("condition", "NM"), payload.get("amount", 1),
                                         self.ck_rows(), self.mk)
            self._after_binder_change()
            sid = str(payload.get("sid", "")).lower()
            return self.snapshot({"label": label, "need_market": sid not in self.mk.by_id})

    def undo(self):
        with self.lock:
            label = self.binder.undo()
            self._after_binder_change()
            return self.snapshot({"label": "Undone: " + label})

    def set_settings(self, payload):
        limits = {"bag_size": (1, 500), "bag_fee": (0, 100000), "credit_bonus": (0, 1000),
                  "ex_share": (0, 100), "vg_share": (0, 100)}
        if not isinstance(payload, dict) or not payload:
            raise collection.EditError(400, "No settings were given.")
        new = {}
        for k, v in payload.items():
            if k not in limits:
                raise collection.EditError(400, "Unknown setting.")
            lo, hi = limits[k]
            if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
                raise collection.EditError(400, "That value is not allowed for this setting.")
            new[k] = v
        with self.lock:
            self.settings.update(new)
            storage.write_json(self._path("settings.json"), self.settings)
            return self.snapshot()

    def reset_settings(self):
        with self.lock:
            self.settings = dict(plan_mod.DEFAULT_SETTINGS)
            storage.write_json(self._path("settings.json"), self.settings)
            return self.snapshot()

    def ui(self, payload):
        action = payload.get("action") if isinstance(payload, dict) else None
        with self.lock:
            if action == "dismiss_reminder":
                self.state["remind_dismissed_at"] = self.clock().isoformat(timespec="seconds")
            elif action == "dismiss_back_line":
                self.state["back_line_dismissed"] = True
            else:
                raise collection.EditError(400, "That action is not known.")
            self._save_state()
            return self.snapshot()

    def import_shoptcg(self, text, mode):
        parsed = collection.read_shoptcg(text)
        with self.lock:
            res = self.binder.import_shoptcg(parsed, mode, self.mk)
            self.state["last_import"] = {
                "copies": res["copies"], "rows": len(parsed["rows"]), "mode": mode,
                "skipped": parsed["skipped"][:200], "skipped_count": len(parsed["skipped"]),
                "other_copies": sum(o["qty"] for o in parsed["other_language"])}
            self._after_binder_change()
            return self.snapshot({"label": res["label"], "skipped": parsed["skipped"][:200]})

    def import_collection(self, text, mode):
        parsed = collection.read_my_collection(text)
        with self.lock:
            if mode == "check":
                return {"ok": True, "cards": parsed["copies"], "rows": len(parsed["rows"]),
                        "mine": self.binder.copies(), "unplaceable": parsed["unplaceable"][:200],
                        "unplaceable_count": len(parsed["unplaceable"])}
            if not parsed["rows"]:
                raise collection.BadFile("That file has no cards in it.")
            res = self.binder.import_collection(parsed, mode, self.mk)
            self._after_binder_change()
            return self.snapshot({"label": res["label"], "unplaceable": parsed["unplaceable"][:200],
                                  "unplaceable_count": len(parsed["unplaceable"])})

    def export_collection(self):
        with self.lock:
            text = collection.export_csv(self.binder.rows, lambda r: collection.resolve(r, self.mk))
            self.state["last_export"] = self.clock().isoformat(timespec="seconds")
            self.state["remind_dismissed_at"] = None
            self._save_state()
            return text, self.binder.copies()

    def plan(self, bags, pay):
        with self.lock:
            rows = collection.view_rows(self.binder.rows, self.ck_rows(), self.mk)
            return plan_mod.build_plan(rows, self.settings, bags, pay)

    # ---------- updating prices ----------

    def start_update(self):
        """Begin an update in the background. Returns the thread, or None when one is already running."""
        with self.lock:
            if self.update["running"]:
                return None
            self.update = {"running": True, "message": "Starting...", "result": None}
        t = threading.Thread(target=self.run_update, daemon=True)
        t.start()
        return t

    def _say(self, text):
        with self.lock:
            self.update["message"] = text

    def run_update(self):
        lines, problems = [], []
        try:
            old = self.ck
            new_ck = None
            self._say("Downloading Card Kingdom's buylist...")
            try:
                new_ck = ck_prices.download(
                    self.ck_opener,
                    progress=lambda n: self._say("Downloading Card Kingdom's buylist... %d MB" % (n // 1048576)))
                with self.lock:
                    ck_prices.save_latest(self.prices_dir, new_ck)
                    self.ck, self._index = new_ck, None
                lines.append("Card Kingdom's buylist is up to date.")
            except (netutil.NetError, ck_prices.PricelistError, OSError) as exc:
                new_ck = None
                msg = "Could not get Card Kingdom's buylist (%s)." % _plain(exc)
                if old:
                    msg += " Your earlier buylist prices stay."
                problems.append(msg)

            with self.lock:
                snap_rows = [dict(r) for r in self.binder.rows]
            idents = market_mod.identifiers(snap_rows)
            if idents:
                result = market_mod.fetch(
                    idents, self.scryfall_post, self.sleep,
                    progress=lambda n, total: self._say("Getting market prices %d of %d..." % (n, total)))
                with self.lock:
                    self.mk.merge(result, set(idents))
                    market_mod.save(self.prices_dir, self.mk)
                    for r in self.binder.rows:
                        if not r["sid"]:
                            e = self.mk.entry_for(r)
                            if e and e.get("id"):
                                r["sid"] = e["id"]
                    self._save_collection()
                if result["error"]:
                    problems.append("Could not get all the market prices (%s). Earlier market prices stay." % result["error"])
                else:
                    lines.append("Market prices are up to date.")
                    if result["missing"]:
                        lines.append("%d cards were not found by the price lookup." % len(result["missing"]))
            if new_ck is not None and old is not None:
                self._find_back(old["rows"], new_ck["rows"])
            elif new_ck is not None:
                with self.lock:
                    self.state["back_keys"], self.state["back_line_dismissed"] = [], False
                    self._save_state()
        except Exception as exc:  # never leave the page waiting
            problems.append("The update stopped (%s)." % _plain(exc))
        finally:
            with self.lock:
                self.update = {"running": False, "message": "",
                               "result": {"ok": not problems, "lines": problems or lines,
                                          "at": self.clock().isoformat(timespec="seconds")}}

    def _find_back(self, before, after):
        """Cards in the binder that CK was not buying at the last update and is buying now."""
        keys = set()
        with self.lock:
            for r in self.binder.rows:
                e = self.mk.entry_for(r)
                sid = r["sid"] or (e["id"] if e else "")
                if not sid:
                    continue
                k = "%s|%s" % (sid, r["finish"])
                if ck_prices.is_buying(after.get(k)) and not ck_prices.is_buying(before.get(k)):
                    keys.add(k)
            self.state["back_keys"] = sorted(keys)
            self.state["back_line_dismissed"] = False
            self._save_state()

    # ---------- the newer-version line ----------

    def check_for_new_version(self, force=False):
        """Ask for the newest release at most once a day. A failure is silent."""
        if not self.check_updates and not force:
            return
        with self.lock:
            c = self.state.setdefault("update_check", {"at": None, "tag": None, "url": None})
            if c.get("at") and not force:
                try:
                    if self.clock() - datetime.fromisoformat(c["at"]) < timedelta(hours=UPDATE_CHECK_HOURS):
                        return
                except ValueError:
                    pass
            c["at"] = self.clock().isoformat(timespec="seconds")
            self._save_state()
        try:
            got = updates.latest(self.github_get)
        except Exception:
            return
        if got:
            with self.lock:
                c.update(got)
                self._save_state()

    # ---------- web routes ----------

    def route(self, method, path, query, body, headers):
        host = (headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if host not in ("127.0.0.1", "localhost"):
            return fail(403, "This address is not allowed.")
        if method == "POST" and headers.get("x-requested-with") != "ScammersKingdom":
            return fail(403, "That request did not come from the Scammer's Kingdom page.")
        try:
            return self._dispatch(method, path, query, body)
        except collection.EditError as exc:
            return fail(exc.status, exc.message)
        except collection.BadFile as exc:
            return fail(400, str(exc))
        except OSError:
            return fail(500, "Your change could not be saved on this PC.")

    def _json_body(self, body):
        try:
            return json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise collection.EditError(400, "That was not a valid request.")

    def _text_body(self, body):
        try:
            return body.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise collection.BadFile("That file could not be read as text.")

    def _dispatch(self, method, path, query, body):
        q = {k: v[0] for k, v in query.items()}
        if method == "GET":
            if path in ("/", "/index.html"):
                with open(os.path.join(self.resources, "static", "index.html"), "rb") as f:
                    return Response(200, f.read(), "text/html; charset=utf-8", {"Content-Security-Policy": CSP})
            if path == "/api/state":
                return jresp(self.snapshot())
            if path == "/api/search":
                return jresp(self.search(q.get("q", ""), q.get("name")))
            if path == "/api/update/status":
                with self.lock:
                    return jresp(dict(self.update))
            if path == "/download/collection.csv":
                text, _n = self.export_collection()
                return self._file(text, "scammers-kingdom-collection-%s.csv")
            if path in ("/download/ck-import.csv", "/download/plan.csv"):
                bags = _int(q.get("bags"))
                pay = q.get("pay", "cash")
                if bags is None or not 1 <= bags <= plan_mod.MAX_BAGS or pay not in ("cash", "credit"):
                    return fail(400, "Choose 1 to %d bags." % plan_mod.MAX_BAGS)
                p = self.plan(bags, pay)
                if path == "/download/plan.csv":
                    return self._file(plan_mod.plan_csv(p), "ck-shipment-plan-%s.csv")
                return self._file(plan_mod.ck_import_csv(p), "ck-buylist-%s.csv")
            return fail(404, "Not found.")
        if method == "POST":
            if path == "/api/edit":
                return jresp(self.edit(self._json_body(body)))
            if path == "/api/add":
                return jresp(self.add(self._json_body(body)))
            if path == "/api/undo":
                return jresp(self.undo())
            if path == "/api/settings":
                return jresp(self.set_settings(self._json_body(body)))
            if path == "/api/settings/reset":
                return jresp(self.reset_settings())
            if path == "/api/ui":
                return jresp(self.ui(self._json_body(body)))
            if path == "/api/import/shoptcg":
                return jresp(self.import_shoptcg(self._text_body(body), q.get("mode", "replace")))
            if path == "/api/import/collection":
                return jresp(self.import_collection(self._text_body(body), q.get("mode", "check")))
            if path == "/api/update":
                if self.start_update() is None:
                    return fail(409, "An update is already running.")
                return jresp({"started": True}, 202)
            return fail(404, "Not found.")
        return fail(405, "That method is not allowed.")

    def _file(self, text, name_pattern):
        name = name_pattern % self.clock().strftime("%Y-%m-%d")
        return Response(200, text.encode("utf-8"), "text/csv; charset=utf-8",
                        {"Content-Disposition": 'attachment; filename="%s"' % name})


def _plain(exc):
    return str(exc) or type(exc).__name__


class Handler(BaseHTTPRequestHandler):
    server_version = "ScammersKingdom"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _go(self, method):
        url = urlparse(self.path)
        body = b""
        if method == "POST":
            n = _int(self.headers.get("Content-Length"), 0)
            if n > MAX_BODY:
                self._send(fail(413, "That file is too big."))
                self.close_connection = True
                return
            body = self.rfile.read(n) if n > 0 else b""
        hdrs = {k.lower(): v for k, v in self.headers.items()}
        self._send(self.server.app.route(method, url.path, parse_qs(url.query), body, hdrs))

    def _send(self, resp):
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.ctype)
        self.send_header("Content-Length", str(len(resp.body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in resp.headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(resp.body)

    def do_GET(self):
        self._go("GET")

    def do_POST(self):
        self._go("POST")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def bind_server(app, start=START_PORT, tries=PORT_TRIES, host="127.0.0.1"):
    """Listen on the first free port from `start` (127.0.0.1 only)."""
    last = None
    for port in range(start, start + tries):
        try:
            srv = Server((host, port), Handler)
        except OSError as exc:
            last = exc
            continue
        srv.app = app
        app.port = port
        return srv
    raise OSError("No free port from %d to %d (%s)" % (start, start + tries - 1, last))


def main(argv=None):
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--port", type=int, default=START_PORT)
    ap.add_argument("--data", default=None, help="folder for your data")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)
    app = App(args.data or storage.data_dir())
    try:
        srv = bind_server(app, args.port)
    except OSError as exc:
        print("Scammer's Kingdom could not start: %s" % exc)
        return 1
    url = "http://127.0.0.1:%d" % app.port
    print("Scammer's Kingdom is running at %s. Close this window to stop." % url)
    threading.Thread(target=app.check_for_new_version, daemon=True).start()
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
