"""The binder: the cards a person owns, the edits they make, and the files that go in and out.

A stored row looks like:
    id, name, set_name, set_code (lower case or ""), number (lower case or ""),
    finish ("nonfoil" / "foil" / "etched"), condition ("NM" / "EX" / "VG"), qty,
    src ("import" or "hand"), sid (Scryfall id or ""), scan_usd (cents or None)

Every change goes through Binder, which keeps the last 20 changes so each can be undone.
"""
import csv
import io
import re

import ck_prices
import market as market_mod

CONDITIONS = ("NM", "EX", "VG")
FINISHES = ("nonfoil", "foil", "etched")
MAX_QTY = 9999
MAX_ADD = 999
HISTORY = 20
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

SHOP_HEADER = ["Card Name", "Set", "Set Code", "Collector Number", "Finish", "Condition", "Language",
               "Quantity", "Purchase Price CAD", "List Price CAD", "TCGplayer CAD", "TCGplayer USD",
               "Store price CAD", "Binder"]
SHOP_NEEDED = ["Card Name", "Set", "Set Code", "Collector Number", "Finish", "Language", "Quantity"]
EXPORT_HEADER = ["Card Name", "Set", "Set Code", "Collector Number", "Finish", "Condition", "Quantity",
                 "Added By Hand", "Scryfall ID"]
FINISH_WORDS = {"nonfoil": "Non-Foil", "foil": "Foil", "etched": "Etched"}


class BadFile(Exception):
    """A file that cannot be used. The message says why, in plain words."""


class EditError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def clamp_qty(n):
    return max(0, min(MAX_QTY, n))


# ---------- reading csv ----------

def _read_csv(text):
    text = text.lstrip("\ufeff")
    return [r for r in csv.reader(io.StringIO(text, newline="")) if any(c.strip() for c in r)]


def _finish_from_word(word):
    w = word.strip().lower().replace(" ", "")
    if w == "foil":
        return "foil"
    if "etched" in w:
        return "etched"
    if w in ("non-foil", "nonfoil", "normal", ""):
        return "nonfoil"
    return None


def _whole(text):
    try:
        v = float(str(text).strip())
    except ValueError:
        return None
    return int(v) if v == int(v) else None


# ---------- the shoptcg.online file ----------

def read_shoptcg(text):
    """Parse a shoptcg.online export. Returns {rows, skipped, other_language}.
    Rows are merged by printing + finish; the file's condition and prices are
    ignored (every card starts as NM; prices come from Scryfall) except the
    scan's own market price, kept as a stand-in until the first lookup."""
    rows = _read_csv(text)
    if not rows:
        raise BadFile("That file is empty.")
    header = [h.strip() for h in rows[0]]
    if header == EXPORT_HEADER:
        raise BadFile("That file was made by Export My Collection. Use Import My Collection for it.")
    missing = [c for c in SHOP_NEEDED if c not in header]
    if missing:
        raise BadFile("This does not look like a shoptcg.online export. The first line needs these columns: "
                      + ", ".join(SHOP_NEEDED) + ".")
    col = {h: i for i, h in enumerate(header)}

    def get(r, name):
        i = col.get(name)
        return r[i].strip() if i is not None and i < len(r) else ""

    merged, order, skipped, other, other_order = {}, [], [], {}, []
    for line, r in enumerate(rows[1:], start=2):
        name = get(r, "Card Name")
        code = get(r, "Set Code").lower()
        number = get(r, "Collector Number").lower()
        finish = _finish_from_word(get(r, "Finish"))
        qty = _whole(get(r, "Quantity"))
        why = None
        if not name:
            why = "no card name"
        elif not code or not number:
            why = "no set code or collector number"
        elif finish is None:
            why = "finish not understood"
        elif qty is None or qty < 1:
            why = "quantity is not a whole number"
        if why:
            skipped.append({"line": line, "name": name or "(no name)", "why": why})
            continue
        lang = get(r, "Language")
        if lang.lower() not in ("", "english", "en"):
            k = (code, number, finish, lang.lower())
            if k not in other:
                other[k] = {"name": name, "set_name": get(r, "Set"), "set_code": code, "number": number,
                            "finish": finish, "qty": 0, "language": lang}
                other_order.append(k)
            other[k]["qty"] += qty
            continue
        k = (code, number, finish)
        if k not in merged:
            merged[k] = {"name": name, "set_name": get(r, "Set"), "set_code": code, "number": number,
                         "finish": finish, "qty": 0, "scan_usd": None}
            order.append(k)
        m = merged[k]
        m["qty"] += qty
        usd = get(r, "TCGplayer USD")
        if m["scan_usd"] is None and usd:
            c = ck_prices.to_cents(usd)
            m["scan_usd"] = c if c > 0 else None
    return {"rows": [merged[k] for k in order], "skipped": skipped,
            "other_language": [other[k] for k in other_order]}


