"""LOOKUP: new cards are looked up on Scryfall right after an import or an add, without another Update prices."""
import csv
import io
import threading

import collection
import fixtures as fx
from harness import call, make_app, rows_by_name

WANT_FAIL = "Market prices for %d cards could not be fetched. Press Update prices to try again."


def prices_first(h, net=None):
    """The natural first-time order: press Update prices before any cards are in."""
    net = net or fx.FakeNet()
    app, net = make_app(h, net=net, auto_lookup=True)
    app.run_update()
    return app, net


def lookup_tests(h):
    h.section("LOOKUP")
    # update, then import: joined with no second update
    app, net = prices_first(h)
    h.eq((net.ck_calls, len(net.scry_calls)), (1, 0), "an update with an empty binder asks CK once and Scryfall for nothing")
    gate, release = threading.Event(), threading.Event()
    net.scry_gate = lambda: (gate.set(), release.wait(5))
    st, j = call(app, "POST", "/api/import/shoptcg?mode=replace", raw=fx.sample_shop())
    gate.wait(5)
    st, s = call(app, "GET", "/api/state")
    sol = rows_by_name(s, "Sol Ring", "nonfoil")[0]
    h.ok(s["lookup"]["running"], "right after the import a lookup is running")
    h.eq(s["lookup"]["message"], "Getting market prices for 10 new cards...", "the page can say 'Getting market prices for 10 new cards...'")
    h.ok(sol["unresolved"] and sol["ck"] is None, "until the lookup finishes the card is marked unresolved (the page shows 'looking up')")
    release.set()
    app.wait_lookup()
    net.scry_gate = None
    st, s = call(app, "GET", "/api/state")
    sol = rows_by_name(s, "Sol Ring", "nonfoil")[0]
    h.ok(not s["lookup"]["running"] and s["lookup"]["error"] is None, "the lookup finishes without an error")
    h.eq((sol["ck"]["cash"], sol["mk"], sol["unresolved"]), (50, 100, False), "the scanned card is joined to CK's list and has its market price")
    h.eq(rows_by_name(s, "Counterspell")[0]["ck"]["cash"], 155, "every matching card is joined")
    h.eq(net.ck_calls, 1, "CK's list was not downloaded again")
    h.ok(rows_by_name(s, "Promo Thing")[0]["unknown"] and not rows_by_name(s, "Promo Thing")[0]["unresolved"],
         "a card Scryfall cannot find is 'not found', no longer 'looking up'")
    h.ok(s["prices"]["has_ck"], "prices from the update are untouched")
    first_calls = len(net.scry_calls)

    # only printings with no market entry are asked
    csv_text = fx.shop_csv([fx.shop_line("Sol Ring", "Commander Masters", "CMM", "410", qty=1),
                            fx.shop_line("Promo Thing", "Promos", "PF18", "2018f", finish="Foil", qty=1),
                            fx.shop_line("Cultivate", "Commander 2021", "C21", "246", qty=2)])
    call(app, "POST", "/api/import/shoptcg?mode=add", raw=csv_text)
    app.wait_lookup()
    asked = [i for c in net.scry_calls[first_calls:] for i in c[1]["identifiers"]]
    h.eq(asked, [{"set": "c21", "collector_number": "246"}], "only the new printing is asked for (not known ones, not ones Scryfall could not find)")
    h.eq(rows_by_name(call(app, "GET", "/api/state")[1], "Cultivate")[0]["mk"], 35, "the new card has its price")
    n = len(net.scry_calls)
    call(app, "POST", "/api/import/shoptcg?mode=add", raw=csv_text)
    app.wait_lookup()
    h.eq(len(net.scry_calls), n, "nothing new to look up means no request at all")

    # Import My Collection: rows with no Scryfall id are joined after the lookup
    head = ",".join(collection.EXPORT_HEADER)
    text = "\r\n".join([head, "Sol Ring,Commander Masters,cmm,410,Non-Foil,NM,2,No,",
                        "Lightning Bolt,Magic 2011,m11,149,Foil,EX,1,No,"]) + "\r\n"
    app2, net2 = prices_first(h)
    st, j = call(app2, "POST", "/api/import/collection?mode=replace", raw=text)
    app2.wait_lookup()
    st, s = call(app2, "GET", "/api/state")
    sol = rows_by_name(s, "Sol Ring")[0]
    h.eq((sol["ck"]["cash"], sol["mk"]), (50, 100), "Import My Collection ends joined too, with no second update")
    h.eq(net2.ck_calls, 1, "...without downloading CK's list again")
    # a hand add only needs the market price
    app3, net3 = prices_first(h)
    sid = fx.printing_id("Counterspell")
    st, j = call(app3, "POST", "/api/add", {"sid": sid, "finish": "nonfoil", "condition": "NM", "amount": 2})
    row = j["rows"][0]
    h.ok(row["ck"]["cash"] == 155 and row["mk"] is None, "a card added by hand is joined to CK at once; only its market price is missing")
    app3.wait_lookup()
    row = call(app3, "GET", "/api/state")[1]["rows"][0]
    h.eq(row["mk"], 200, "its market price arrives without pressing Update prices")
    h.eq(net3.scry_calls[-1][1]["identifiers"], [{"id": sid}], "...asked for by its Scryfall id")

    # failure
    net4 = fx.FakeNet()
    net4.scry_error_at = 0
    app4, _ = prices_first(h, net4)
    call(app4, "POST", "/api/import/shoptcg?mode=replace", raw=fx.sample_shop())
    app4.wait_lookup()
    st, s = call(app4, "GET", "/api/state")
    h.eq(s["lookup"]["error"], WANT_FAIL % 10, "if Scryfall cannot be reached it says so in one line")
    h.ok(not s["lookup"]["running"] and rows_by_name(s, "Sol Ring", "nonfoil")[0]["unresolved"], "the cards stay unresolved ('not looked up'), nothing is lost")
    h.eq(len(s["rows"]), 11, "the binder itself is intact")
    net4.scry_error_at = None
    app4.run_update()
    st, s = call(app4, "GET", "/api/state")
    h.ok(s["lookup"]["error"] is None and rows_by_name(s, "Sol Ring", "nonfoil")[0]["ck"] is not None, "Update prices still fixes it, and clears the message")

    # no CK prices at all: the market lookup still runs, the page says to press Update prices
    app5, net5 = make_app(h, auto_lookup=True)
    call(app5, "POST", "/api/import/shoptcg?mode=replace", raw=fx.sample_shop())
    app5.wait_lookup()
    st, s = call(app5, "GET", "/api/state")
    h.ok(not s["prices"]["has_ck"] and rows_by_name(s, "Sol Ring", "nonfoil")[0]["mk"] == 100, "without CK prices the market prices are still looked up")
    h.eq(net5.ck_calls, 0, "a lookup never downloads CK's list")
    # no lookup by itself when switched off, and none while an update runs
    app6, net6 = make_app(h, auto_lookup=False)
    call(app6, "POST", "/api/import/shoptcg?mode=replace", raw=fx.sample_shop())
    h.eq(len(net6.scry_calls), 0, "(with the switch off nothing is asked)")
    app7, net7 = make_app(h, auto_lookup=True)
    app7.update["running"] = True
    call(app7, "POST", "/api/import/shoptcg?mode=replace", raw=fx.sample_shop())
    h.eq(len(net7.scry_calls), 0, "no lookup runs alongside an update (the update covers it)")
    app7.update["running"] = False
    app7.schedule_lookup()
    app7.wait_lookup()
    h.ok(len(net7.scry_calls) >= 1, "...and cards added meanwhile are looked up when it ends")
    # the lookup keeps the date of the last full update
    app8, net8 = prices_first(h)
    before = app8.mk.as_of
    app8.mk.as_of = "2000-01-01T00:00:00+00:00"
    call(app8, "POST", "/api/import/shoptcg?mode=replace", raw=fx.sample_shop())
    app8.wait_lookup()
    h.eq(app8.mk.as_of, "2000-01-01T00:00:00+00:00", "a lookup of new cards does not pretend to be a full update")
