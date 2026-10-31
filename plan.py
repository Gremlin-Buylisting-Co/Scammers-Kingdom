"""The shipment plan and the files made from it. Pure functions: no files, no network.

A "row" here is a binder row as the page sees it:
    id, name, set_name, set_code, number, finish ("nonfoil" / "foil" / "etched"),
    cond ("NM" / "EX" / "VG"), qty, sid (Scryfall id or ""),
    ck (None when CK is not buying it, else {cash, wants, name, edition, variation, url}),
    mk (market price in cents or None), link.
Money is whole cents everywhere. `S` is the settings dict.
"""
import csv
import io

GREEN = 70   # CK % of market at or above this is green
AMBER = 40   # at or above this (and below GREEN) is amber

DEFAULT_SETTINGS = {
    "bag_size": 50,
    "bag_fee": 80,        # cents, charged for every bag, full or not
    "credit_bonus": 30,   # percent on top of cash when paid in store credit
    "ex_share": 80,       # percent of the NM price CK pays for EX (an estimate)
    "vg_share": 60,       # percent of the NM price CK pays for VG (an estimate)
}
MAX_BAGS = 50
CONDITIONS = ("NM", "EX", "VG")

# How a card with variants is written in the file CK's CSV import reads.
#   "plain": the name as CK lists it
#   "paren": Name (variation), with the variation exactly as CK's list gives it
CK_IMPORT_VARIANT_STYLE = "paren"


def band(pct):
    """'g', 'a' or 'n' (green, amber, grey) for a CK % of market."""
    if pct is None:
        return "n"
    return "g" if pct >= GREEN else "a" if pct >= AMBER else "n"


# ---------- prices of one row ----------

def share_for(cond, S):
    return S["ex_share"] if cond == "EX" else S["vg_share"] if cond == "VG" else 100


