"""CK, MARKET, BACK, PLAN, LINKS and CKFILE."""
import io
import json
import os
import urllib.request
from urllib.parse import parse_qsl, urlparse

import ck_prices
import collection
import fixtures as fx
import market
import netutil
import plan
from harness import call, loaded_app, make_app, rows_by_name


# ------------------------------------------------------------------ CK
def ck_tests(h):
    h.section("CK")
    net = fx.FakeNet()
    slim = ck_prices.download(net.ck_opener)
    rows = slim["rows"]
    h.eq(net.ck_calls, 1, "one request for the whole list")
    req = net.last_ck_request
    h.eq(req.full_url, "https://api.cardkingdom.com/api/v2/pricelist", "the pricelist address")
    h.eq(req.get_header("User-agent"), "ScammersKingdom/" + __import__("appinfo").APP_VERSION, "a plain User-Agent with the version")
    h.ok("gzip" in (req.get_header("Accept-encoding") or ""), "gzip is accepted")
    h.ok(all("|" in k and k.split("|")[1] in ("nonfoil", "foil", "etched") for k in rows), "keys are '<scryfall id>|<finish>'")
    h.ok(not any(e["name"] == "Token" for e in rows.values()), "rows without a Scryfall id are dropped")
    sol = rows[fx.printing_id("Sol Ring") + "|nonfoil"]
    h.eq((sol["cash"], sol["wants"], sol["name"], sol["edition"], sol["variation"]),
         (50, 10, "Sol Ring", "Commander Masters", ""), "slim row: cash in cents, wants, name, edition, variation")
    h.ok(set(sol) == {"sku", "cash", "wants", "url", "name", "edition", "variation"}, "slim row has exactly the kept fields")
    h.eq(rows[fx.printing_id("Sol Ring") + "|foil"]["cash"], 120, "foil is a separate entry under the same id")
    h.eq(rows[fx.printing_id("Brainstorm", "Etched Foil") + "|etched"]["cash"], 300, "the word 'Etched' in the variation makes it etched")
    h.ok(fx.printing_id("Sketch Card") + "|foil" in rows and fx.printing_id("Sketch Card") + "|etched" not in rows,
         "a 'Sketched' edition is foil, not etched")
    h.eq(ck_prices.finish_of({"edition": "Foo ETCHED", "is_foil": "false"}), "etched", "etched is matched ignoring case, in the edition too")
    h.eq(ck_prices.finish_of({"variation": "", "edition": "", "is_foil": "true"}), "foil", "is_foil 'true' is foil")
    h.eq(ck_prices.finish_of({"variation": "", "edition": "", "is_foil": "false"}), "nonfoil", "otherwise non-foil")
    # buying rule
    h.ok(ck_prices.is_buying({"cash": 1, "wants": 1}), "buying when cash > 0 and wants > 0")
    h.ok(not ck_prices.is_buying({"cash": 0, "wants": 5}) and not ck_prices.is_buying({"cash": 5, "wants": 0}),
         "not buying when cash is 0 or wants is 0")
    h.ok(not ck_prices.is_buying(rows[fx.printing_id("Mystic Remora") + "|nonfoil"]), "a listed card with no price is not buying")
    h.ok(not ck_prices.is_buying(rows[fx.printing_id("Giant Growth") + "|nonfoil"]), "a price with nothing wanted is not buying")
    # parsing details
    h.eq([ck_prices.to_cents(x) for x in ("0.01", "12.34", "1,200.50", "bad", "-1", "0.005", None)],
         [1, 1234, 120050, 0, 0, 1, 0], "prices are parsed to whole cents")
    # streaming reader
    data = fx.pricelist_bytes()
    full = list(ck_prices.iter_rows(io.BytesIO(data)))
    tiny = list(ck_prices.iter_rows(io.BytesIO(data), chunk=7))
    h.ok(full == tiny and len(full) == len(fx.ck_rows()), "reading in tiny chunks gives the same rows")
    flat = list(ck_prices.iter_rows(io.BytesIO(fx.pricelist_bytes(wrap=False)), chunk=5))
    h.eq(len(flat), len(full), "a plain list works as well as an object with a data list")
    s_gz = ck_prices.download(lambda req, timeout=None: fx.FakeResponse(fx.pricelist_bytes(gz=True), gz=True))
    h.eq(len(s_gz["rows"]), len(rows), "a gzip answer is unzipped")
    try:
        ck_prices.download(lambda req, timeout=None: fx.FakeResponse(b"<html>nope</html>"))
        h.ok(False, "an answer that is not a price list is refused")
    except ck_prices.PricelistError:
        h.ok(True, "an answer that is not a price list is refused")
    try:
        ck_prices.download(lambda req, timeout=None: fx.FakeResponse(json.dumps({"data": []}).encode()))
        h.ok(False, "an empty list is refused")
    except ck_prices.PricelistError:
        h.ok(True, "an empty list is refused, so the old prices are never wiped")
    try:
        ck_prices.download(lambda req, timeout=None: (_ for _ in ()).throw(OSError("down")))
        h.ok(False, "a failed request raises")
    except netutil.NetError as e:
        h.ok(len(str(e)) > 5, "a failed request becomes a plain-words error")

    # keeping the files: the old list becomes ck_previous.json
    d = h.tmp()
    ck_prices.save_latest(d, {"as_of": "one", "rows": {"a|nonfoil": {"cash": 1}}})
    h.ok(not os.path.exists(os.path.join(d, "ck_previous.json")), "the first save has nothing to move")
    ck_prices.save_latest(d, {"as_of": "two", "rows": {"b|nonfoil": {"cash": 2}}})
    h.eq(ck_prices.load(d)["as_of"], "two", "ck_latest.json is the new list")
    h.eq(ck_prices.load(d, "ck_previous.json")["as_of"], "one", "ck_previous.json is the old list")
    ck_prices.save_latest(d, {"as_of": "three", "rows": {"c|nonfoil": {"cash": 3}}})
    h.eq(ck_prices.load(d, "ck_previous.json")["as_of"], "two", "each update moves the one before it")

    # credit
    S = dict(plan.DEFAULT_SETTINGS)
    row = {"id": 1, "ck": {"cash": 1000}, "cond": "NM"}
    h.eq(plan.credit_each(plan.cash_each(row, S), S), 1300, "credit = cash x 1.30 with the default 30% bonus")
    S2 = dict(S, credit_bonus=10)
    h.eq(plan.credit_each(1000, S2), 1100, "the bonus is a setting")
    h.eq(plan.credit_each(1, S), 1, "credit is at least a cent")
    # an update end to end, as the page sees it
    app, net = loaded_app(h)
    st, s = call(app, "GET", "/api/state")
    h.ok(os.path.exists(os.path.join(app.prices_dir, "ck_latest.json")), "Update prices writes prices/ck_latest.json")
    h.eq(len(net.scry_calls) >= 1 and net.ck_calls, 1, "an update asks CK once")


