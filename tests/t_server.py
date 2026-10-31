"""SERVER, HAND, PUBLIC and DOCS."""
import codecs
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request

import collection
import fixtures as fx
import server
import storage
import updates
from appinfo import APP_VERSION
from harness import ROOT, call, loaded_app, make_app, rows_by_name


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------------ SERVER
def server_tests(h):
    h.section("SERVER")
    app, net = loaded_app(h)
    st, body = call(app, "GET", "/")
    h.eq(st, 200, "GET / serves the page")
    h.ok("<title>Scammer's Kingdom</title>" in body, "...which is the Scammer's Kingdom page")
    h.ok("default-src 'self'" in call.last.headers["Content-Security-Policy"], "the page comes with a content security policy")
    for path in ("/api/state", "/api/update/status"):
        h.eq(call(app, "GET", path)[0], 200, "GET %s works" % path)
    h.eq(call(app, "GET", "/nope")[0], 404, "an unknown address answers 404")
    h.eq(call(app, "POST", "/nope", {})[0], 404, "an unknown POST address answers 404")
    h.eq(call(app, "DELETE", "/api/state")[0], 405, "other methods answer 405")
    h.eq(call(app, "POST", "/api/edit", {"op": "qty"}, headers={"x-requested-with": ""})[0], 403, "a POST without the page's header is refused (cross-site safety)")
    h.eq(call(app, "GET", "/api/state", headers={"host": "evil.example:8901"})[0], 403, "a request for another host name is refused (DNS rebinding safety)")
    h.eq(call(app, "POST", "/api/edit", raw="{not json")[0], 400, "a body that is not JSON answers 400")
    st, j = call(app, "POST", "/api/edit", {"op": "qty", "id": 1, "qty": 2})
    h.eq(st, 200, "a valid edit answers 200")
    h.ok(isinstance(call(app, "POST", "/api/edit", {"op": "nonsense", "id": 1})[1]["error"], str), "errors come back as {error: plain words}")

    # one update at a time
    gate = threading.Event()
    started = threading.Event()
    net2 = fx.FakeNet()

    def slow():
        started.set()
        gate.wait(5)
    net2.ck_gate = slow
    app2, _ = loaded_app(h, net=net2, update=False)
    st, _ = call(app2, "POST", "/api/update")
    h.eq(st, 202, "Update prices starts in the background")
    started.wait(5)
    st, j = call(app2, "POST", "/api/update")
    h.eq(st, 409, "a second update while one runs is refused")
    h.ok(call(app2, "GET", "/api/update/status")[1]["running"], "the status says it is running")
    h.ok("Downloading Card Kingdom's buylist" in call(app2, "GET", "/api/update/status")[1]["message"], "progress: 'Downloading Card Kingdom's buylist...'")
    gate.set()
    for _ in range(100):
        if not app2.update["running"]:
            break
        threading.Event().wait(0.05)
    h.ok(not app2.update["running"] and app2.update["result"]["ok"], "the update finishes")
    h.eq(net2.ck_calls, 1, "and made exactly one CK request")
    msgs = []
    app3, net3 = loaded_app(h, update=False)
    orig = app3._say
    app3._say = lambda t: (msgs.append(t), orig(t))
    app3.run_update()
    h.ok(any(m.startswith("Getting market prices 1 of 1") for m in msgs), "progress: 'Getting market prices 1 of 1...'")
    # what failed is said plainly, and the other side still works
    net3.ck_error = OSError("down")
    app3.run_update()
    r = app3.update["result"]
    h.ok(not r["ok"] and "Card Kingdom's buylist" in r["lines"][0], "a failed CK download is named in the answer")
    h.ok(rows_by_name(call(app3, "GET", "/api/state")[1], "Sol Ring")[0]["mk"] == 100, "the other source's prices stay (market)")
    # nothing runs by itself
    app4, net4 = make_app(h)
    call(app4, "GET", "/api/state")
    h.eq((net4.ck_calls, len(net4.scry_calls)), (0, 0), "no price request without pressing Update prices")
    h.eq(call(app4, "GET", "/api/state")[1]["prices"]["has_ck"], False, "with no prices the page is told so (it offers the button)")

    # real HTTP, 127.0.0.1 only
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    first = probe.getsockname()[1]
    probe.close()
    srv = server.bind_server(app, first, 11)
    try:
        h.eq(srv.server_address[0], "127.0.0.1", "binds 127.0.0.1 only")
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        with urllib.request.urlopen("http://127.0.0.1:%d/api/state" % srv.server_address[1], timeout=5) as r:
            h.eq(json.loads(r.read())["version"], APP_VERSION, "a real request over 127.0.0.1 works")
        req = urllib.request.Request("http://127.0.0.1:%d/api/edit" % srv.server_address[1], data=b"{}", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            h.ok(False, "a real POST without the header is refused")
        except urllib.error.HTTPError as e:
            h.eq(e.code, 403, "a real POST without the header is refused")
        srv.shutdown()
        # a port that is busy is skipped
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        p = busy.getsockname()[1]
        s2 = server.bind_server(app, p, 11)
        h.ok(p < s2.server_address[1] <= p + 10, "the next free port is used when the first is busy")
        s2.server_close()
        busy.close()
    finally:
        srv.server_close()

    # the start-up line
    port = socket.socket()
    port.bind(("127.0.0.1", 0))
    free = port.getsockname()[1]
    port.close()
    out = io.StringIO()
    real_out, real_serve, real_check = sys.stdout, server.Server.serve_forever, server.App.check_for_new_version
    server.Server.serve_forever = lambda self, *a, **k: None
    server.App.check_for_new_version = lambda self, force=False: None
    sys.stdout = out
    try:
        server.main(["--port", str(free), "--no-browser", "--data", h.tmp()])
    finally:
        sys.stdout, server.Server.serve_forever, server.App.check_for_new_version = real_out, real_serve, real_check
    h.eq(out.getvalue().strip(), "Scammer's Kingdom is running at http://127.0.0.1:%d. Close this window to stop." % free, "start-up message")
    h.eq(server.START_PORT, 8901, "the first port tried is 8901")

    # frozen paths
    h.eq(storage.resource_dir(frozen=True, meipass="/tmp/_MEI123"), "/tmp/_MEI123", "frozen: the page is read from sys._MEIPASS")
    h.eq(storage.resource_dir(frozen=False, here="/src"), "/src", "from source: next to server.py")
    h.eq(storage.data_dir(frozen=False, here="/src"), os.path.join("/src", "ScammersKingdom-data"), "from source: data next to server.py")
    h.eq(os.path.basename(storage.data_dir(frozen=True, executable="/apps/x/Scammers.exe")), "ScammersKingdom-data", "frozen: the data folder is ScammersKingdom-data")
    h.eq(os.path.basename(os.path.dirname(storage.data_dir(frozen=True, executable="/apps/x/Scammers.exe"))), "x", "frozen: ...next to the .exe")
    h.ok(os.path.isfile(os.path.join(storage.resource_dir(), "static", "index.html")), "the page file is where the program looks")
    for bad in ({"bag_size": 0}, {"bag_size": "50"}, {"ex_share": 101}, {"bag_fee": -1}, {"nope": 1}, {}):
        h.eq(call(app, "POST", "/api/settings", bad)[0], 400, "settings refuse %r" % (bad,))
    st, j = call(app, "POST", "/api/settings", {"bag_size": 20})
    h.eq(j["settings"]["bag_size"], 20, "settings accept good values")
    st, j = call(app, "POST", "/api/settings/reset")
    h.eq(j["settings"], j["defaults"], "Reset restores the defaults")
    h.eq(j["defaults"], {"bag_size": 50, "bag_fee": 80, "credit_bonus": 30, "ex_share": 80, "vg_share": 60}, "default settings")

    # the newer-version line
    app5, _ = make_app(h)
    tag = {"v": "v1.0.1"}
    calls = []

    def gh(url, headers=None):
        calls.append(url)
        return {"tag_name": tag["v"], "html_url": "https://github.com/Gremlin-Buylisting-Co/Scammers-Kingdom/releases/tag/" + tag["v"]}
    app5.github_get = gh
    app5.check_updates = True
    app5.check_for_new_version()
    h.eq(calls, ["https://api.github.com/repos/Gremlin-Buylisting-Co/Scammers-Kingdom/releases/latest"], "asks GitHub for the latest release")
    nv = call(app5, "GET", "/api/state")[1]["new_version"]
    h.ok(nv and nv["tag"] == "v1.0.1" and nv["url"].startswith("https://github.com/Gremlin-Buylisting-Co/"), "a newer tag gives the header line its link")
    app5.check_for_new_version()
    h.eq(len(calls), 1, "at most once a day")
    tag["v"] = "v1.0.0"
    app5.check_for_new_version(force=True)
    h.eq(call(app5, "GET", "/api/state")[1]["new_version"], None, "the same version shows nothing")
    h.ok(updates.is_newer("v1.10.0", "1.9.9") and not updates.is_newer("1.0", "1.0.0") and not updates.is_newer("junk", "1.0.0"), "version comparison")
    app6, _ = make_app(h)
    app6.check_updates = True

    def boom(url, headers=None):
        raise OSError("down")
    app6.github_get = boom
    app6.check_for_new_version()
    h.eq(call(app6, "GET", "/api/state")[1]["new_version"], None, "a failed check is silent")
    app6.github_get = lambda url, headers=None: {"tag_name": "v9.0.0", "html_url": "https://evil.example/x"}
    app6.check_for_new_version(force=True)
    h.eq(call(app6, "GET", "/api/state")[1]["new_version"], None, "a release link on another site is ignored")


# ------------------------------------------------------------------ HAND
def hand_tests(h):
    h.section("HAND")
    rows = [fx.ck_raw("Bolt", "Ed A", "1.00", 4), fx.ck_raw("Bolt Bend", "Ed A", "0.50", 4), fx.ck_raw("Lightning Bolt", "Ed B", "0.90", 4),
            fx.ck_raw("Firebolt", "Ed C", "0.40", 4), fx.ck_raw("Jötun Grunt", "Alliances", "0.10", 4),
            fx.ck_raw("Ertai, Wizard Adept", "Urza's Legacy", "1.20", 2), fx.ck_raw("Zebra", "Ed A", "0", 0)]
    rows += [fx.ck_raw("Zap Number %02d" % i, "Ed A", "0.10", 1) for i in range(60)]
    net = fx.FakeNet()
    net.ck_rows = rows
    app, _ = make_app(h, net=net)
    st, j = call(app, "GET", "/api/search?q=bolt")
    h.eq(st, 200, "search answers")
    h.eq(j["ok"], False, "the search needs the first price download")
    h.eq(j["message"], "Press Update prices first: the card list comes with Card Kingdom's prices.", "before any prices the search says to press Update prices")
    st, j = call(app, "POST", "/api/add", {"sid": fx.printing_id("Bolt"), "finish": "nonfoil"})
    h.eq(st, 400, "adding before any prices is refused")
    app.run_update()
    names = lambda q: [i["name"] for i in call(app, "GET", "/api/search", query={"q": q})[1]["items"]]
    h.eq(names("b"), [], "one letter finds nothing (from 2 letters)")
    r = names("bolt")
    h.eq(r[0], "Bolt", "a whole-name match comes first")
    h.ok(r[1].startswith("Bolt "), "then names starting with the text")
    late = [n for n in r if not n.startswith("Bolt")]
    h.ok(late[:2] == ["Firebolt", "Lightning Bolt"] and r.index(late[0]) > max(i for i, n in enumerate(r) if n.startswith("Bolt")),
         "then names containing it")
    h.eq(len(names("zap")), 40, "at most 40 printings (60 match)")
    h.eq(names("JOTUN"), ["Jötun Grunt"], "accents and case are ignored (JOTUN finds Jötun)")
    h.eq(names("jötun"), ["Jötun Grunt"], "an accent in the search works too")
    h.eq(names("ertai wizard"), ["Ertai, Wizard Adept"], "punctuation in the name does not get in the way")
    item = call(app, "GET", "/api/search", query={"q": "ertai"})[1]["items"][0]
    h.eq((item["cash"], item["buying"], item["edition"], item["finish"]), (120, True, "Urza's Legacy", "nonfoil"), "each printing shows what CK pays")
    h.eq(call(app, "GET", "/api/search", query={"q": "zebra"})[1]["items"][0]["buying"], False, "a printing CK is not buying says so ('not buying')")
    h.eq(call(app, "GET", "/api/search", query={"q": "x"})[1]["items"], [], "short searches cost nothing")

    # add
    sid = fx.printing_id("Lightning Bolt")
    st, j = call(app, "POST", "/api/add", {"sid": sid, "finish": "nonfoil", "condition": "EX", "amount": 3})
    h.eq(st, 200, "add answers 200")
    row = j["rows"][0]
    h.eq((row["name"], row["cond"], row["qty"], row["src"], row["sid"]), ("Lightning Bolt", "EX", 3, "hand", sid), "added with the chosen condition, Scryfall id and source 'hand'")
    h.ok(j["need_market"], "a card with no market price yet says so")
    h.eq(row["mk"], None, "its market price waits for Update prices")
    app.run_update()
    st, s = call(app, "GET", "/api/state")
    h.eq(rows_by_name(s, "Lightning Bolt")[0]["mk"], 150, "the next update gets its price from Scryfall by id")
    ident = [c for c in net.scry_calls[-1][1]["identifiers"]]
    h.ok({"id": sid} in ident, "...asked for as {\"id\": ...}")
    st, j = call(app, "POST", "/api/add", {"sid": sid, "finish": "nonfoil", "condition": "EX", "amount": 2})
    h.eq((len(j["rows"]), j["rows"][0]["qty"]), (1, 5), "the same printing and condition is merged (quantities added)")
    st, j = call(app, "POST", "/api/add", {"sid": sid, "finish": "nonfoil", "condition": "NM", "amount": 1})
    h.eq(len(j["rows"]), 2, "another condition is its own row")
    for bad in ({"sid": sid, "finish": "nonfoil", "amount": 0}, {"sid": sid, "finish": "nonfoil", "amount": 1000},
                {"sid": sid, "finish": "nonfoil", "amount": "2"}, {"sid": sid, "finish": "nonfoil", "condition": "XX"},
                {"sid": "nope", "finish": "nonfoil"}, {"sid": sid, "finish": "foil"}):
        h.eq(call(app, "POST", "/api/add", bad)[0], 400, "add refuses %r" % (bad,))

    # editing: one validated route
    ex = [r for r in j["rows"] if r["cond"] == "EX"][0]
    nm = [r for r in j["rows"] if r["cond"] == "NM"][0]
    writes = []
    real = storage.write_json
    storage.write_json = lambda path, obj, *a, **k: (writes.append(os.path.basename(path)), real(path, obj, *a, **k))[1]
    try:
        st, j2 = call(app, "POST", "/api/edit", {"op": "qty", "id": ex["id"], "qty": 9})
    finally:
        storage.write_json = real
    h.eq((st, rows_by_name(j2, "Lightning Bolt", cond="EX")[0]["qty"]), (200, 9), "edit quantity in place")
    h.eq(writes.count("collection.json"), 1, "an edit writes collection.json once")
    st, j2 = call(app, "POST", "/api/edit", {"op": "condition", "id": nm["id"], "condition": "EX"})
    h.eq([(r["cond"], r["qty"]) for r in j2["rows"]], [("EX", 10)], "a condition change that makes two rows the same merges them (quantities added)")
    h.ok("Merged" in j2["label"], "the toast says it merged")
    st, j2 = call(app, "POST", "/api/edit", {"op": "condition", "id": ex["id"], "condition": "VG"})
    h.eq(rows_by_name(j2, "Lightning Bolt")[0]["cond"], "VG", "edit condition")
    # change the printing
    st, j2 = call(app, "POST", "/api/edit", {"op": "printing", "id": ex["id"], "sid": fx.printing_id("Bolt"), "finish": "nonfoil"})
    r2 = j2["rows"][0]
    h.eq((r2["name"], r2["qty"], r2["cond"], r2["id"]), ("Bolt", 10, "VG", ex["id"]), "Change keeps quantity and condition, and the row")
    call(app, "POST", "/api/add", {"sid": fx.printing_id("Bolt"), "finish": "nonfoil", "condition": "VG", "amount": 2})
    st, j2 = call(app, "GET", "/api/state")
    h.eq(len(j2["rows"]), 1, "adding the same printing + condition by hand joins the row")
    h.eq(call(app, "POST", "/api/edit", {"op": "printing", "id": ex["id"], "sid": "x", "finish": "nonfoil"})[0], 400, "a printing CK does not list is refused")
    # remove, bulk
    for i in range(3):
        call(app, "POST", "/api/add", {"sid": fx.printing_id("Zebra"), "finish": "nonfoil", "condition": ["NM", "EX", "VG"][i], "amount": 1})
    st, s = call(app, "GET", "/api/state")
    ids = [r["id"] for r in s["rows"] if r["name"] == "Zebra"]
    st, s = call(app, "POST", "/api/edit", {"op": "condition", "ids": ids, "condition": "EX"})
    h.eq(rows_by_name(s, "Zebra")[0]["qty"], 3, "set condition for a selection (they all merge into one)")
    st, s = call(app, "POST", "/api/edit", {"op": "qty", "id": rows_by_name(s, "Zebra")[0]["id"], "qty": 0})
    h.eq(rows_by_name(s, "Zebra"), [], "quantity 0 removes the row")
    call(app, "POST", "/api/add", {"sid": fx.printing_id("Zebra"), "finish": "nonfoil", "amount": 1})
    call(app, "POST", "/api/add", {"sid": fx.printing_id("Jötun Grunt"), "finish": "nonfoil", "amount": 1})
    st, s = call(app, "GET", "/api/state")
    ids = [r["id"] for r in s["rows"] if r["name"] in ("Zebra", "Jötun Grunt")]
    st, s = call(app, "POST", "/api/edit", {"op": "remove", "ids": ids})
    h.eq([r["name"] for r in s["rows"]], ["Bolt"], "remove selected")
    # validation
    bid = s["rows"][0]["id"]
    for bad, code in (({"op": "qty", "id": 99999, "qty": 1}, 404), ({"op": "remove", "id": 99999}, 404),
                      ({"op": "qty", "id": bid, "qty": -1}, 400), ({"op": "qty", "id": bid, "qty": "3"}, 400),
                      ({"op": "qty", "id": bid, "qty": True}, 400), ({"op": "qty", "id": bid, "qty": 10000}, 400),
                      ({"op": "qty", "id": bid, "qty": 1.5}, 400), ({"op": "condition", "id": bid, "condition": "XX"}, 400),
                      ({"op": "condition", "id": bid}, 400), ({"op": "zap", "id": bid}, 400), ({"op": "qty", "qty": 1}, 400),
                      ({"op": "remove", "ids": []}, 400), ({"op": "remove", "ids": ["a"]}, 400)):
        h.eq(call(app, "POST", "/api/edit", bad)[0], code, "edit %r answers %d" % (bad, code))
    h.eq(call(app, "POST", "/api/edit", {"op": "qty", "id": bid, "qty": 9999})[0], 200, "quantity up to 9999 is fine")

    # undo: last 20
    app2, _ = make_app(h, net=net)
    app2.run_update()
    st, j = call(app2, "POST", "/api/add", {"sid": fx.printing_id("Bolt"), "finish": "nonfoil", "amount": 1})
    rid = j["rows"][0]["id"]
    for q in range(2, 28):
        call(app2, "POST", "/api/edit", {"op": "qty", "id": rid, "qty": q})
    st, j = call(app2, "GET", "/api/state")
    h.eq((j["undo"]["count"], j["rows"][0]["qty"]), (20, 27), "the last 20 changes can be undone")
    for i in range(20):
        st, j = call(app2, "POST", "/api/undo")
    h.eq(j["rows"][0]["qty"], 7, "twenty undos take it back 20 steps")
    st, j = call(app2, "POST", "/api/undo")
    h.eq(st, 409, "nothing more to undo after 20")
    h.eq(j["error"], "Nothing to undo.", "...and says so")
    st, j = call(app2, "POST", "/api/edit", {"op": "qty", "id": rid, "qty": 40})
    h.ok(j["label"] == "Quantity of Bolt is now 40." and j["undo"]["label"] == j["label"], "each change has a label for the toast and Undo button")
    st, j = call(app2, "POST", "/api/undo")
    h.ok(j["label"].startswith("Undone: ") and j["rows"][0]["qty"] == 7, "undo reports what it undid")


# ------------------------------------------------------------------ PUBLIC
def tracked_files():
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
        files = [f for f in out if f.strip()]
        if files:
            return files
    except Exception:
        pass
    files = []
    for base, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "ScammersKingdom-data", "build", "dist")]
        for n in names:
            files.append(os.path.relpath(os.path.join(base, n), ROOT).replace(os.sep, "/"))
    return files


