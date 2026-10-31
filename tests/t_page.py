"""FILTER and PAGE (run the page's own script under node), and the picture address part of HAND."""
import json
import os
import re
import shutil
import subprocess

import ck_prices
import plan
from harness import ROOT, call, make_app

HTML = os.path.join(ROOT, "static", "index.html")
RUNNER = os.path.join(ROOT, "tests", "page_harness.js")
NODE = shutil.which("node")


def node(job):
    job = dict(job, html=HTML)
    p = subprocess.run([NODE, RUNNER], input=json.dumps(job).encode("utf-8"), capture_output=True, timeout=60)
    if p.returncode != 0 or not p.stdout.strip():
        raise RuntimeError("node failed: " + p.stderr.decode("utf-8", "replace")[-800:])
    return json.loads(p.stdout.decode("utf-8"))


def srow(i, name, set_name="Ed", code="ed", number="1", cash=100, mk=200, qty=1, wants=999, cond="NM", finish="nonfoil",
         src="import", back=False, link=True, edition=None):
    return {"id": i, "name": name, "set_name": set_name, "set_code": code, "number": number, "finish": finish, "cond": cond,
            "qty": qty, "src": src, "sid": "sid-%d" % i, "unknown": False, "mk": mk, "mk_src": "scryfall", "back": back,
            "ck": None if cash is None else {"cash": cash, "wants": wants, "name": name, "edition": edition or set_name, "variation": "", "url": ""},
            "link": ("https://www.cardkingdom.com/purchasing/mtg_singles?x=%d" % i) if (link and cash is not None) else None}


def base_state(h, rows, has_ck=True):
    app, _ = make_app(h)
    s = app.snapshot()
    s["rows"] = rows
    s["prices"] = {"has_ck": has_ck, "ck_as_of": "2026-10-31T09:42:00-07:00", "market_as_of": "2026-10-31T09:42:00-07:00"}
    s["back"]["count"] = sum(1 for r in rows if r["back"])
    return s


def many_rows(n):
    rows = []
    for i in range(1, n + 1):
        rows.append(srow(i, "Card %03d" % i, set_name="Set %d" % (i % 3), code="s%d" % (i % 3), number=str(i),
                         cash=10 + (i * 7) % 90, mk=100 + (i * 13) % 200, qty=1 + i % 3))
    return rows


def pure(rows, S, exprs):
    return node({"mode": "pure", "data": {"rows": rows, "S": S}, "exprs": exprs})["results"]