# ---------- Export / Import My Collection ----------

def export_csv(rows, info):
    """The collection file, as text starting with a byte order mark so Excel opens it.
    `info(row)` gives (set_code, number, scryfall id) with anything the row itself lacks."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(EXPORT_HEADER)
    for r in rows:
        code, number, sid = info(r)
        w.writerow([r["name"], r["set_name"], code, number, FINISH_WORDS[r["finish"]], r["condition"],
                    r["qty"], "Yes" if r["src"] == "hand" else "No", sid])
    return "\ufeff" + buf.getvalue()


def read_my_collection(text):
    """Parse a file made by Export My Collection. Returns {rows, unplaceable, copies}."""
    rows = _read_csv(text)
    header = [h.strip() for h in rows[0]] if rows else []
    if header != EXPORT_HEADER:
        if all(c in header for c in SHOP_NEEDED):
            raise BadFile("That is a shoptcg.online file: use the shoptcg.online import on Step 1 for it.")
        raise BadFile("That file was not made by Export My Collection.")
    out, bad = [], []
    for line, r in enumerate(rows[1:], start=2):
        r = [c.strip() for c in r] + [""] * (len(EXPORT_HEADER) - len(r))
        name, set_name, code, number, fin, cond, qty, hand, sid = r[:9]
        finish = _finish_from_word(fin) if fin else None
        q = _whole(qty)
        sid = sid.lower() if UUID_RE.match(sid.lower()) else ""
        why = None
        if not name:
            why = "no card name"
        elif finish is None:
            why = "finish not understood"
        elif cond.upper() not in CONDITIONS:
            why = "condition not understood"
        elif q is None or q < 1:
            why = "quantity is not a whole number"
        elif not sid and not (code and number):
            why = "no Scryfall ID, and no set code with a number"
        if why:
            bad.append({"line": line, "name": name or "(no name)", "why": why})
            continue
        out.append({"name": name, "set_name": set_name, "set_code": code.lower(), "number": number.lower(),
                    "finish": finish, "condition": cond.upper(), "qty": min(q, MAX_QTY),
                    "src": "hand" if hand.lower() in ("yes", "true", "1") else "import", "sid": sid})
    return {"rows": out, "unplaceable": bad, "copies": sum(r["qty"] for r in out)}


# ---------- the binder ----------

class Binder:
    def __init__(self, data=None, history=None):
        data = data if isinstance(data, dict) else {}
        self.rows = [self._clean(r) for r in data.get("rows", []) if isinstance(r, dict)]
        self.next_id = max([data.get("next_id", 1)] + [r["id"] + 1 for r in self.rows])
        self.other = list(data.get("other_language") or [])
        self.history = [h for h in (history or []) if isinstance(h, dict)][-HISTORY:]

    @staticmethod
    def _clean(r):
        return {"id": int(r["id"]), "name": str(r.get("name", "")), "set_name": str(r.get("set_name", "")),
                "set_code": str(r.get("set_code", "")).lower(), "number": str(r.get("number", "")).lower(),
                "finish": r.get("finish") if r.get("finish") in FINISHES else "nonfoil",
                "condition": r.get("condition") if r.get("condition") in CONDITIONS else "NM",
                "qty": clamp_qty(int(r.get("qty", 1))), "src": "hand" if r.get("src") == "hand" else "import",
                "sid": str(r.get("sid", "")).lower(), "scan_usd": r.get("scan_usd")}

    def to_json(self):
        return {"version": 1, "next_id": self.next_id, "rows": self.rows, "other_language": self.other}

    def copies(self):
        return sum(r["qty"] for r in self.rows)

    def by_id(self, rid):
        for r in self.rows:
            if r["id"] == rid:
                return r
        return None

    # -- bookkeeping for undo
    def _snapshot(self):
        return {"rows": {r["id"]: dict(r) for r in self.rows}, "other": [dict(o) for o in self.other]}

    def _record(self, snap, label):
        before, now = snap["rows"], {r["id"]: r for r in self.rows}
        ops = [[i, row] for i, row in before.items() if i not in now or now[i] != row]
        ops += [[i, None] for i in now if i not in before]
        other_changed = snap["other"] != self.other
        if not ops and not other_changed:
            return False
        self.history.append({"label": label, "ops": ops, "other": snap["other"] if other_changed else None})
        del self.history[:-HISTORY]
        return True

    def undo(self):
        """Take back the last change. Returns its label."""
        if not self.history:
            raise EditError(409, "Nothing to undo.")
        h = self.history.pop()
        rows = {r["id"]: r for r in self.rows}
        for rid, before in h["ops"]:
            if before is None:
                rows.pop(rid, None)
            else:
                rows[rid] = before
        self.rows = [rows[i] for i in sorted(rows)]
        if h.get("other") is not None:
            self.other = h["other"]
        return h["label"]

    # -- finding the same printing
    @staticmethod
    def _idents(row, mk):
        """Names under which a row's printing is known. Two rows with one name in common are the same printing."""
        out = set()
        if row["sid"]:
            out.add("id:" + row["sid"])
        k = market_mod.sn_key(row)
        if k:
            out.add(k)
        e = mk.entry_for(row) if mk else None
        if e and e.get("id"):
            out.add("id:" + e["id"])
        return out

    def _index(self, mk, rows=None):
        idx = {}
        for r in (self.rows if rows is None else rows):
            for k in self._idents(r, mk):
                idx.setdefault((k, r["finish"], r["condition"]), []).append(r)
        return idx

    def _find(self, idx, row, mk, skip=None):
        for k in self._idents(row, mk):
            for other in idx.get((k, row["finish"], row["condition"]), ()):
                if other is not row and (skip is None or other["id"] not in skip):
                    return other
        return None

    def _merge_rows(self, ids, mk):
        """Rows that became the same printing + finish + condition as another row join it. Returns how many joined."""
        gone, n = set(), 0
        idx = self._index(mk)
        for rid in sorted(ids):
            row = self.by_id(rid)
            if row is None or rid in gone:
                continue
            hit = self._find(idx, row, mk, gone)
            if hit is None:
                continue
            hit["qty"] = clamp_qty(hit["qty"] + row["qty"])
            if row["src"] == "hand":
                hit["src"] = "hand"
            gone.add(rid)
            n += 1
        if gone:
            self.rows = [r for r in self.rows if r["id"] not in gone]
        return n

    def _new_row(self, **kw):
        row = {"id": self.next_id, "name": "", "set_name": "", "set_code": "", "number": "",
               "finish": "nonfoil", "condition": "NM", "qty": 1, "src": "import", "sid": "", "scan_usd": None}
        row.update(kw)
        self.next_id += 1
        return row

    # -- the one edit route
    def edit(self, payload, ck_rows, mk):
        """Apply one edit. Returns the label (None when nothing changed).
        Raises EditError(404) for an unknown row and EditError(400) for a bad value."""
        if not isinstance(payload, dict):
            raise EditError(400, "That was not a valid edit.")
        op = payload.get("op")
        if op not in ("qty", "condition", "printing", "remove"):
            raise EditError(400, "That edit is not known.")
        ids = payload.get("ids", [payload["id"]] if "id" in payload else None)
        if not isinstance(ids, list) or not ids or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            raise EditError(400, "No rows were named.")
        ids = sorted(set(ids))
        rows = []
        for i in ids:
            row = self.by_id(i)
            if row is None:
                raise EditError(404, "That card is no longer in your binder.")
            rows.append(row)
        snap = self._snapshot()
        label = None
        if op == "remove":
            self.rows = [r for r in self.rows if r["id"] not in set(ids)]
            label = "Removed %s." % (rows[0]["name"] if len(rows) == 1 else "%d rows" % len(rows))
        elif op == "qty":
            q = payload.get("qty")
            if not isinstance(q, int) or isinstance(q, bool) or q < 0 or q > MAX_QTY or len(rows) != 1:
                raise EditError(400, "Quantity must be a whole number from 0 to %d." % MAX_QTY)
            row = rows[0]
            if q == 0:
                self.rows = [r for r in self.rows if r["id"] != row["id"]]
                label = "Removed %s." % row["name"]
            elif q != row["qty"]:
                row["qty"] = q
                label = "Quantity of %s is now %d." % (row["name"], q)
        elif op == "condition":
            cond = payload.get("condition")
            if cond not in CONDITIONS:
                raise EditError(400, "Condition must be NM, EX or VG.")
            changed = [r for r in rows if r["condition"] != cond]
            for r in changed:
                r["condition"] = cond
            if changed:
                merged = self._merge_rows([r["id"] for r in changed], mk)
                what = rows[0]["name"] + " is now " + cond + "." if len(rows) == 1 else \
                    "Set %d rows to %s." % (len(rows), cond)
                label = what + (" Merged with a matching row (quantities added)." if merged else "")
        else:  # printing
            if len(rows) != 1:
                raise EditError(400, "Change one card's printing at a time.")
            sid, finish = str(payload.get("sid", "")).lower(), payload.get("finish")
            entry = ck_rows.get("%s|%s" % (sid, finish)) if finish in FINISHES else None
            if entry is None:
                raise EditError(400, "That printing is not in Card Kingdom's list.")
            row = rows[0]
            if row["sid"] != sid or row["finish"] != finish:
                row.update(name=entry["name"], set_name=entry["edition"], set_code="", number="",
                           sid=sid, finish=finish, scan_usd=None)
                merged = self._merge_rows([row["id"]], mk)
                label = "Changed %s to %s%s." % (entry["name"], entry["edition"],
                                                 " (foil)" if finish != "nonfoil" else "")
                label += " Merged with a matching row (quantities added)." if merged else ""
        if label is None:
            return None
        self._record(snap, label)
        return label

    def add_hand(self, sid, finish, condition, amount, ck_rows, mk):
        sid = str(sid or "").lower()
        entry = ck_rows.get("%s|%s" % (sid, finish)) if finish in FINISHES else None
        if entry is None:
            raise EditError(400, "That printing is not in Card Kingdom's list.")
        if condition not in CONDITIONS:
            raise EditError(400, "Condition must be NM, EX or VG.")
        if not isinstance(amount, int) or isinstance(amount, bool) or not 1 <= amount <= MAX_ADD:
            raise EditError(400, "Amount must be a whole number from 1 to %d." % MAX_ADD)
        snap = self._snapshot()
        probe = {"sid": sid, "finish": finish, "condition": condition, "set_code": "", "number": ""}
        hit = None
        for r in self.rows:
            if r["src"] == "hand" and r["finish"] == finish and r["condition"] == condition and \
                    self._idents(r, mk) & self._idents(probe, mk):
                hit = r
                break
        if hit:
            hit["qty"] = clamp_qty(hit["qty"] + amount)
        else:
            self.rows.append(self._new_row(name=entry["name"], set_name=entry["edition"], finish=finish,
                                           condition=condition, qty=amount, src="hand", sid=sid))
        label = "Added %d x %s (%s%s), %s." % (amount, entry["name"], entry["edition"],
                                               ", foil" if finish != "nonfoil" else "", condition)
        self._record(snap, label)
        return label

    # -- imports
    def import_shoptcg(self, parsed, mode, mk):
        if mode not in ("replace", "add"):
            raise EditError(400, "Choose Replace or Add.")
        snap = self._snapshot()
        had_hand = any(r["src"] == "hand" for r in self.rows)
        if mode == "replace":
            self.rows = [r for r in self.rows if r["src"] != "import"]
            self.other = [dict(o) for o in parsed["other_language"]]
            idx = {}
        else:
            idx = self._index(mk, [r for r in self.rows if r["src"] == "import" and r["condition"] == "NM"])
            merged_other = {(o["set_code"], o["number"], o["finish"], o["language"].lower()): dict(o) for o in self.other}
            for o in parsed["other_language"]:
                k = (o["set_code"], o["number"], o["finish"], o["language"].lower())
                if k in merged_other:
                    merged_other[k]["qty"] += o["qty"]
                else:
                    merged_other[k] = dict(o)
            self.other = list(merged_other.values())
        copies = 0
        for p in parsed["rows"]:
            copies += p["qty"]
            probe = {"sid": "", "set_code": p["set_code"], "number": p["number"], "finish": p["finish"],
                     "condition": "NM"}
            hit = self._find(idx, probe, mk) if idx else None
            if hit:
                hit["qty"] = clamp_qty(hit["qty"] + p["qty"])
                if hit["scan_usd"] is None:
                    hit["scan_usd"] = p["scan_usd"]
                continue
            row = self._new_row(name=p["name"], set_name=p["set_name"], set_code=p["set_code"],
                                number=p["number"], finish=p["finish"], qty=clamp_qty(p["qty"]),
                                src="import", scan_usd=p["scan_usd"])
            self.rows.append(row)
            if mode == "add":
                for k in self._idents(row, mk):
                    idx.setdefault((k, row["finish"], "NM"), []).append(row)
        if mode == "replace":
            label = "Replaced your imported cards with %d from the file." % copies
            if had_hand:
                label += " Cards you added by hand were kept."
        else:
            label = "Added %d cards from the file to your binder." % copies
        self._record(snap, label)
        return {"label": label, "copies": copies}

    def import_collection(self, parsed, mode, mk):
        if mode not in ("replace", "add"):
            raise EditError(400, "Choose Replace or Add to my binder.")
        snap = self._snapshot()
        copies = parsed["copies"]
        if mode == "replace":
            self.rows = []
            idx = None
        else:
            idx = self._index(mk)
        for p in parsed["rows"]:
            probe = {"sid": p["sid"], "set_code": p["set_code"], "number": p["number"], "finish": p["finish"],
                     "condition": p["condition"]}
            hit = self._find(idx, probe, mk) if idx is not None else None
            if hit:
                hit["qty"] = clamp_qty(hit["qty"] + p["qty"])
                if p["src"] == "hand":
                    hit["src"] = "hand"
                continue
            row = self._new_row(name=p["name"], set_name=p["set_name"], set_code=p["set_code"],
                                number=p["number"], finish=p["finish"], condition=p["condition"],
                                qty=p["qty"], src=p["src"], sid=p["sid"])
            self.rows.append(row)
            if idx is not None:
                for k in self._idents(row, mk):
                    idx.setdefault((k, row["finish"], row["condition"]), []).append(row)
        label = ("Replaced your binder with %d cards from the file." if mode == "replace"
                 else "Added %d cards from the file to your binder.") % copies
        self._record(snap, label)
        return {"label": label, "copies": copies}


