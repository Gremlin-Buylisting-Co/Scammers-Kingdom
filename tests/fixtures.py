"""Made-up data for the tests: a small Card Kingdom list, Scryfall answers and shoptcg.online files.
Every id, price and quantity here is invented."""
import gzip
import io
import json
import uuid

NS = uuid.UUID("0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d")


def sid(key):
    return str(uuid.uuid5(NS, key))


# name, CK edition, set code, number, market usd, market foil usd, CK cash (NM), CK wants, CK foil cash, CK foil wants
CARDS = [
    ("Sol Ring", "Commander Masters", "cmm", "410", "1.00", "3.00", "0.50", 10, "1.20", 5),
    ("Lightning Bolt", "Magic 2011", "m11", "149", "1.50", "4.00", "0.90", 4, "2.00", 4),
    ("Counterspell", "Modern Horizons 2", "mh2", "267", "2.00", "5.00", "1.55", 3, None, 0),
    ("Rhystic Study", "Prophecy", "pcy", "45", "32.00", None, "20.00", 2, None, 0),
    ("Sheoldred, the Apocalypse", "Dominaria United", "dmu", "107", "28.00", "30.00", "17.00", 4, "18.00", 2),
    ("Arcane Signet", "Commander Legends", "cmr", "297", "1.00", None, "0.55", 2, None, 0),
    ("Mystic Remora", "Ice Age", "ice", "84", "5.50", None, "0.00", 0, None, 0),
    ("Giant Growth", "Magic 2011", "m11", "175", "0.10", None, "0.05", 0, None, 0),
    ("Wastes", "Modern Horizons 3", "mh3", "310", "0.03", None, "0.01", 50, None, 0),
    ("Island", "Bloomburrow", "blb", "277", "0.03", None, "0.01", 12, None, 0),
    ("Jötun Grunt", "Alliances", "all", "1", "0.20", None, "0.10", 6, None, 0),
    ("Cultivate", "Commander 2021", "c21", "246", "0.35", None, "0.15", 8, None, 0),
    ("Shock", "Dominaria Remastered", "dmr", "128", "0.10", None, "0.08", 4, None, 0),
    ("Evolving Wilds", "Bloomburrow", "blb", "254", "0.06", None, "0.04", 20, None, 0),
    ("Ertai, Wizard Adept", "Urza's Legacy", "ulg", "37", "3.00", None, "1.20", 2, None, 0),
    ("Assault // Battery", "Guilds of Ravnica", "grn", "1", "0.40", None, "0.30", 5, None, 0),
    # no scan price: stays without a market price
    ("Mystery Pilgrim", "Eternal Masters", "ema", "99", None, None, "0.40", 3, None, 0),
]
KEYS = {c[0]: c for c in CARDS}

# printings that share a name and an edition and differ by variation
FORESTS = [("0306", "0.01", "0.05"), ("0307", "0.01", "0.05"), ("0576 - Surge Foil", "0.50", "2.00")]


def printing_id(name, extra=""):
    """One Scryfall id per printing: its foil and non-foil copies share it."""
    return sid("%s|%s" % (name, extra))


def ck_raw(name, edition, cash, wants, foil=False, variation="", scry=None, url=None, sku=None):
    scry = scry or printing_id(name, variation)
    slug = edition.lower().replace(" ", "-").replace("'", "")
    return {
        "sku": sku or "SKU-%s" % scry[:8], "scryfall_id": scry, "name": name, "edition": edition,
        "variation": variation, "is_foil": "true" if foil else "false", "price_buy": cash,
        "qty_buying": wants, "price_retail": "9.99",
        "url": url or "mtg/%s/%s%s" % (slug, name.lower().replace(" ", "-").replace(",", ""),
                                        "-foil" if foil else ""),
    }


def ck_rows():
    rows = []
    for name, ed, _c, _n, _m, _mf, cash, wants, fcash, fwants in CARDS:
        rows.append(ck_raw(name, ed, cash, wants))
        if fcash:
            rows.append(ck_raw(name, ed, fcash, fwants, foil=True))
    for var, cash, _m in FORESTS:
        foil = "Foil" in var
        rows.append(ck_raw("Forest", "Final Fantasy", cash, 100, foil=foil, variation=var,
                           url="mtg/final-fantasy/forest-%s" % var.lower().replace(" - ", "-").replace(" ", "-")))
    # etched, and a word that only contains "etched"
    rows.append(ck_raw("Brainstorm", "Mystical Archive", "3.00", 4, foil=True, variation="Etched Foil"))
    rows.append(ck_raw("Sketch Card", "Sketched Showcase", "0.20", 4, foil=True))
    rows.append(ck_raw("Brainstorm", "Mystical Archive", "0.50", 4, foil=False))
    # a row with no Scryfall id is dropped
    rows.append({"sku": "X", "scryfall_id": None, "name": "Token", "edition": "Tokens", "variation": "",
                 "is_foil": "false", "price_buy": "0.10", "qty_buying": 3, "url": "mtg/tokens/token"})
    return rows


def pricelist(rows=None, wrap=True, created="2026-01-02 03:04:05"):
    rows = ck_rows() if rows is None else rows
    return {"meta": {"created_at": created, "base_url": "https://example.invalid/"}, "data": rows} if wrap else rows


