"""IMPORT, EXPORTIMPORT and PERSIST."""
import csv
import io
import json
import os
from datetime import timedelta

import collection
import fixtures as fx
import storage
from harness import call, loaded_app, make_app, rows_by_name


def norm_rows(rows):
    keep = ("name", "set_name", "set_code", "number", "finish", "condition", "qty", "src", "sid")
    return sorted([tuple(r[k] for k in keep) for r in rows])


def hand_add(app, name, finish="nonfoil", cond="NM", amount=1, variation=""):
    sid = fx.printing_id(name, variation)
    st, j = call(app, "POST", "/api/add", {"sid": sid, "finish": finish, "condition": cond, "amount": amount})
    assert st == 200, (st, j)
    return j


# ------------------------------------------------------------------ IMPORT
def import_tests(h):
    h.section("IMPORT")
    parsed = collection.read_shoptcg(fx.sample_shop())
    rows = {(r["set_code"], r["number"], r["finish"]): r for r in parsed["rows"]}
    h.eq(len(parsed["rows"]), 11, "rows merged by printing + finish (11 from 15 lines)")
    h.eq(rows[("cmm", "410", "nonfoil")]["qty"], 3, "same printing + finish: quantities added (2 + 1)")
    h.eq(rows[("cmm", "410", "foil")]["qty"], 1, "the foil copy is its own row")
    h.ok(("pf18", "2018f", "foil") in rows, "promo collector number 2018f kept as written, set code lower case")
    h.ok(("spc", "241s", "etched") in rows, "promo number 241s and the Etched finish")
    h.eq(rows[("cmm", "410", "nonfoil")]["scan_usd"], 110, "the scan's own USD price is kept as a stand-in (cents)")
    h.eq(len(parsed["other_language"]), 1, "non-English rows are listed apart")
    h.eq(parsed["other_language"][0]["qty"], 2, "the other-language row keeps its quantity")
    h.ok(not any(r["name"] == "Blitz de Luz" for r in parsed["rows"]), "non-English cards are not in the binder rows")
    h.eq(sorted(s["why"] for s in parsed["skipped"]), ["no card name", "quantity is not a whole number"],
         "unusable rows are skipped with a reason")

    # header
    try:
        collection.read_shoptcg("Name,Qty\nSol Ring,1\n")
        h.ok(False, "a file without the shoptcg.online header is refused")
    except collection.BadFile as e:
        h.ok(all(c in str(e) for c in ("Card Name", "Set Code", "Collector Number", "Finish", "Quantity")),
             "the refusal names the columns it needs")
    try:
        collection.read_shoptcg("")
        h.ok(False, "an empty file is refused")
    except collection.BadFile:
        h.ok(True, "an empty file is refused")
    h.eq(collection.SHOP_HEADER, fx.HEADER.split(","), "the expected header matches the shoptcg.online export exactly")

    # through the program
    app, net = make_app(h)
    st, j = call(app, "POST", "/api/import/shoptcg?mode=replace", raw=fx.sample_shop())
    h.eq(st, 200, "import answers 200")
    h.eq({r["cond"] for r in j["rows"]}, {"NM"}, "every card starts as NM")
    h.eq(j["last_import"]["copies"], 41, "41 copies counted")
    h.eq(j["other_language"][0]["language"], "Portuguese", "other-language rows are stored for display")
    st, j = call(app, "POST", "/api/import/shoptcg?mode=replace", raw="Name,Qty\nx,1\n")
    h.eq(st, 400, "a bad file is refused with 400")
    h.ok("Card Name" in j["error"], "the answer names the needed columns")
    st, j = call(app, "POST", "/api/import/shoptcg?mode=replace", raw=fx.sample_shop().replace("CMM", "cmm"))
    h.eq(st, 200, "set codes in either case are accepted")

    # matching after prices
    app, net = loaded_app(h)
    st, s = call(app, "GET", "/api/state")
    sol = rows_by_name(s, "Sol Ring")
    h.eq([r["ck"]["cash"] for r in sorted(sol, key=lambda r: r["finish"])], [120, 50],
         "matched by set code + number + finish: foil and non-foil get their own CK price")
    h.ok(all(r["sid"] == fx.printing_id("Sol Ring") for r in sol), "both finishes carry the Scryfall id from the lookup")
    h.eq(rows_by_name(s, "Mystic Remora")[0]["ck"], None, "a printing CK is not buying has no CK price")
    h.ok(rows_by_name(s, "Promo Thing")[0]["unknown"], "a printing Scryfall does not know is marked as not found")

    # modes
    before = rows_by_name(s, "Sol Ring", "nonfoil")[0]["qty"]
    st, j = call(app, "POST", "/api/import/shoptcg?mode=add", raw=fx.sample_shop())
    h.eq(rows_by_name(j, "Sol Ring", "nonfoil")[0]["qty"], before * 2, "Add merges into existing rows (quantities added)")
    h.eq(len(rows_by_name(j, "Sol Ring", "nonfoil")), 1, "Add does not make a second row for the same printing")
    st, j = call(app, "POST", "/api/import/shoptcg?mode=replace", raw=fx.sample_shop())
    h.eq(rows_by_name(j, "Sol Ring", "nonfoil")[0]["qty"], before, "Replace puts back exactly the file's quantities")
    st, j = call(app, "POST", "/api/import/shoptcg?mode=sideways", raw=fx.sample_shop())
    h.eq(st, 400, "an unknown mode is refused")

    # Replace keeps hand-added cards
    hand_add(app, "Rhystic Study", cond="EX", amount=2)
    hand_add(app, "Sol Ring", amount=1)
    st, j = call(app, "POST", "/api/import/shoptcg?mode=replace", raw=fx.shop_csv([fx.shop_line("Counterspell", "Modern Horizons 2", "MH2", "267", qty=5)]))
    names = sorted((r["name"], r["src"]) for r in j["rows"])
    h.eq(names, [("Counterspell", "import"), ("Rhystic Study", "hand"), ("Sol Ring", "hand")],
         "Replace replaces only imported cards; cards added by hand are kept")
    h.ok("kept" in j["label"] and "hand" in j["label"], "the message says hand-added cards were kept")
    h.eq(j["other_language"], [], "Replace also replaces the other-language list")