# ---------- what the page sees ----------

def resolve(row, mk):
    """(set code, number, Scryfall id) for a row, using the market cache for what it lacks."""
    e = mk.entry_for(row)
    return (row["set_code"] or (e["set"] if e else ""), row["number"] or (e["number"] if e else ""),
            row["sid"] or (e["id"] if e else ""))


def view_rows(rows, ck_rows, mk, back_keys=()):
    """Each stored row joined with CK's price, the market price and the buylist link."""
    out = []
    for r in rows:
        e = mk.entry_for(r)
        sid = r["sid"] or (e["id"] if e else "")
        ck = ck_rows.get("%s|%s" % (sid, r["finish"])) if sid else None
        buying = ck_prices.is_buying(ck)
        price, src = market_mod.price_for(e, r["finish"]), "scryfall"
        if e is None and r["scan_usd"]:
            price, src = r["scan_usd"], "scan"
        if price is None:
            src = None
        key = "%s|%s" % (sid, r["finish"])
        out.append({
            "id": r["id"], "name": r["name"] or (e["name"] if e else ""),
            "set_name": r["set_name"] or (e["set_name"] if e else ""),
            "set_code": r["set_code"] or (e["set"] if e else ""),
            "number": r["number"] or (e["number"] if e else ""),
            "finish": r["finish"], "cond": r["condition"], "qty": r["qty"], "src": r["src"], "sid": sid,
            "ck": {k: ck[k] for k in ("cash", "wants", "name", "edition", "variation", "url")} if buying else None,
            "mk": price, "mk_src": src,
            "back": bool(buying and key in back_keys),
            "unknown": mk.is_unknown(r),
            "unresolved": bool(market_mod.row_key(r)) and e is None and market_mod.row_key(r) not in mk.missing,
            "link": ck_prices.buylist_url(ck["name"], r["finish"], ck["url"]) if buying else None,
        })
    return out