def cash_each(row, S):
    """What CK pays for one copy in this row's condition, in cents (at least 1)."""
    nm = row["ck"]["cash"]
    return max(1, (nm * share_for(row["cond"], S) + 50) // 100)


def credit_each(cash, S):
    return max(1, (cash * (100 + S["credit_bonus"]) + 50) // 100)


def pct_of_market(cash, mk):
    if not mk:
        return None
    return cash / mk * 100


def allocate(rows):
    """{row id: copies CK would take}. CK never takes more copies of a printing
    than it wants, however many rows (conditions) hold that printing; the best
    paying rows use up the allowance first."""
    groups = {}
    for r in rows:
        if r.get("ck"):
            groups.setdefault((r["sid"], r["finish"]), []).append(r)
    out = {}
    for g in groups.values():
        left = g[0]["ck"]["wants"]
        for r in sorted(g, key=lambda r: (-_order_value(r), r["id"])):
            take = min(r["qty"], left)
            out[r["id"]] = take
            left -= take
    return out


def _order_value(r):
    """Better condition first: NM, then EX, then VG."""
    return {"NM": 3, "EX": 2, "VG": 1}.get(r["cond"], 0)


def ranked(rows, S):
    """Rows CK is buying, best first: highest CK % of market, then the higher CK
    price, then name. A card with no market price goes after the others.
    Returns [{row, copies, cash, credit, pct}]."""
    copies = allocate(rows)
    out = []
    for r in rows:
        if not r.get("ck") or copies.get(r["id"], 0) <= 0:
            continue
        cash = cash_each(r, S)
        out.append({"row": r, "copies": copies[r["id"]], "cash": cash,
                    "credit": credit_each(cash, S), "pct": pct_of_market(cash, r.get("mk"))})
    out.sort(key=_rank_key)
    return out


def _rank_key(e):
    r = e["row"]
    return (e["pct"] is None, -(e["pct"] or 0), -e["cash"], r["name"].lower(),
            r["set_name"].lower(), r["finish"], r["cond"], r["id"])


# ---------- the plan ----------

def build_plan(rows, S, bags, pay="cash"):
    """Take every copy CK is buying, best first, fill `bags` bags of S['bag_size']
    in that order. Only bags that hold at least one card are returned."""
    size = S["bag_size"]
    n = max(1, min(MAX_BAGS, int(bags)))
    cap = size * n
    entries = ranked(rows, S)
    available = sum(e["copies"] for e in entries)
    out_bags, cur, fill, remaining = [], [], 0, cap
    for e in entries:
        c = min(e["copies"], remaining)
        while c > 0:
            k = min(size - fill, c)
            cur.append({"row": e["row"], "qty": k, "cash": e["cash"],
                        "credit": e["credit"], "pct": e["pct"]})
            fill += k
            c -= k
            remaining -= k
            if fill == size:
                out_bags.append(cur)
                cur, fill = [], 0
        if remaining <= 0:
            break
    if cur:
        out_bags.append(cur)
    bag_list, total, fees, get_total, taken = [], 0, 0, 0, 0
    for i, lines in enumerate(out_bags):
        cards = sum(l["qty"] for l in lines)
        value = sum(l["qty"] * (l["credit"] if pay == "credit" else l["cash"]) for l in lines)
        fee = S["bag_fee"]
        get = max(0, value - fee)
        bag_list.append({"n": i + 1, "lines": lines, "cards": cards, "total": value, "fee": fee,
                         "get": get, "missing": max(0, size - cards)})
        total += value
        fees += fee
        get_total += get
        taken += cards
    return {"bags": bag_list, "requested": n, "capacity": cap, "size": size, "pay": pay,
            "taken": taken, "available": available, "left": available - taken,
            "short": available < cap, "total": total, "fees": fees, "get": get_total}


# ---------- files ----------

def _csv_text(rows, bom=False, quoting=csv.QUOTE_MINIMAL):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n", quoting=quoting)
    for r in rows:
        w.writerow(r)
    return ("\ufeff" if bom else "") + buf.getvalue()


def dollars(cents):
    return "%d.%02d" % (cents // 100, cents % 100)


def plan_csv(plan):
    """A spreadsheet of the plan (opens in Excel)."""
    rows = [["Bag", "Qty", "Card", "Set", "Number", "Foil", "Condition", "CK pays cash each",
             "CK pays credit each", "Market price", "CK % of market"]]
    for bag in plan["bags"]:
        for l in bag["lines"]:
            r = l["row"]
            foil = {"foil": "Yes", "etched": "Etched"}.get(r["finish"], "No")
            rows.append([bag["n"], l["qty"], r["name"], r["set_name"], r["number"], foil, r["cond"],
                         dollars(l["cash"]), dollars(l["credit"]),
                         dollars(r["mk"]) if r.get("mk") else "",
                         "" if l["pct"] is None else "%d%%" % int(l["pct"] + 0.5)])
    return _csv_text(rows, bom=True)


def ck_import_name(row):
    """The card name as written in the CK import file, for one printing.
    `row` is the printing's CK entry (name, variation)."""
    name = row["name"]
    if CK_IMPORT_VARIANT_STYLE == "paren" and row.get("variation"):
        return "%s (%s)" % (name, row["variation"])
    return name


def ck_import_lines(plan):
    """[(name, edition, foil "TRUE"/"FALSE", quantity)] for the whole shipment:
    one line per printing, quantities of the same printing added together."""
    order, qty = [], {}
    for bag in plan["bags"]:
        for l in bag["lines"]:
            ck = l["row"]["ck"]
            key = (ck_import_name(ck), ck["edition"], "FALSE" if l["row"]["finish"] == "nonfoil" else "TRUE")
            if key not in qty:
                qty[key] = 0
                order.append(key)
            qty[key] += l["qty"]
    return [(k[0], k[1], k[2], qty[k]) for k in order]


def ck_import_csv(plan):
    """Card Kingdom's CSV import format: no header row; card name, edition, foil, quantity.
    CK grades cards when they arrive, so there is no condition column."""
    return _csv_text([list(l) for l in ck_import_lines(plan)])