# ------------------------------------------------------------------ MARKET
def market_tests(h):
    h.section("MARKET")
    rows = [{"set_code": "tst", "number": str(i), "sid": "", "finish": "nonfoil"} for i in range(200)]
    idents = market.identifiers(rows + rows[:5])
    h.eq(len(idents), 200, "each printing is asked for once")
    calls, sleeps = [], []

    def post(url, payload):
        calls.append((url, len(payload["identifiers"])))
        return {"object": "list", "not_found": payload["identifiers"], "data": []}
    progress = []
    res = market.fetch(idents, post, sleeps.append, lambda a, b: progress.append((a, b)))
    h.eq([c[1] for c in calls], [75, 75, 50], "200 printings go in requests of at most 75")
    h.eq(calls[0][0], "https://api.scryfall.com/cards/collection", "the collection endpoint")
    h.eq(len(sleeps), 2, "a pause between requests, not before the first")
    h.ok(all(s >= 0.1 for s in sleeps), "each pause is at least 100 ms (at most 10 requests a second)")
    h.eq(progress, [(1, 3), (2, 3), (3, 3)], "progress is reported as 'n of total'")
    h.eq(len(res["missing"]), 200, "not_found printings are remembered as missing")

    # prices by finish
    app, net = loaded_app(h)
    st, s = call(app, "GET", "/api/state")
    sol_n = rows_by_name(s, "Sol Ring", "nonfoil")[0]
    sol_f = rows_by_name(s, "Sol Ring", "foil")[0]
    h.eq((sol_n["mk"], sol_f["mk"]), (100, 300), "usd for non-foil, usd_foil for foil (cents)")
    h.eq(sol_n["mk_src"], "scryfall", "the price comes from Scryfall")
    e = app.mk.entries["sn:cmm|410"]
    h.eq((e["id"], e["name"], e["set_name"]), (fx.printing_id("Sol Ring"), "Sol Ring", "Commander Masters"), "id, name and set name are cached")
    h.eq(market.price_for({"usd": 5, "usd_foil": 6, "usd_etched": 7}, "etched"), 7, "usd_etched for etched")
    h.eq(market.price_for({"usd": 5, "usd_foil": None, "usd_etched": None}, "foil"), None, "a missing finish price is no price")
    # the headers go out as the brief says
    seen = {}

    class R:
        headers = {}

        def read(self, n=-1):
            return b'{"data":[],"not_found":[]}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=None):
        seen["req"], seen["timeout"] = req, timeout
        return R()
    netutil.post_json("https://api.scryfall.com/cards/collection", {"identifiers": []}, opener=opener)
    h.eq(seen["req"].get_header("User-agent"), "ScammersKingdom/" + __import__("appinfo").APP_VERSION, "User-Agent: ScammersKingdom/<version>")
    h.eq(seen["req"].get_header("Accept"), "application/json", "Accept: application/json")
    h.eq(seen["req"].get_method(), "POST", "a POST")
    h.ok(seen["timeout"], "with a timeout")
    # the order of cards in the answer does not matter; a missing set/number falls back to order
    ids = [{"set": "cmm", "collector_number": "410"}, {"set": "m11", "collector_number": "149"}]
    res = market.fetch(market.identifiers([{"set_code": "cmm", "number": "410", "sid": ""}, {"set_code": "m11", "number": "149", "sid": ""}]),
                       lambda u, p: fx.scry_answer(p["identifiers"], shuffle=True), lambda s: None)
    h.eq(sorted(res["entries"]), ["sn:cmm|410", "sn:m11|149"], "cards are matched to what was asked by set + number, in any order")
    res = market.fetch(market.identifiers([{"set_code": "cmm", "number": "410", "sid": ""}, {"set_code": "m11", "number": "149", "sid": ""}]),
                       lambda u, p: fx.scry_answer(p["identifiers"], strip_set=True), lambda s: None)
    h.eq(sorted(res["entries"]), ["sn:cmm|410", "sn:m11|149"], "if the answer lacks set and number, request order is used")
    ids = market.identifiers([{"set_code": "", "number": "", "sid": fx.printing_id("Sol Ring")}])
    h.eq(list(ids.values()), [{"id": fx.printing_id("Sol Ring")}], "a card added by hand is asked for by its Scryfall id")

    # a failing request keeps what was fetched and what was cached
    cache = market.Cache({"as_of": "2000-01-01T00:00:00+00:00", "entries": {"sn:tst|0": {"id": "x", "usd": 5, "usd_foil": None, "usd_etched": None, "set": "tst", "number": "0"}}})
    n = {"i": 0}

    def flaky(url, payload):
        n["i"] += 1
        if n["i"] == 2:
            raise netutil.NetError("no internet connection, or the site could not be reached")
        return fx.scry_answer([])
    res = market.fetch(idents, flaky, lambda s: None)
    h.ok(res["error"] and res["batches"] == 1, "a failed request stops the run and says why")
    cache.merge(res, set(idents) | {"sn:tst|0"})
    h.ok("sn:tst|0" in cache.entries and cache.as_of == "2000-01-01T00:00:00+00:00", "earlier prices and their date stay")

    # fallback to the scan's own price until the first lookup
    app, net = make_app(h)
    call(app, "POST", "/api/import/shoptcg?mode=replace", raw=fx.sample_shop())
    st, s = call(app, "GET", "/api/state")
    sol = rows_by_name(s, "Sol Ring", "nonfoil")[0]
    h.eq((sol["mk"], sol["mk_src"]), (110, "scan"), "before the first lookup the scan's USD price is shown")
    app.run_update()
    st, s = call(app, "GET", "/api/state")
    h.eq(rows_by_name(s, "Sol Ring", "nonfoil")[0]["mk_src"], "scryfall", "after the lookup Scryfall's price replaces it")
    # a failing lookup keeps earlier market prices
    net.scry_error_at = 0
    app.run_update()
    st, s = call(app, "GET", "/api/state")
    h.eq(rows_by_name(s, "Sol Ring", "nonfoil")[0]["mk"], 100, "if Scryfall fails the earlier market price stays")
    h.ok(any("market" in l.lower() for l in app.update["result"]["lines"]) and not app.update["result"]["ok"],
         "and the update says plainly what failed")
    h.ok(not any("Card Kingdom" in l and "Could not" in l for l in app.update["result"]["lines"]), "the CK side is not blamed when only Scryfall failed")