# ------------------------------------------------------------------ EXPORTIMPORT
def exportimport_tests(h):
    h.section("EXPORTIMPORT")
    app, net = loaded_app(h)
    hand_add(app, "Rhystic Study", cond="VG", amount=2)
    st, s = call(app, "GET", "/api/state")
    sol = rows_by_name(s, "Counterspell")[0]
    call(app, "POST", "/api/edit", {"op": "condition", "id": sol["id"], "condition": "EX"})

    st, text = call(app, "GET", "/download/collection.csv")
    raw = call.last.body
    h.eq(st, 200, "export answers 200")
    h.ok(raw.startswith(b"\xef\xbb\xbf"), "the file starts with a byte order mark so Excel opens it")
    lines = text.lstrip("﻿").split("\r\n")
    h.eq(lines[0], "Card Name,Set,Set Code,Collector Number,Finish,Condition,Quantity,Added By Hand,Scryfall ID", "exact header")
    h.ok('"Sheoldred, the Apocalypse"' in text, "a name with a comma is quoted")
    h.ok("scammers-kingdom-collection-" in call.last.headers["Content-Disposition"] and
         call.last.headers["Content-Disposition"].endswith('.csv"'), "download name scammers-kingdom-collection-YYYY-MM-DD.csv")
    parsed_back = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    h.eq(len(parsed_back) - 1, len(s["rows"]), "one line per binder row")
    h.ok(any(r[5] == "EX" for r in parsed_back[1:]) and any(r[7] == "Yes" for r in parsed_back[1:]),
         "condition and Added By Hand are written")

    # round trip into a fresh binder
    app2, _ = make_app(h)
    st, c = call(app2, "POST", "/api/import/collection?mode=check", raw=text)
    h.eq((st, c["cards"], c["mine"]), (200, sum(r["qty"] for r in s["rows"]), 0), "check names both counts")
    st, j = call(app2, "POST", "/api/import/collection?mode=replace", raw=text)
    st, s1 = call(app, "GET", "/api/state")
    h.eq(norm_rows(app2.binder.rows), norm_rows(app.binder.rows), "Export then Import of the same file gives the same binder")
    st, text2 = call(app2, "GET", "/download/collection.csv")
    h.eq(text2, text, "exporting the restored binder gives the same file")

    # Replace and Add on a non-empty binder
    before = app2.binder.copies()
    st, j = call(app2, "POST", "/api/import/collection?mode=add", raw=text)
    h.eq(app2.binder.copies(), before * 2, "Add to my binder adds the quantities onto matching rows")
    h.eq(len(app2.binder.rows), len(app.binder.rows), "Add does not duplicate rows that match")
    st, j = call(app2, "POST", "/api/import/collection?mode=replace", raw=text)
    h.eq(app2.binder.copies(), before, "Replace puts the binder back to exactly the file")
    st, c = call(app2, "POST", "/api/import/collection?mode=check", raw=text)
    h.eq(c["mine"], before, "the check reports the current binder's count too")

    # refusals
    st, j = call(app2, "POST", "/api/import/collection?mode=check", raw=fx.sample_shop())
    h.eq(st, 400, "a shoptcg.online file is refused here")
    h.ok("shoptcg" in j["error"] and "Step 1" in j["error"], "...and told to use the other import")
    st, j = call(app2, "POST", "/api/import/collection?mode=check", raw="a,b,c\n1,2,3\n")
    h.eq(st, 400, "any other file is refused")
    h.eq(j["error"], "That file was not made by Export My Collection.", "...in one sentence")
    st, j = call(app, "POST", "/api/import/shoptcg?mode=replace", raw=text)
    h.eq(st, 400, "an Export My Collection file is refused by the shoptcg.online import")
    h.ok("Import My Collection" in j["error"], "...and told to use Import My Collection")

    # unplaceable rows are listed
    head = ",".join(collection.EXPORT_HEADER)
    bad = "\r\n".join([head,
                       "Sol Ring,Commander Masters,cmm,410,Non-Foil,NM,2,No,",
                       "Mystery,Nowhere,,,Non-Foil,NM,1,No,",
                       "Wastes,Modern Horizons 3,mh3,310,Non-Foil,NM,zero,No,",
                       "Island,Bloomburrow,,," + "Foil,VG,3,Yes," + fx.printing_id("Island")]) + "\r\n"
    app3, _ = make_app(h)
    st, j = call(app3, "POST", "/api/import/collection?mode=replace", raw=bad)
    h.eq(st, 200, "a file with some bad rows still imports the good ones")
    h.eq(j["unplaceable_count"], 2, "two rows could not be placed")
    h.eq(sorted(u["name"] for u in j["unplaceable"]), ["Mystery", "Wastes"], "the rows that cannot be placed are listed by name")
    h.eq(len(j["rows"]), 2, "the other two rows were placed (one by set + number, one by Scryfall id)")
    isl = rows_by_name(j, "Island")[0]
    h.eq((isl["finish"], isl["cond"], isl["src"], isl["qty"]), ("foil", "VG", "hand", 3), "finish, condition, hand flag and quantity restored")

    # one undo step
    h.eq(len(app3.binder.history), 1, "the import is one history entry")
    st, j = call(app3, "POST", "/api/undo")
    h.eq(len(j["rows"]), 0, "one Undo takes the whole import back")

    # 14-day reminder
    app4, _ = make_app(h)
    clock = {"now": app4.clock()}
    app4.clock = lambda: clock["now"]
    st, s = call(app4, "GET", "/api/state")
    h.eq(s["remind"], False, "no reminder with an empty binder")
    call(app4, "POST", "/api/import/collection?mode=replace", raw=text)
    clock["now"] += timedelta(days=13)
    h.eq(call(app4, "GET", "/api/state")[1]["remind"], False, "no reminder before 14 days")
    clock["now"] += timedelta(days=2)
    h.eq(call(app4, "GET", "/api/state")[1]["remind"], True, "a line suggests exporting after 14 days without an export")
    call(app4, "POST", "/api/ui", {"action": "dismiss_reminder"})
    h.eq(call(app4, "GET", "/api/state")[1]["remind"], False, "it can be dismissed")
    clock["now"] += timedelta(days=15)
    h.eq(call(app4, "GET", "/api/state")[1]["remind"], True, "it comes back 14 days after being dismissed")
    call(app4, "GET", "/download/collection.csv")
    h.eq(call(app4, "GET", "/api/state")[1]["remind"], False, "an export clears it")
    h.ok(app4.state["last_export"] is not None, "state.json keeps the last export time")
    clock["now"] += timedelta(days=14, minutes=1)
    h.eq(call(app4, "GET", "/api/state")[1]["remind"], True, "and counts from that export")