# ------------------------------------------------------------------ FILTER
def filter_tests(h):
    h.section("FILTER")
    if not NODE:
        h.skip("node is not installed: FILTER needs it")
        return
    S = dict(plan.DEFAULT_SETTINGS)
    rows = [srow(1, "Sol Ring", "Commander Masters", "cmm", "410"), srow(2, "Sol Talisman", "Commander Masters", "cmm", "412", cash=50),
            srow(3, "Jötun Grunt", "Alliances", "all", "1", cash=10), srow(4, "Lightning Bolt", "Magic 2011", "m11", "149", back=True),
            srow(5, "Promo Bolt", "Promos", "pf18", "2018f", src="hand", cash=30), srow(6, "Cultivate", "Commander 2021", "c21", "246", back=True),
            srow(7, "Hidden", "Nowhere", "nw", "9", cash=None)]

    def names(q, **o):
        opts = dict({"only": False, "backOnly": False, "handOnly": False, "q": q}, **o)
        e = "api.visibleRows(api.rankRows(D.rows,D.S)," + json.dumps(opts) + ").map(x=>x.r.name).sort()"
        return pure(rows, S, [e])[0]
    h.eq(names("sol"), ["Sol Ring", "Sol Talisman"], "any part of the name")
    h.eq(names("olt"), ["Lightning Bolt", "Promo Bolt"], "a part from the middle of a name")
    h.eq(names("commander"), ["Cultivate", "Sol Ring", "Sol Talisman"], "set name")
    h.eq(names("cmm"), ["Sol Ring", "Sol Talisman"], "set code")
    h.eq(names("410"), ["Sol Ring"], "collector number")
    h.eq(names("#149"), ["Lightning Bolt"], "a number written with #")
    h.eq(names("2018f"), ["Promo Bolt"], "a promo number like 2018f")
    h.eq(names("jotun"), ["Jötun Grunt"], "accents ignored (jotun finds Jötun)")
    h.eq(names("JÖTUN"), ["Jötun Grunt"], "case and accents ignored together")
    h.eq(names("ring cmm"), ["Sol Ring"], "every word must match somewhere")
    h.eq(names("   "), sorted(r["name"] for r in rows), "blank search shows everything")
    h.eq(names("zzz"), [], "no match gives an empty list")
    h.eq(names("sol", only=True), ["Sol Ring", "Sol Talisman"], "combines with 'Only cards CK is buying'")
    h.eq(names("", only=True), sorted(r["name"] for r in rows if r["ck"]), "'Only cards CK is buying' leaves out the others")
    h.eq(names("", backOnly=True), ["Cultivate", "Lightning Bolt"], "the Back chip")
    h.eq(names("bolt", backOnly=True), ["Lightning Bolt"], "the Back chip combines with the search")
    h.eq(names("", handOnly=True), ["Promo Bolt"], "the Added by hand chip")
    h.eq(names("bolt", handOnly=True), ["Promo Bolt"], "the hand chip combines with the search")
    h.eq(names("bolt", handOnly=True, backOnly=True), [], "both chips together")
    # parity of the accent / punctuation rules with the program's own search
    samples = ["Jötun Grunt", "Sheoldred, the Apocalypse", "  Spaced   out ", "ÀÉÎõü", "Urza's Saga", "#12", "Æther Vial", "Lim-Dûl's Vault"]
    js = pure([], S, ["D.s.map(api.norm)".replace("D.s", json.dumps(samples))])[0]
    h.eq(js, [ck_prices.norm(s) for s in samples], "the page and the program normalize text the same way")
    # paging with filters
    rows2 = many_rows(250)
    ex = ["(function(){var l=api.visibleRows(api.rankRows(D.rows,D.S),{only:true,q:''});"
          "return [l.length,api.pageSlice(l,0,100).rows.length,api.pageSlice(l,1,100).rows.length,api.pageSlice(l,2,100).rows.length,"
          "api.pageSlice(l,9,100).page,api.pageSlice(l,0,100).pages,api.pageSlice(l,2,100).from,api.pageSlice(l,2,100).to];})()",
          "(function(){var l=api.visibleRows(api.rankRows(D.rows,D.S),{only:true,q:'s1'});return [l.length,api.pageSlice(l,0,100).pages];})()",
          "api.PAGE"]
    r = pure(rows2, S, ex)
    h.eq(r[0], [250, 100, 100, 50, 2, 3, 200, 250], "100 rows per page: 100, 100, 50; a page past the end clamps to the last")
    h.eq(r[1], [84, 1], "a filter shrinks the paging (84 of 250 have set code s1)")
    h.eq(r[2], 100, "PAGE is 100")
    # bulk actions: only the filtered rows
    ex = ["(function(){var l=api.visibleRows(api.rankRows(D.rows,D.S),{only:false,q:'bolt'});"
          "return api.selectedVisible(l,{1:true,4:true,5:true,6:true}).sort();})()"]
    h.eq(pure(rows, S, ex)[0], [4, 5], "bulk actions apply to the filtered rows only (1 and 6 are selected but hidden)")

    # the page itself: no match offers 'Add it with + Add a card', text carried over
    state = base_state(h, rows2[:5])
    out = node({"mode": "page", "state": state, "steps": [
        {"op": "set", "id": "search", "props": {"value": "zzz"}},
        {"op": "fire", "id": "search", "event": "input", "ev": {"value": "zzz"}},
        {"op": "read", "id": "tbody", "prop": "innerHTML"},
        {"op": "read", "id": "showing", "prop": "textContent"},
        {"op": "docclick", "target": {"id": "addFromFind", "closest": {}}},
        {"op": "read", "id": "asearch", "prop": "value"},
        {"op": "read", "id": "addrow", "prop": "hidden"},
    ]})
    res = out["results"]
    h.ok("Add it with + Add a card" in res[0] and "zzz" in res[0], "no match: 'Add it with + Add a card'")
    h.eq(res[1], "Showing 0 of 5 cards", "'Showing N of M cards'")
    h.eq((res[2], res[3]), ("zzz", False), "the button opens the add row with the text carried over")
    out = node({"mode": "page", "state": state, "steps": [
        {"op": "fire", "id": "search", "event": "input", "ev": {"value": "card 00"}},
        {"op": "read", "id": "showing", "prop": "textContent"},
        {"op": "read", "id": "clearFind", "prop": "hidden"},
        {"op": "fire", "id": "clearFind", "event": "click"},
        {"op": "read", "id": "showing", "prop": "textContent"},
        {"op": "read", "id": "search", "prop": "value"},
    ]})["results"]
    h.eq((out[0], out[1]), ("Showing 5 of 5 cards", False), "typing filters as you go; a clear button appears")
    h.eq((out[2], out[3]), ("Showing 5 of 5 cards", ""), "the clear button empties the filter")
    # bulk actions in the page apply only to the visible rows
    rs = [srow(1, "Alpha", cash=50), srow(2, "Bravo", cash=40), srow(3, "Alpha Two", cash=30)]
    state = base_state(h, rs)
    out = node({"mode": "page", "state": state, "responses": {"POST /api/edit": dict(state, label="done")}, "steps": [
        {"op": "rowEvent", "target": {"kind": "rowchk", "id": 2, "checked": True}},
        {"op": "rowEvent", "target": {"kind": "rowchk", "id": 1, "checked": True}},
        {"op": "set", "id": "search", "props": {"value": "alpha"}},
        {"op": "fire", "id": "search", "event": "input", "ev": {"value": "alpha"}},
        {"op": "clearcalls"},
        {"op": "fire", "id": "bulkRemove", "event": "click"},
        {"op": "calls"},
    ]})["results"]
    posts = [c for c in out[0] if c["method"] == "POST"]
    h.eq(json.loads(posts[0]["body"]), {"op": "remove", "ids": [1]}, "Remove selected leaves out the selected row the filter hides")