# ------------------------------------------------------------------ BACK
def back_tests(h):
    h.section("BACK")
    net = fx.FakeNet()
    base = [r for r in fx.ck_rows()]

    def with_counter(wants):
        out = []
        for r in base:
            r = dict(r)
            if r["name"] == "Counterspell":
                r["qty_buying"] = wants
            out.append(r)
        return out
    net.ck_rows = with_counter(0)
    app, _ = loaded_app(h, net=net)
    st, s = call(app, "GET", "/api/state")
    h.eq(s["back"]["count"], 0, "the first update ever says nothing")
    h.ok(not os.path.exists(os.path.join(app.prices_dir, "ck_previous.json")), "the first update has no previous list")
    h.eq(app.state["back_keys"], [], "nothing is stored for the first update")
    net.ck_rows = with_counter(3)
    app.run_update()
    st, s = call(app, "GET", "/api/state")
    back = [r["name"] for r in s["rows"] if r["back"]]
    h.eq(back, ["Counterspell"], "a card not bought last time and bought now is back on the buylist")
    h.eq(s["back"]["count"], 1, "the badge count")
    h.ok(os.path.exists(os.path.join(app.prices_dir, "ck_previous.json")), "the old list was kept as ck_previous.json")
    h.ok(not rows_by_name(s, "Sol Ring")[0]["back"], "a card bought both times is not 'back'")
    h.ok(not rows_by_name(s, "Mystic Remora")[0]["back"], "a card never bought is not 'back'")
    # the list stays until the next update or until dismissed (state.json)
    st, s = call(app, "GET", "/api/state")
    h.eq(s["back"]["count"], 1, "the back list stays between page loads")
    call(app, "POST", "/api/ui", {"action": "dismiss_back_line"})
    st, s = call(app, "GET", "/api/state")
    h.ok(s["back"]["line_dismissed"] and s["back"]["count"] == 1, "dismissing hides the line (kept in state.json); the Back tags stay")
    import server
    again = server.App(app.data_folder, check_updates=False)
    h.ok(call(again, "GET", "/api/state")[1]["back"]["line_dismissed"], "the dismissal survives a restart")
    app.run_update()
    st, s = call(app, "GET", "/api/state")
    h.eq((s["back"]["count"], s["back"]["line_dismissed"]), (0, False), "the next update replaces the list (nothing new this time)")
    # a printing missing from the previous list altogether counts as not bought
    net.ck_rows = [r for r in with_counter(3) if r["name"] != "Lightning Bolt"]
    app.run_update()
    net.ck_rows = with_counter(3)
    app.run_update()
    st, s = call(app, "GET", "/api/state")
    h.eq([r["name"] for r in s["rows"] if r["back"]], ["Lightning Bolt"], "a printing absent last time and present now is back")
    # a failed CK download changes nothing
    net.ck_error = OSError("down")
    app.run_update()
    st, s = call(app, "GET", "/api/state")
    h.eq([r["name"] for r in s["rows"] if r["back"]], ["Lightning Bolt"], "a failed CK download keeps the list and the prices")
    h.ok("Card Kingdom" in app.update["result"]["lines"][0] and "stay" in app.update["result"]["lines"][0], "...and says plainly what failed")