# ------------------------------------------------------------------ PERSIST
def persist_tests(h):
    h.section("PERSIST")
    folder = h.tmp()
    app, net = loaded_app(h, folder=folder)
    hand_add(app, "Rhystic Study", cond="EX", amount=2)
    st, s = call(app, "GET", "/api/state")
    c = rows_by_name(s, "Counterspell")[0]
    call(app, "POST", "/api/edit", {"op": "condition", "id": c["id"], "condition": "VG"})
    call(app, "POST", "/api/settings", {"bag_size": 40})
    call(app, "POST", "/api/settings", {"ex_share": 75})
    st, s = call(app, "GET", "/api/state")
    for name in ("collection.json", "state.json", "settings.json"):
        h.ok(os.path.exists(os.path.join(folder, name)), "%s is written in the data folder" % name)
    for name in ("ck_latest.json", "market.json"):
        h.ok(os.path.exists(os.path.join(folder, "prices", name)), "prices/%s is written" % name)

    import server
    again = server.App(folder, check_updates=False)        # a restart, in the same folder
    st, s2 = call(again, "GET", "/api/state")
    h.eq(norm_rows(again.binder.rows), norm_rows(app.binder.rows), "restart: the binder is read back")
    h.eq(rows_by_name(s2, "Counterspell")[0]["cond"], "VG", "restart: conditions are kept")
    h.eq(s2["settings"]["bag_size"], 40, "restart: settings are kept")
    h.eq(s2["settings"]["ex_share"], 75, "restart: every setting is kept")
    h.eq(s2["undo"]["count"], s["undo"]["count"], "restart: undo history is kept")
    st, u = call(again, "POST", "/api/undo")
    h.eq(rows_by_name(u, "Counterspell")[0]["cond"], "NM", "restart: undo still works (the condition goes back)")
    h.ok(s2["prices"]["has_ck"] and rows_by_name(s2, "Sol Ring")[0]["ck"] is not None, "restart: prices are read back too")
    h.eq([r["id"] for r in s["rows"]], [r["id"] for r in s2["rows"]], "restart: rows keep their ids")

    # the data folder lives with the program; a new program file in the same folder finds it
    h.eq(storage.data_dir(frozen=True, executable=os.path.join("C:", os.sep, "Apps", "ScammersKingdom.exe")),
         os.path.join("C:", os.sep, "Apps", "ScammersKingdom-data"), "the data folder sits next to the .exe")

    # safe writes
    d = h.tmp()
    p = os.path.join(d, "x.json")
    storage.write_json(p, {"a": 1})
    h.eq(json.load(open(p)), {"a": 1}, "write_json writes the file")
    h.eq([f for f in os.listdir(d) if f.endswith(".tmp")], [], "no temporary file is left behind")
    fails = {"n": 0}

    def flaky(src, dst):
        fails["n"] += 1
        if fails["n"] <= 3:
            raise PermissionError("busy")
        os.replace(src, dst)
    storage.write_json(p, {"a": 2}, replace=flaky, sleep=lambda s: None)
    h.eq((json.load(open(p)), fails["n"]), ({"a": 2}, 4), "a write is retried 3 times when the replace fails")
    fails["n"] = -100
    try:
        storage.write_json(p, {"a": 3}, replace=flaky, sleep=lambda s: None)
        h.ok(False, "a write that keeps failing raises")
    except PermissionError:
        h.ok(True, "a write that keeps failing raises")
    h.eq(json.load(open(p)), {"a": 2}, "a failed write leaves the old file untouched")
    h.eq([f for f in os.listdir(d) if f.endswith(".tmp")], [], "...and cleans up its temporary file")
    open(p, "w").write("{broken")
    h.eq(storage.read_json(p, "dflt"), "dflt", "a damaged file gives the default")
    h.ok(os.path.exists(p + ".broken"), "...and is set aside, not deleted")
    # a damaged binder file starts an empty binder and tells the person
    folder2 = h.tmp()
    open(os.path.join(folder2, "collection.json"), "w").write("{nope")
    a2 = server.App(folder2, check_updates=False)
    h.ok(len(a2.binder.rows) == 0 and a2.warnings, "a damaged collection.json is set aside with a warning")