# ------------------------------------------------------------------ PAGE
def total_line(html):
    m = re.search(r"Total: <b>([\d,]+) cards</b>, \$([\d,.]+) (?:cash|credit), bag fees \$([\d,.]+), you get <b>\$([\d,.]+)</b>", html)
    return m.groups() if m else None


def money(c):
    return "%s.%02d" % (format(c // 100, ","), c % 100)


def page_tests(h):
    h.section("PAGE")
    src = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    # 1.0.1: the search list's highlighted option must not use the row-action class "act" (invisible until hovered, with padding): the option vanished and the list shifted under the mouse
    h.ok('toggle("hl"' in src and '.pl li.hl{' in src and 'toggle("act"' not in src and ".pl li.act" not in src,
         "the search list highlights the option under the mouse with its own class, not the row-action links' hidden style")
    if not NODE:
        h.skip("node is not installed: PAGE needs it")
        return
    S = dict(plan.DEFAULT_SETTINGS)
    # first visit: Step 1
    empty = base_state(h, [], has_ck=False)
    r = node({"mode": "page", "state": empty, "steps": [
        {"op": "read", "id": "p1", "prop": "hidden"}, {"op": "read", "id": "p2", "prop": "hidden"}, {"op": "read", "id": "p3", "prop": "hidden"},
        {"op": "read", "id": "status", "prop": "innerHTML"}, {"op": "read", "id": "update", "prop": "disabled"},
        {"op": "attr", "id": "steps:0", "name": "aria-current"}]})
    res = r["results"]
    h.ok("error" not in r, "the page script runs to the end without an error")
    h.eq(res[:3], [False, True, True], "with no cards the page opens on Step 1, one step visible")
    h.ok("No prices yet" in res[3] and "Update prices" in res[3], "with no prices the header says so and points at Update prices")
    h.eq(res[4], False, "Update prices is a button the person presses (nothing runs by itself)")
    # with cards: Step 2, 100 rows, then the plan
    rows = many_rows(250)
    state = base_state(h, rows)
    steps = [
        {"op": "read", "id": "p1", "prop": "hidden"}, {"op": "read", "id": "p2", "prop": "hidden"},
        {"op": "count", "id": "tbody", "pattern": "<tr class="},
        {"op": "read", "id": "pagerTop", "prop": "innerHTML"},
        {"op": "read", "id": "showing", "prop": "textContent"},
        {"op": "count", "id": "tbody", "pattern": "Sell on CK"},
        {"op": "read", "id": "summary", "prop": "innerHTML"},
        {"op": "docclick", "target": {"id": "", "closest": {".pgnext": {}}}},
        {"op": "read", "id": "pagerTop", "prop": "innerHTML"},
        {"op": "count", "id": "tbody", "pattern": "<tr class="},
        {"op": "fireList", "list": "#steps button", "index": 2, "event": "click"},
        {"op": "read", "id": "p2", "prop": "hidden"}, {"op": "read", "id": "p3", "prop": "hidden"},
        {"op": "read", "id": "plan", "prop": "innerHTML"},
        {"op": "read", "id": "ckfile", "prop": "href"}, {"op": "read", "id": "csv", "prop": "href"},
        {"op": "fireList", "list": "#pay button", "index": 1, "event": "click"},
        {"op": "read", "id": "plan", "prop": "innerHTML"},
        {"op": "read", "id": "csv", "prop": "href"},
        {"op": "set", "id": "bags", "props": {"value": "3"}},
        {"op": "fire", "id": "bags", "event": "input", "ev": {"value": "3"}},
        {"op": "read", "id": "plan", "prop": "innerHTML"},
        {"op": "read", "id": "ckfile", "prop": "href"},
        {"op": "read", "id": "bagsLabel", "prop": "textContent"},
        {"op": "fireList", "list": "#steps button", "index": 0, "event": "click"},
        {"op": "read", "id": "p1", "prop": "hidden"}, {"op": "read", "id": "p3", "prop": "hidden"},
    ]
    r = node({"mode": "page", "state": state, "steps": steps})
    res = r["results"]
    h.eq(res[:2], [True, False], "with cards the page opens on Step 2")
    h.eq(res[2], 100, "Step 2 shows 100 rows per page")
    h.ok("1-100" in res[3] and "of 250" in res[3], "the pager says 1-100 of 250")
    h.eq(res[4], "Showing 250 of 250 cards", "'Showing N of M cards'")
    h.eq(res[5], 100, "every card CK is buying has a 'Sell on CK' link")
    h.ok("<b>" in res[6] and "cash" in res[6] and "credit" in res[6], "the summary line shows cash and credit")
    h.ok("101-200" in res[7] and res[8] == 100, "Next shows rows 101-200")
    h.eq(res[9:11], [True, False], "the step bar moves to Step 3 and only that step shows")

    def expect(pay, bags):
        p = plan.build_plan(rows, S, bags, pay)
        return (format(p["taken"], ","), money(p["total"]), money(p["fees"]), money(p["get"])), p
    want, p = expect("cash", 2)
    h.eq(total_line(res[11]), want, "plan numbers match plan.py (2 bags, cash): cards, total, fees, you get")
    h.eq(len(re.findall(r'<details class="bag">', res[11])), len(p["bags"]), "one folded list per bag")
    for b in p["bags"]:
        h.ok("Bag %d: %d cards, $%s cash, bag fee $%s, you get $%s" % (b["n"], b["cards"], money(b["total"]), money(b["fee"]), money(b["get"])) in res[11],
             "bag %d line matches plan.py" % b["n"])
    h.eq(res[12], "/download/ck-import.csv?bags=2", "Download CK import file points at the shipment's file")
    h.eq(res[13], "/download/plan.csv?bags=2&pay=cash", "Download CSV points at the plan spreadsheet")
    want, p = expect("credit", 2)
    h.eq(total_line(res[14]), want, "plan numbers match plan.py in credit")
    h.eq(res[15], "/download/plan.csv?bags=2&pay=credit", "the CSV link follows Cash / Credit")
    want, p = expect("credit", 3)
    h.eq(total_line(res[16]), want, "plan numbers match plan.py (3 bags, credit)")
    h.eq(res[17], "/download/ck-import.csv?bags=3", "the CK file link follows the bag count")
    h.eq(res[18], "How many 50-card bags?", "the question uses the bag size")
    h.eq(res[19:21], [False, True], "the step bar goes back to Step 1")
    # partly filled last bag and not enough cards
    few = [srow(1, "Only", cash=100, mk=200, qty=60)]
    r = node({"mode": "page", "state": base_state(h, few), "steps": [
        {"op": "fireList", "list": "#steps button", "index": 2, "event": "click"},
        {"op": "read", "id": "plan", "prop": "innerHTML"}]})["results"]
    h.ok("10 cards" in r[0] and "40 more cards would fill it" in r[0], "a partly filled last bag says how many more would fill it")
    h.ok("more than you can fill" in r[0], "fewer copies than the bags hold is said")
    # CK's wanted limit, in the page and in plan.py, with one printing in two conditions
    capped = [srow(1, "Capped", cash=90, mk=100, qty=10, wants=2), srow(2, "Free", cash=50, mk=100, qty=2),
              srow(3, "Same", cash=80, mk=100, qty=2, wants=3, cond="EX"), srow(4, "Same", cash=80, mk=100, qty=2, wants=3, cond="NM")]
    capped[3]["sid"] = capped[2]["sid"]
    r = node({"mode": "page", "state": base_state(h, capped), "steps": [
        {"op": "fireList", "list": "#steps button", "index": 2, "event": "click"},
        {"op": "read", "id": "plan", "prop": "innerHTML"}]})["results"]
    p = plan.build_plan(capped, S, 2, "cash")
    h.eq(p["taken"], 2 + 2 + 3, "(plan.py takes only what CK wants: 2 + 2 + 3)")
    h.eq(total_line(r[0]), (str(p["taken"]), money(p["total"]), money(p["fees"]), money(p["get"])), "the page's plan respects CK's wanted limit the same way plan.py does")
    # importing from the page: a shoptcg.online file, then My Collection with the Replace / Add choice
    rows3 = [srow(1, "Imported", cash=50)]
    st3 = base_state(h, rows3)
    r = node({"mode": "page", "state": base_state(h, [], has_ck=True), "responses": {
        "POST /api/import/shoptcg": dict(st3, label="Replaced.", last_import={"copies": 1, "rows": 1, "mode": "replace", "skipped": [], "skipped_count": 0, "other_copies": 0}),
        "POST /api/import/collection": dict(st3, ok=True, cards=5, mine=3, label="Added.", unplaceable=[], unplaceable_count=0)}, "steps": [
        {"op": "fireList", "list": "#mode button", "index": 1, "event": "click"},
        {"op": "fire", "id": "shopIn", "event": "change", "ev": {"files": [{"text": "a,b\n1,2\n"}]}},
        {"op": "wait", "ms": 60},
        {"op": "calls"},
        {"op": "read", "id": "p2", "prop": "hidden"},
        {"op": "clearcalls"},
        {"op": "fire", "id": "fileIn", "event": "change", "ev": {"files": [{"text": "x\n"}]}},
        {"op": "wait", "ms": 60},
        {"op": "read", "id": "modal", "prop": "hidden"},
        {"op": "read", "id": "modalText", "prop": "textContent"},
        {"op": "fire", "id": "mReplace", "event": "click"},
        {"op": "wait", "ms": 60},
        {"op": "calls"},
        {"op": "read", "id": "modal", "prop": "hidden"},
    ]})["results"]
    imp = [c for c in r[0] if c["method"] == "POST"][0]
    h.ok(imp["url"] == "/api/import/shoptcg?mode=add" and imp["body"] == "a,b\n1,2\n", "dropping a file sends it with the chosen Replace / Add mode")
    h.eq(r[1], False, "after an import the page moves to Step 2")
    h.eq(r[2], False, "Import My Collection asks Replace / Add / Cancel when the binder has cards")
    h.ok("Replace your binder (3 cards) with the 5 cards in this file" in r[3], "...naming both counts")
    urls = [c["url"] for c in r[4] if c["method"] == "POST"]
    h.eq(urls, ["/api/import/collection?mode=check", "/api/import/collection?mode=replace"], "the file is checked first, then the choice is sent to the program")
    h.eq(r[5], True, "the question closes")
    # new cards are being looked up: 'looking up' instead of 'not buying'; no CK prices: say to press Update prices
    waiting = [dict(srow(1, "Fresh", cash=None, mk=None), unresolved=True), srow(2, "Known", cash=50)]
    st4 = base_state(h, waiting)
    st4["lookup"] = {"running": True, "count": 1, "message": "Getting market prices for 1 new card...", "error": None}
    r = node({"mode": "page", "state": st4, "responses": {"GET /api/lookup/status": {"running": True}}, "steps": [
        {"op": "read", "id": "tbody", "prop": "innerHTML"}, {"op": "read", "id": "summary", "prop": "textContent"},
        {"op": "read", "id": "status", "prop": "textContent"}]})["results"]
    h.ok("Fresh" in r[0] and "looking up\u2026" in r[0] and r[0].count("not buying") == 0, "a card still being looked up shows 'looking up...', not 'not buying'")
    h.eq(r[1], "Getting market prices for 1 new card...", "the summary says new cards are being looked up, not 'CK is buying 0'")
    h.eq(r[2], "Getting market prices for 1 new card...", "the header says so too")
    st5 = base_state(h, waiting)
    st5["lookup"] = {"running": False, "count": 1, "message": "", "error": "Market prices for 1 card could not be fetched. Press Update prices to try again."}
    r = node({"mode": "page", "state": st5, "steps": [
        {"op": "read", "id": "tbody", "prop": "innerHTML"}, {"op": "read", "id": "warnText", "prop": "textContent"},
        {"op": "read", "id": "warn", "prop": "hidden"}]})["results"]
    h.ok("not looked up" in r[0], "after a failed lookup the card says 'not looked up'")
    h.eq((r[1], r[2]), ("Market prices for 1 card could not be fetched. Press Update prices to try again.", False), "the failure is one plain line")
    st6 = base_state(h, [srow(1, "Fresh", cash=None, mk=None)], has_ck=False)
    r = node({"mode": "page", "state": st6, "steps": [
        {"op": "read", "id": "p2empty", "prop": "innerHTML"}, {"op": "read", "id": "p2body", "prop": "hidden"}]})["results"]
    h.ok("Press <b>Update prices</b> to see what Card Kingdom pays" in r[0], "with no CK prices the binder says to press Update prices to see what Card Kingdom pays")
    h.ok("CK is buying" not in r[0] and r[1] is True, "...and does not say 'CK is buying 0 of N'")
    r = node({"mode": "page", "state": base_state(h, waiting), "steps": [
        {"op": "read", "id": "showing", "prop": "textContent"}]})["results"]
    h.eq(r[0], "Showing 2 of 2 cards", "cards still unresolved stay visible under 'Only cards CK is buying'")
    # the back-on-the-buylist alert
    brows = [srow(1, "Old", cash=50), srow(2, "Returned", cash=60, back=True), srow(3, "Returned Too", cash=70, back=True)]
    r = node({"mode": "page", "state": base_state(h, brows), "steps": [
        {"op": "read", "id": "badge", "prop": "hidden"}, {"op": "read", "id": "badge", "prop": "textContent"},
        {"op": "read", "id": "alert", "prop": "hidden"}, {"op": "read", "id": "alertText", "prop": "innerHTML"},
        {"op": "read", "id": "chipBack", "prop": "hidden"}, {"op": "read", "id": "chipBack", "prop": "textContent"},
        {"op": "count", "id": "tbody", "pattern": 'tag back'},
        {"op": "docclick", "target": {"id": "alertShow", "closest": {}}},
        {"op": "count", "id": "tbody", "pattern": "<tr class="},
        {"op": "attr", "id": "chipBack", "name": "aria-pressed"}]})["results"]
    h.eq(r[:2], [False, "2 back on the buylist"], "the header badge shows the count")
    h.ok(r[2] is False and "2 of your cards are back on CK's buylist" in r[3], "a dismissable line at the top of Step 2")
    h.eq(r[4:6], [False, "Back on the buylist (2)"], "the Back filter chip")
    h.eq(r[6], 2, "a 'Back' tag on those cards")
    h.eq((r[7], r[8]), (2, "true"), "'show them' filters to the returned cards")
    r = node({"mode": "page", "state": base_state(h, [srow(1, "Plain", cash=50)]), "steps": [
        {"op": "read", "id": "badge", "prop": "hidden"}, {"op": "read", "id": "alert", "prop": "hidden"}, {"op": "read", "id": "chipBack", "prop": "hidden"}]})["results"]
    h.eq(r, [True, True, True], "with nothing back there is no badge, line or chip")
    # grey for not buying, theme default
    mixed = [srow(1, "Buy", cash=50), srow(2, "Nope", cash=None), srow(3, "NoMarket", cash=50, mk=None)]
    r = node({"mode": "page", "state": base_state(h, mixed), "steps": [
        {"op": "fire", "id": "onlyBuying", "event": "change", "ev": {"checked": False}},
        {"op": "read", "id": "tbody", "prop": "innerHTML"},
        {"op": "attr", "id": "html", "name": "data-theme"},
        {"op": "fire", "id": "theme", "event": "click"},
        {"op": "attr", "id": "html", "name": "data-theme"}]})["results"]
    h.ok("not buying" in r[0] and "no market price" in r[0], "'not buying' and 'no market price' are shown")
    h.eq((r[1], r[2]), ("dark", "light"), "dark is the default; the toggle switches to light")
    # adding a card by hand, and changing a printing, as the page drives them
    item = {"sid": "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", "finish": "foil", "name": "Sol Ring", "edition": "Commander Masters",
            "variation": "", "number": "410", "cash": 120, "wants": 5, "buying": True}
    own = srow(1, "Sol Ring", "Commander Masters", "cmm", "410", finish="foil")
    own["sid"] = item["sid"]
    state = base_state(h, [own])
    r = node({"mode": "page", "state": state, "responses": {"GET /api/search": {"ok": True, "items": [item]},
                                                           "POST /api/add": dict(state, label="Added 2 x Sol Ring.")}, "steps": [
        {"op": "fire", "id": "addBtn", "event": "click"},
        {"op": "read", "id": "addrow", "prop": "hidden"},
        {"op": "set", "id": "asearch", "props": {"value": "sol"}},
        {"op": "fire", "id": "asearch", "event": "input", "ev": {"value": "sol"}},
        {"op": "wait", "ms": 300},
        {"op": "read", "id": "alist", "prop": "innerHTML"},
        {"op": "fire", "id": "asearch", "event": "keydown", "evt": {"key": "ArrowDown"}},
        {"op": "fire", "id": "asearch", "event": "keydown", "evt": {"key": "Enter"}},
        {"op": "read", "id": "asearch", "prop": "value"},
        {"op": "set", "id": "aamt", "props": {"value": "2"}},
        {"op": "set", "id": "acond", "props": {"value": "EX"}},
        {"op": "clearcalls"},
        {"op": "fire", "id": "aadd", "event": "click"},
        {"op": "wait", "ms": 60},
        {"op": "calls"},
        {"op": "read", "id": "aconfirm", "prop": "textContent"},
        {"op": "docclick", "target": {"id": "", "closest": {".chg": {}}, "attrs": {"data-id": "1"}}},
        {"op": "wait", "ms": 60},
        {"op": "read", "id": "chhead", "prop": "textContent"},
        {"op": "read", "id": "chlist", "prop": "innerHTML"},
    ]})["results"]
    h.eq(r[0], False, "+ Add a card opens the add row above the binder")
    h.ok("Sol Ring" in r[1] and "CK pays $1.20" in r[1] and "Foil" in r[1] and "#410" in r[1], "the dropdown shows the card, set, number, foil tag and what CK pays")
    h.ok(r[2].startswith("Sol Ring (Commander Masters #410, foil)"), "Enter picks the printing")
    post = [c for c in r[3] if c["method"] == "POST"]
    h.eq(json.loads(post[0]["body"]), {"sid": item["sid"], "finish": "foil", "condition": "EX", "amount": 2}, "Add sends the printing, condition and amount")
    h.ok(r[4].startswith("Added 2 x Sol Ring"), "the row stays open with a confirmation")
    h.eq(r[5], "Change the printing of Sol Ring", "Change opens the printing list for that card")
    h.ok("current" in r[6], "the current printing is marked")
    # step 3 with no cards
    # picture address: only for a valid id
    pic = pure([], S, ["api.pictureUrl('0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d')", "api.pictureUrl('javascript:alert(1)')",
                       "api.pictureUrl('')", "api.pictureUrl('0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d/../x')", "api.pictureUrl(undefined)"])
    h.section("HAND")
    h.eq(pic[0], "https://api.scryfall.com/cards/0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d?format=image&version=large", "the picture address (large, from Scryfall)")
    h.eq(pic[1:], [None, None, None, None], "no picture address unless the id matches the UUID pattern")
    html = open(HTML, encoding="utf-8").read()
    h.ok('referrerpolicy="no-referrer"' in html, "pictures are loaded with referrerpolicy=no-referrer")
    h.ok("onerror" in html and "p.hidden=true" in html.replace(" ", ""), "a picture that fails shows nothing")