# ------------------------------------------------------------------ PLAN
def prow(i, name, cash, mk, qty=1, wants=None, cond="NM", finish="nonfoil", sid=None, edition="Ed"):
    return {"id": i, "name": name, "set_name": edition, "set_code": "", "number": "", "finish": finish, "cond": cond,
            "qty": qty, "sid": sid or "sid-%s" % name, "src": "import",
            "ck": None if cash is None else {"cash": cash, "wants": wants if wants is not None else 999, "name": name,
                                              "edition": edition, "variation": "", "url": ""},
            "mk": mk, "link": None}


def plan_tests(h):
    h.section("PLAN")
    S = dict(plan.DEFAULT_SETTINGS, bag_size=3)
    rows = [prow(1, "Low", 30, 100), prow(2, "High", 90, 100), prow(3, "Mid", 50, 100),
            prow(4, "Never", None, 100), prow(5, "NoPrice", 40, None)]
    r = plan.ranked(rows, S)
    h.eq([e["row"]["name"] for e in r], ["High", "Mid", "Low", "NoPrice"], "ranked by CK % of market; no market price last; not buying left out")
    # ties: the higher CK price first, then name
    rows = [prow(1, "B", 50, 100), prow(2, "A", 50, 100), prow(3, "C", 100, 200), prow(4, "D", 25, 50)]
    h.eq([e["row"]["name"] for e in plan.ranked(rows, S)], ["C", "A", "B", "D"],
         "equal percent: higher CK price first, then name (A before B)")
    # wants limit
    rows = [prow(1, "Capped", 90, 100, qty=10, wants=2), prow(2, "Free", 50, 100, qty=2)]
    p = plan.build_plan(rows, dict(S, bag_size=50), 1)
    h.eq(p["taken"], 4, "never more copies of a printing than CK wants (2 of 10) plus 2 free")
    h.eq(p["bags"][0]["lines"][0]["qty"], 2, "the capped printing contributes only what CK wants")
    # the limit is per printing, across conditions; the better condition uses it first
    rows = [prow(1, "Same", 100, 200, qty=2, wants=3, cond="EX"), prow(2, "Same", 100, 200, qty=2, wants=3, cond="NM")]
    al = plan.allocate(rows)
    h.eq((al[2], al[1]), (2, 1), "one printing in two conditions shares CK's limit, NM first")
    # bag cutting
    rows = [prow(i, "Card%d" % i, 100 - i, 100, qty=1) for i in range(1, 8)]
    p = plan.build_plan(rows, S, 2)
    h.eq([b["cards"] for b in p["bags"]], [3, 3], "cut into bags of the bag size, in rank order")
    h.eq((p["taken"], p["left"], p["available"]), (6, 1, 7), "only bag size x n copies are taken; one is left over")
    h.eq([l["row"]["name"] for l in p["bags"][0]["lines"]], ["Card1", "Card2", "Card3"], "best cards fill the first bag")
    # a copy count spanning two bags
    rows = [prow(1, "Many", 90, 100, qty=5)]
    p = plan.build_plan(rows, S, 2)
    h.eq([(b["cards"], b["lines"][0]["qty"]) for b in p["bags"]], [(3, 3), (2, 2)], "one printing can span two bags")
    # partial bag still pays the fee
    h.eq((p["bags"][1]["missing"], p["bags"][1]["fee"]), (1, 80), "a partly filled last bag says how many would fill it and still pays the fee")
    h.eq(p["bags"][1]["get"], max(0, 2 * 90 - 80), "what you get = total minus the fee")
    h.eq(p["bags"][0]["get"], 3 * 90 - 80, "a full bag: total minus the fee")
    # fewer copies than the bags hold
    p = plan.build_plan(rows, S, 4)
    h.ok(p["short"] and p["available"] == 5 and len(p["bags"]) == 2, "fewer copies than the bags hold is flagged; only needed bags are returned")
    h.ok(plan.build_plan(rows, dict(S, bag_size=5), 1)["short"] is False, "exactly full is not short")
    # cash versus credit
    rows = [prow(1, "Money", 1000, 2000, qty=1)]
    pc = plan.build_plan(rows, dict(S, bag_size=50), 1, "cash")
    pr = plan.build_plan(rows, dict(S, bag_size=50), 1, "credit")
    h.eq((pc["bags"][0]["total"], pr["bags"][0]["total"]), (1000, 1300), "credit pays cash x 1.30")
    h.eq((pc["get"], pr["get"]), (920, 1220), "the fee comes off either way")
    h.eq([e["cash"] for e in plan.ranked(rows, S)], [1000], "ranking uses cash")
    # EX / VG shares
    rows = [prow(1, "Cond", 1000, 2000, cond="EX"), prow(2, "Cond2", 1000, 2000, cond="VG")]
    h.eq((plan.cash_each(rows[0], plan.DEFAULT_SETTINGS), plan.cash_each(rows[1], plan.DEFAULT_SETTINGS)), (800, 600), "EX pays 80% and VG 60% of NM to start")
    h.eq(plan.cash_each(rows[0], dict(plan.DEFAULT_SETTINGS, ex_share=90)), 900, "the shares are settings")
    # no minimum price
    rows = [prow(1, "Penny", 1, 3, qty=2), prow(2, "Big", 500, 600, qty=1)]
    p = plan.build_plan(rows, dict(S, bag_size=50), 1)
    h.eq(p["taken"], 3, "$0.01 cards are included alongside valuable ones")
    h.eq(plan.cash_each(prow(1, "Penny", 1, 3, cond="VG"), dict(plan.DEFAULT_SETTINGS, vg_share=40)), 1, "a copy never pays less than a cent")
    # bands
    h.eq([plan.band(x) for x in (70, 69.9, 40, 39.9, None)], ["g", "a", "a", "n", "n"], "green 70+, amber 40-69, grey below")
    # CSV spreadsheet
    p = plan.build_plan([prow(1, "Sheoldred, the Apocalypse", 1700, 2800, qty=1)], dict(S, bag_size=50), 1)
    text = plan.plan_csv(p)
    h.ok(text.startswith("﻿") and "\r\n" in text and '"Sheoldred, the Apocalypse"' in text and "61%" in text,
         "Download CSV: spreadsheet with a byte order mark, quoting and the percent")
    # in the program
    app, net = loaded_app(h)
    st, s = call(app, "GET", "/download/plan.csv?bags=1&pay=credit")
    h.eq(st, 200, "the plan CSV route works")
    h.ok(s.startswith("﻿Bag,Qty,Card"), "...and starts with its header")
    h.eq(call(app, "GET", "/download/plan.csv?bags=99")[0], 400, "too many bags is refused")
    h.eq(call(app, "GET", "/download/plan.csv?bags=x")[0], 400, "a bad bag count is refused")


