"""Card Kingdom's buylist: download, slim copy, search, and buylist links.

The slim copy keeps one entry per printing and finish:
    "<scryfall id>|<finish>": {sku, cash, wants, url, name, edition, variation}
`cash` is what CK pays for a Near Mint copy, in whole cents.
"""
import codecs
import json
import os
import re
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import quote

import netutil
import storage
from appinfo import USER_AGENT

CK_URL = "https://api.cardkingdom.com/api/v2/pricelist"
BUYLIST_PAGE = "https://www.cardkingdom.com/purchasing/mtg_singles?"
LATEST = "ck_latest.json"
PREVIOUS = "ck_previous.json"
_ETCHED = re.compile(r"\betched\b", re.I)


class PricelistError(Exception):
    pass


# ---------- reading CK's list ----------

def to_cents(value):
    try:
        d = Decimal(str(value).strip().replace("$", "").replace(",", ""))
    except (InvalidOperation, ValueError):
        return 0
    if not d.is_finite() or d < 0:
        return 0
    return int((d * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_int(value):
    try:
        return max(0, int(float(str(value).strip())))
    except (ValueError, TypeError, OverflowError):
        return 0


def finish_of(raw):
    """etched when the whole word 'etched' is in the variation or edition
    (case ignored, so 'Sketched' does not count), else foil, else nonfoil."""
    if _ETCHED.search(str(raw.get("variation") or "")) or _ETCHED.search(str(raw.get("edition") or "")):
        return "etched"
    if str(raw.get("is_foil")).strip().lower() == "true":
        return "foil"
    return "nonfoil"


def is_buying(entry):
    return bool(entry) and entry.get("cash", 0) > 0 and entry.get("wants", 0) > 0


def slim_row(raw):
    if not isinstance(raw, dict):
        return None
    sid = str(raw.get("scryfall_id") or "").strip().lower()
    if not sid:
        return None
    key = "%s|%s" % (sid, finish_of(raw))
    return key, {
        "sku": str(raw.get("sku") or ""),
        "cash": to_cents(raw.get("price_buy")),
        "wants": to_int(raw.get("qty_buying")),
        "url": str(raw.get("url") or ""),
        "name": str(raw.get("name") or ""),
        "edition": str(raw.get("edition") or ""),
        "variation": str(raw.get("variation") or ""),
    }


def _better(new, old):
    """Which of two entries for the same printing and finish to keep."""
    if is_buying(new) != is_buying(old):
        return is_buying(new)
    return new["cash"] > old["cash"]


def build_slim(rows, as_of):
    out = {}
    for raw in rows:
        got = slim_row(raw)
        if not got:
            continue
        key, entry = got
        if key not in out or _better(entry, out[key]):
            out[key] = entry
    return {"as_of": as_of, "rows": out}


class _Reader:
    """Reads JSON values one at a time from a byte stream, so the whole
    70 MB list never has to sit in memory as text."""

    def __init__(self, stream, chunk):
        self.stream = stream
        self.chunk = chunk
        self.dec = json.JSONDecoder()
        self.utf = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.buf = ""
        self.pos = 0
        self.eof = False

    def fill(self):
        if self.eof:
            return False
        data = self.stream.read(self.chunk)
        if not data:
            self.eof = True
            self.buf = self.buf[self.pos:] + self.utf.decode(b"", final=True)
            self.pos = 0
            return False
        self.buf = self.buf[self.pos:] + self.utf.decode(data)
        self.pos = 0
        return True

    def peek(self):
        while True:
            while self.pos < len(self.buf) and self.buf[self.pos] in " \t\r\n":
                self.pos += 1
            if self.pos < len(self.buf):
                return self.buf[self.pos]
            if not self.fill():
                return ""

    def value(self):
        while True:
            self.peek()
            try:
                val, end = self.dec.raw_decode(self.buf, self.pos)
            except json.JSONDecodeError:
                if not self.fill():
                    raise
                continue
            if end == len(self.buf) and not self.eof and self.fill():
                continue
            self.pos = end
            return val

    def array_items(self):
        self.pos += 1  # the opening bracket
        while True:
            c = self.peek()
            if c == "]" or c == "":
                self.pos += 1
                return
            if c == ",":
                self.pos += 1
                continue
            item = self.value()
            if isinstance(item, dict):
                yield item


def iter_rows(stream, chunk=1 << 16):
    """Yield the price rows from CK's file: a top-level list, or an object whose
    first list holds the rows."""
    r = _Reader(stream, chunk)
    c = r.peek()
    if c == "[":
        yield from r.array_items()
        return
    if c != "{":
        raise PricelistError("Card Kingdom's answer was not a price list.")
    r.pos += 1
    while True:
        c = r.peek()
        if c in ("}", ""):
            return
        if c == ",":
            r.pos += 1
            continue
        r.value()  # the key
        if r.peek() != ":":
            raise PricelistError("Card Kingdom's answer was not a price list.")
        r.pos += 1
        if r.peek() == "[":
            yield from r.array_items()
            return
        r.value()


class _Counting:
    def __init__(self, stream, progress):
        self.stream, self.progress, self.n = stream, progress, 0

    def read(self, size=-1):
        data = self.stream.read(size)
        self.n += len(data)
        if self.progress:
            self.progress(self.n)
        return data


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def download(opener=None, progress=None, as_of=None):
    """One request for CK's whole list. Returns the slim copy."""
    req = urllib.request.Request(CK_URL, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    })
    resp = netutil.open_url(req, timeout=60, opener=opener)
    try:
        with resp:
            stream = _Counting(netutil.decoded(resp), progress)
            slim = build_slim(iter_rows(stream), as_of or now_iso())
    except (PricelistError, netutil.NetError):
        raise
    except json.JSONDecodeError as exc:
        raise PricelistError("Card Kingdom's answer could not be read.") from exc
    except Exception as exc:
        raise netutil.NetError(netutil.explain(exc)) from exc
    if not slim["rows"]:
        raise PricelistError("Card Kingdom's answer had no cards in it.")
    return slim


# ---------- keeping the files ----------

def load(prices_dir, name=LATEST):
    data = storage.read_json(os.path.join(prices_dir, name), None)
    if isinstance(data, dict) and isinstance(data.get("rows"), dict):
        return data
    return None


def save_latest(prices_dir, slim, replace=os.replace, sleep=time.sleep):
    """Write the new list. The old one becomes ck_previous.json first."""
    os.makedirs(prices_dir, exist_ok=True)
    latest = os.path.join(prices_dir, LATEST)
    prev = os.path.join(prices_dir, PREVIOUS)
    new = latest + ".new"
    storage.write_json(new, slim, replace=replace, sleep=sleep)
    moved = False
    try:
        if os.path.exists(latest):
            storage.replace_retry(latest, prev, replace=replace, sleep=sleep)
            moved = True
        storage.replace_retry(new, latest, replace=replace, sleep=sleep)
    except OSError:
        if moved and not os.path.exists(latest):
            try:
                os.replace(prev, latest)
            except OSError:
                pass
        try:
            os.remove(new)
        except OSError:
            pass
        raise


# ---------- searching the list ----------

def norm(text):
    """Lower case, no accents, no punctuation, single spaces."""
    t = unicodedata.normalize("NFD", str(text))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").lower()
    t = re.sub(r"[^\w\s]", "", t)
    return " ".join(t.split())


def build_index(rows):
    idx = [(norm(e["name"]), e["edition"].lower(), e["variation"], k) for k, e in rows.items()]
    idx.sort()
    return idx


def search(rows, index, q=None, name=None, limit=40):
    """Printings matching `q` (whole-name matches first, then names that start
    with it, then names that contain it), or every printing of one card `name`
    (a double-faced card matches on its front name). Returns (key, entry) pairs."""
    if name is not None:
        want = norm(name.split(" // ")[0])
        out = []
        for nn, _ed, _var, key in index:
            if nn == want or (nn.startswith(want) and
                              norm(rows[key]["name"].split(" // ")[0]) == want):
                out.append((key, rows[key]))
                if len(out) >= limit:
                    break
        return out
    want = norm(q or "")
    if len(want) < 2:
        return []
    exact, starts, inside = [], [], []
    for nn, _ed, _var, key in index:
        if nn == want:
            if len(exact) < limit:
                exact.append(key)
        elif nn.startswith(want):
            if len(starts) < limit:
                starts.append(key)
        elif want in nn:
            if len(inside) < limit:
                inside.append(key)
    return [(k, rows[k]) for k in (exact + starts + inside)[:limit]]


# ---------- buylist links ----------

def edition_slug(url):
    """'mtg/final-fantasy/forest-0576-surge-foil-foil' -> 'final-fantasy'."""
    parts = [p for p in str(url or "").split("?")[0].split("/") if p]
    for i, p in enumerate(parts):
        if p == "mtg" and i + 1 < len(parts):
            return parts[i + 1]
    return ""


def buylist_url(name, finish, ck_url=""):
    """Card Kingdom's buylist search for one card, to open in the person's own browser."""
    params = [
        ("filter[sort]", "price_desc"),
        ("filter[search]", "mtg_advanced"),
        ("filter[name]", str(name).split(" // ")[0]),
    ]
    slug = edition_slug(ck_url)
    if slug:
        params.append(("filter[edition]", slug))
    if finish in ("foil", "etched"):
        params.append(("filter[foils]", "1"))
    else:
        params.append(("filter[singles]", "1"))
    return BUYLIST_PAGE + "&".join("%s=%s" % (quote(k, safe=""), quote(v, safe="")) for k, v in params)