def public_tests(h):
    h.section("PUBLIC")
    files = tracked_files()
    email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+")
    # words that must never appear in what is published, stored scrambled so this file does not contain them
    scrambled = ['neovgentr', 'yrqtre', 'fryy pneg', 'ybpxrq cevpr', 'sebmra cevpr', 'serrmr', 'cevpr qebc',
                 'fuvccvat qrnqyvar', 'noh tnzrf', 'fgne pvgl', 'pneqznexrg', 'pneqfcurer', "fpnzzre'f nqinagntr",
                 'fpnzzref nqinagntr']
    banned = [codecs.decode(w, "rot13") for w in scrambled]
    bad_mail, bad_word, checked = [], [], 0
    for f in files:
        if f.endswith((".png", ".ico", ".jpg")):
            continue
        try:
            text = read(f)
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        checked += 1
        for m in email.findall(text):
            if not re.search(r"\.(js|py|json|css|html|md)$", m):
                bad_mail.append((f, m))
        low = text.lower().replace("’", "'")
        for w in banned:
            if w in low:
                bad_word.append((f, "banned phrase #%d" % banned.index(w)))
    h.ok(checked > 10, "scanned %d tracked files" % checked)
    h.eq(bad_mail, [], "no email address in any tracked file")
    h.eq(bad_word, [], "none of the banned words in any tracked file")
    h.ok(".gitignore" in files and "ScammersKingdom-data/" in read(".gitignore"), "the data folder is ignored by git")
    h.ok(not any(f.startswith("ScammersKingdom-data") for f in files), "no user data is tracked")
    data = [f for f in files if f.endswith(".json")]
    h.eq(data, [], "no data file is tracked")