# ------------------------------------------------------------------ LINKS
def links_tests(h):
    h.section("LINKS")
    base = "https://www.cardkingdom.com/purchasing/mtg_singles?"
    u = ck_prices.buylist_url("Forest", "foil", "mtg/final-fantasy/forest-0576-surge-foil-foil")
    h.eq(u, base + "filter%5Bsort%5D=price_desc&filter%5Bsearch%5D=mtg_advanced&filter%5Bname%5D=Forest"
         "&filter%5Bedition%5D=final-fantasy&filter%5Bfoils%5D=1", "the whole address, parameters in order")
    keys = [k for k, _ in parse_qsl(urlparse(u).query)]
    h.eq(keys, ["filter[sort]", "filter[search]", "filter[name]", "filter[edition]", "filter[foils]"], "order: sort, search, name, edition, foils")
    u = ck_prices.buylist_url("Ertai, Wizard Adept", "nonfoil", "mtg/urzas-legacy/ertai-wizard-adept")
    h.ok("filter%5Bname%5D=Ertai%2C%20Wizard%20Adept" in u, "each value is URL-encoded (comma and spaces)")
    u = ck_prices.buylist_url("Assault // Battery", "nonfoil", "mtg/guilds-of-ravnica/assault-battery")
    h.ok("filter%5Bname%5D=Assault&" in u, "a double-faced or split card uses the part before ' // '")
    h.eq(dict(parse_qsl(urlparse(u).query))["filter[name]"], "Assault", "...decoded: just 'Assault'")
    h.ok(u.endswith("filter%5Bsingles%5D=1") and "filter%5Bfoils%5D" not in u, "non-foil: singles=1 and no foils")
    u = ck_prices.buylist_url("Brainstorm", "etched", "mtg/mystical-archive/brainstorm-etched-foil")
    h.ok(u.endswith("filter%5Bfoils%5D=1") and "filter%5Bsingles%5D" not in u, "etched counts as foil: foils=1")
    h.eq(ck_prices.edition_slug("mtg/final-fantasy/forest-0576-surge-foil-foil"), "final-fantasy", "edition slug from the CK url")
    h.eq(ck_prices.edition_slug("/mtg/dominaria-united/x"), "dominaria-united", "a leading slash is fine")
    h.eq(ck_prices.edition_slug("https://www.cardkingdom.com/mtg/dominaria-united/x?a=1"), "dominaria-united", "a full address is fine")
    h.eq(ck_prices.edition_slug(""), "", "no url: no slug")
    u = ck_prices.buylist_url("Sol Ring", "nonfoil", "")
    h.ok("edition" not in u, "no edition parameter when the slug is unknown")
    # in the binder rows: none when CK is not buying
    app, net = loaded_app(h)
    st, s = call(app, "GET", "/api/state")
    h.ok(rows_by_name(s, "Mystic Remora")[0]["link"] is None, "no link when CK is not buying the card")
    h.ok(all(r["link"] for r in s["rows"] if r["ck"]), "a link for every card CK is buying")
    h.ok(all(r["link"] is None for r in s["rows"] if not r["ck"]), "and only for those")
    h.ok(rows_by_name(s, "Sol Ring", "foil")[0]["link"].endswith("filter%5Bfoils%5D=1"), "a foil row links to the foil list")
    h.ok(rows_by_name(s, "Sheoldred, the Apocalypse")[0]["link"].startswith(base), "links point at CK's buylist search")
    # the page opens links in a new tab and the program never requests them
    page = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "index.html"), encoding="utf-8").read()
    h.ok('target="_blank" rel="noopener noreferrer">Sell on CK' in page, "'Sell on CK' opens a new tab with rel=noopener noreferrer")
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.py"), encoding="utf-8").read()
    h.ok("buylist_url" not in src, "the server never builds a request for the link (it is only handed to the page)")