def pricelist_bytes(rows=None, gz=False, wrap=True):
    data = json.dumps(pricelist(rows, wrap)).encode("utf-8")
    return gzip.compress(data) if gz else data


class FakeResponse:
    def __init__(self, data, gz=False):
        self._buf = io.BytesIO(data)
        self.headers = {"Content-Encoding": "gzip"} if gz else {}
        self.status = 200

    def read(self, n=-1):
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def scry_card(name, finish_prices=True):
    _n, ed, code, number, usd, usd_foil, *_ = KEYS[name]
    return {"object": "card", "id": printing_id(name), "name": name, "set": code, "set_name": ed,
            "collector_number": number,
            "prices": {"usd": usd, "usd_foil": usd_foil, "usd_etched": None, "eur": "1.00"}}


def scry_answer(identifiers, shuffle=False, strip_set=False):
    """What Scryfall's collection endpoint would answer for these identifiers."""
    data, nf = [], []
    by_sn = {(c[2], c[3]): c[0] for c in CARDS}
    by_id = {printing_id(c[0]): c[0] for c in CARDS}
    for ident in identifiers:
        name = None
        if "id" in ident:
            name = by_id.get(ident["id"])
        else:
            name = by_sn.get((ident.get("set"), ident.get("collector_number")))
        if name is None:
            nf.append(ident)
            continue
        card = scry_card(name)
        if strip_set:
            card = {k: v for k, v in card.items() if k not in ("set", "collector_number")}
        data.append(card)
    if shuffle:
        data.reverse()
    return {"object": "list", "not_found": nf, "data": data}


class FakeNet:
    """Stands in for the web. Records what was asked and never touches a socket."""

    def __init__(self):
        self.ck_rows = None
        self.ck_calls = 0
        self.ck_error = None
        self.ck_gate = None
        self.scry_calls = []
        self.scry_error_at = None
        self.scry_gate = None
        self.sleeps = []
        self.gz = False

    def ck_opener(self, req, timeout=None):
        self.ck_calls += 1
        self.last_ck_request = req
        if self.ck_gate:
            self.ck_gate()
        if self.ck_error:
            raise self.ck_error
        return FakeResponse(pricelist_bytes(self.ck_rows, gz=self.gz), gz=self.gz)

    def scryfall_post(self, url, payload):
        from netutil import NetError
        n = len(self.scry_calls)
        self.scry_calls.append((url, payload))
        if self.scry_gate:
            self.scry_gate()
        if self.scry_error_at is not None and n >= self.scry_error_at:
            raise NetError("no internet connection, or the site could not be reached")
        return scry_answer(payload["identifiers"])

    def sleep(self, secs):
        self.sleeps.append(secs)


HEADER = ("Card Name,Set,Set Code,Collector Number,Finish,Condition,Language,Quantity,"
          "Purchase Price CAD,List Price CAD,TCGplayer CAD,TCGplayer USD,Store price CAD,Binder")


def shop_line(name, set_name, code, number, finish="Non-Foil", cond="Near Mint", lang="English", qty=1, usd="1.00"):
    def q(s):
        s = str(s)
        return '"%s"' % s.replace('"', '""') if any(c in s for c in ',"\n') else s
    return ",".join([q(name), q(set_name), code, number, finish, cond, lang, str(qty), "0.00", "0.00", "1.00",
                     str(usd), "1.00", "Binder A"])


def shop_csv(lines, header=HEADER):
    return "\r\n".join([header] + list(lines)) + "\r\n"


def sample_shop():
    """A small made-up scan: duplicates, foil, etched, promo numbers, other language, bad rows."""
    return shop_csv([
        shop_line("Sol Ring", "Commander Masters", "CMM", "410", qty=2, usd="1.10"),
        shop_line("Sol Ring", "Commander Masters", "CMM", "410", qty=1, usd="1.10"),
        shop_line("Sol Ring", "Commander Masters", "CMM", "410", finish="Foil", qty=1, usd="3.10"),
        shop_line("Lightning Bolt", "Magic 2011", "M11", "149", qty=3),
        shop_line("Counterspell", "Modern Horizons 2", "MH2", "267", qty=2),
        shop_line("Sheoldred, the Apocalypse", "Dominaria United", "DMU", "107", qty=1),
        shop_line("Wastes", "Modern Horizons 3", "MH3", "310", qty=7),
        shop_line("Island", "Bloomburrow", "BLB", "277", qty=20),
        shop_line("Mystic Remora", "Ice Age", "ICE", "84", qty=1),
        shop_line("Mystery Pilgrim", "Eternal Masters", "EMA", "99", qty=1, usd=""),
        shop_line("Promo Thing", "Promos", "PF18", "2018f", finish="Foil", qty=1),
        shop_line("Etched Thing", "Special", "SPC", "241s", finish="Etched", qty=1),
        shop_line("Blitz de Luz", "Alliances", "ALL", "5", lang="Portuguese", qty=2),
        shop_line("", "Nowhere", "", "", qty=1),
        shop_line("Odd Quantity", "Nowhere", "XYZ", "1", qty="x"),
    ])