# ------------------------------------------------------------------ DOCS
def docs_tests(h):
    h.section("DOCS")
    readme = read("README.md")
    for what, needle in (("the Releases page", "Releases"), ("the unsigned warning", "More info"), ("Run anyway", "Run anyway"),
                         ("the data folder", "ScammersKingdom-data"), ("running from source", "python server.py"),
                         ("Card Kingdom's buylist as a price source", "Card Kingdom"), ("Scryfall", "Scryfall"),
                         ("shoptcg.online", "shoptcg.online"), ("step 1", "Add cards"), ("step 2", "Your binder"),
                         ("step 3", "Plan a shipment"), ("50-card bags", "50")):
        h.ok(needle in readme, "README mentions %s" % what)
    h.ok("nothing else" in readme.lower() or "talks to nothing" in readme.lower(), "README says what the tool talks to (nothing else)")
    h.ok("https://www.cardkingdom.com/static/csvImport" in readme, "README links Card Kingdom's CSV import page where the CK import file is described")
    h.ok("CK import file" in readme, "README describes the CK import file")
    h.ok(read("CHANGELOG.md").lstrip().startswith("# Changelog") and "1.0.0" in read("CHANGELOG.md"), "CHANGELOG has 1.0.0")
    h.eq(APP_VERSION, "1.0.0", "APP_VERSION is 1.0.0")
    bat = read("build_exe.bat")
    h.ok("PyInstaller" in bat and "--onefile" in bat and "--console" in bat and "static" in bat, "build_exe.bat: PyInstaller one file, console, with the page")
    h.ok("pip install" in bat or "pip install" in bat.lower(), "build_exe.bat says how to get PyInstaller if it is missing")
    h.ok("server.py" in bat, "build_exe.bat builds server.py")
    page = read("static/index.html")
    h.ok('href="https://www.cardkingdom.com/static/csvImport"' in page and 'rel="noopener noreferrer"' in page.split("csvImport")[1].split(">")[0] and
         'target="_blank"' in page.split("csvImport")[1].split(">")[0], "the page links the CK CSV import page, opening in a new tab with rel=noopener noreferrer")
    h.ok("How to upload it: <a" in page and "Card Kingdom's CSV import page</a>" in page, "the link line reads 'How to upload it: Card Kingdom's CSV import page'")
    h.ok("On Card Kingdom, choose your file, then match the four columns: card name, edition, foil, quantity." in page, "the instruction line is on the page")
    h.ok(page.index("Download CK import file") < page.index("How to upload it"), "the link comes right under the Download CK import file button")
    h.ok("Prices: Card Kingdom's buylist and TCGplayer market (via Scryfall). Not affiliated with Card Kingdom." in page, "footer text")
    h.ok("MOCKUP" not in page and "Notes for the owner" not in page, "no mockup bar or owner notes in the page")
    ext = re.findall(r'(?:src|href)="(https?://[^"]+)"', page)
    h.ok(all(u.startswith("https://www.cardkingdom.com/") for u in ext), "the only fixed links in the page go to Card Kingdom")
    h.ok("<script src" not in page and "@import" not in page and "fonts.googleapis" not in page and "<link" not in page, "no external fonts, libraries or CDNs")
    h.ok("clipboard" not in page.lower(), "no copy-to-clipboard list")