# ------------------------------------------------------------------ CKFILE
def vrow(i, name, edition, variation="", finish="nonfoil", cond="NM", cash=100):
    r = prow(i, name, cash, 200, edition=edition, cond=cond, finish=finish, sid="sid-%s-%s" % (name, variation))
    r["ck"]["variation"] = variation
    return r


def ckfile_tests(h):
    h.section("CKFILE")
    h.eq(plan.CK_IMPORT_VARIANT_STYLE, "paren", "the default variant style is 'paren'")
    S = dict(plan.DEFAULT_SETTINGS)
    rows = [vrow(1, "Forest", "Final Fantasy", "0576 - Surge Foil", "foil", cash=900),
            vrow(2, "Sol Ring", "Commander Masters", cash=800),
            vrow(3, "Assault // Battery", "Guilds of Ravnica", cash=700),
            vrow(4, "Ertai, Wizard Adept", "Urza's Legacy", cash=600)]
    rows[1]["qty"] = 2
    p = plan.build_plan(rows, S, 1)
    want = ("Forest (0576 - Surge Foil),Final Fantasy,TRUE,1\r\n"
            "Sol Ring,Commander Masters,FALSE,2\r\n"
            "Assault // Battery,Guilds of Ravnica,FALSE,1\r\n"
            "\"Ertai, Wizard Adept\",Urza's Legacy,FALSE,1\r\n")
    h.eq(plan.ck_import_csv(p), want, "the four cases: variant, no variant, split card, name with a comma (quoted)")
    h.eq(plan.ck_import_name({"name": "Forest", "variation": "0306"}), "Forest (0306)", "a variant is written Name (variation)")
    h.eq(plan.ck_import_name({"name": "Forest", "variation": ""}), "Forest", "no variation: the plain name")
    h.eq(plan.ck_import_name({"name": "Assault // Battery", "variation": ""}), "Assault // Battery", "a split card keeps its ' // '")
    saved = plan.CK_IMPORT_VARIANT_STYLE
    try:
        plan.CK_IMPORT_VARIANT_STYLE = "plain"
        h.eq(plan.ck_import_name({"name": "Forest", "variation": "0306"}), "Forest", "style 'plain' writes the plain name")
        h.ok(plan.ck_import_csv(p).startswith("Forest,Final Fantasy,TRUE,1\r\n"), "...and the file follows it")
        plan.CK_IMPORT_VARIANT_STYLE = "paren"
        h.eq(plan.ck_import_name({"name": "Forest", "variation": "0307"}), "Forest (0307)", "style 'paren' writes Name (variation)")
    finally:
        plan.CK_IMPORT_VARIANT_STYLE = saved
    text = plan.ck_import_csv(p)
    lines = text.split("\r\n")
    h.ok(text.endswith("\r\n") and "\n" not in text.replace("\r\n", ""), "CRLF line ends")
    h.ok(not text.startswith("Card") and all(len(next(__import__("csv").reader([l]))) == 4 for l in lines if l),
         "no header row; four columns per line")
    h.ok(not text.startswith("﻿"), "no byte order mark")
    # foil / etched / quantity sums, conditions do not matter
    rows = [vrow(1, "Sol Ring", "Commander Masters", cond="NM"), vrow(2, "Sol Ring", "Commander Masters", cond="EX"),
            vrow(3, "Brainstorm", "Mystical Archive", "Etched Foil", "etched")]
    rows[0]["sid"] = rows[1]["sid"] = "same"
    rows[0]["qty"], rows[1]["qty"] = 2, 3
    for r in rows[:2]:
        r["ck"]["wants"] = 99
    p = plan.build_plan(rows, S, 1)
    lines = plan.ck_import_lines(p)
    h.eq(lines, [("Brainstorm (Etched Foil)", "Mystical Archive", "TRUE", 1), ("Sol Ring", "Commander Masters", "FALSE", 5)],
         "one line per printing: NM and EX copies are added together; etched is TRUE")
    rows_nm = [dict(r, cond="NM") for r in rows]
    rows_vg = [dict(r, cond="VG") for r in rows]
    h.eq(plan.ck_import_csv(plan.build_plan(rows_nm, S, 1)), plan.ck_import_csv(plan.build_plan(rows_vg, S, 1)),
         "the file is the same whatever conditions were chosen (no condition column)")
    # a whole shipment in one file, across bags
    S3 = dict(S, bag_size=2)
    rows = [vrow(i, "Card%d" % i, "Ed", cash=1000 - i) for i in range(1, 6)]
    p = plan.build_plan(rows, S3, 3)
    h.eq(len(p["bags"]), 3, "three bags")
    h.eq(len(plan.ck_import_csv(p).split("\r\n")) - 1, 5, "one file holds all the bags' cards")
    # through the program
    app, net = loaded_app(h)
    st, text = call(app, "GET", "/download/ck-import.csv?bags=1")
    disp = call.last.headers["Content-Disposition"]
    h.ok(disp.startswith('attachment; filename="ck-buylist-') and disp.endswith('.csv"'), "file name ck-buylist-YYYY-MM-DD.csv")
    first = text.split("\r\n")[0]
    h.eq(first, "Counterspell,Modern Horizons 2,FALSE,2", "best CK % of market first (Counterspell 78%), with CK's own name and edition")
    h.ok('"Sheoldred, the Apocalypse",Dominaria United,FALSE,1' in text.split("\r\n"), "a name with a comma is quoted in the real file")
    h.ok("Mystic Remora" not in text and "Giant Growth" not in text, "cards CK is not buying are not in the file")
    st, text2 = call(app, "GET", "/download/ck-import.csv?bags=1")
    h.eq(text, text2, "the same shipment gives the same file")
    g = [l for l in text.split("\r\n") if l.startswith("Sol Ring")]
    h.ok(any(",TRUE," in l for l in g) and any(",FALSE," in l for l in g), "foil and non-foil Sol Ring are separate lines")
