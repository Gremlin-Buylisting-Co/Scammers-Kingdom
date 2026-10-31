"""Market prices from Scryfall (TCGplayer's market price), for the printings in the binder."""
import os
import time

import netutil
import storage
from ck_prices import now_iso, to_cents

SCRYFALL_URL = "https://api.scryfall.com/cards/collection"
BATCH = 75          # identifiers per request (Scryfall's limit)
PAUSE = 0.1         # seconds between requests: at most 10 a second
FILE = "market.json"
FINISH_FIELD = {"nonfoil": "usd", "foil": "usd_foil", "etched": "usd_etched"}


def sn_key(row):
    code, number = row.get("set_code") or "", row.get("number") or ""
    if code and number:
        return "sn:%s|%s" % (code.lower(), number.lower())
    return None


def row_key(row):
    """The cache key for a binder row: set + number if it has them, else its Scryfall id."""
    k = sn_key(row)
    if k:
        return k
    if row.get("sid"):
        return "id:" + row["sid"]
    return None


def identifiers(rows):
    """{cache key: Scryfall identifier} for every distinct printing in `rows`."""
    out = {}
    for r in rows:
        k = row_key(r)
        if not k or k in out:
            continue
        if k.startswith("sn:"):
            code, number = k[3:].split("|", 1)
            out[k] = {"set": code, "collector_number": number}
        else:
            out[k] = {"id": k[3:]}
    return out


def _entry(card):
    prices = card.get("prices") or {}
    return {
        "id": str(card.get("id") or "").lower(),
        "name": card.get("name") or "",
        "set_name": card.get("set_name") or "",
        "set": str(card.get("set") or "").lower(),
        "number": str(card.get("collector_number") or "").lower(),
        "usd": to_cents(prices.get("usd")) if prices.get("usd") else None,
        "usd_foil": to_cents(prices.get("usd_foil")) if prices.get("usd_foil") else None,
        "usd_etched": to_cents(prices.get("usd_etched")) if prices.get("usd_etched") else None,
    }


def _ident_key(ident):
    if "id" in ident:
        return "id:" + str(ident["id"]).lower()
    return "sn:%s|%s" % (str(ident.get("set", "")).lower(), str(ident.get("collector_number", "")).lower())


def fetch(idents, post=None, sleep=time.sleep, progress=None):
    """Ask Scryfall for the given identifiers, 75 at a time, at most 10 requests a second.

    Returns {"entries": {key: entry}, "missing": [keys], "error": text or None,
    "batches": n, "total": n}. On a failed request it stops and keeps what it has."""
    post = post or netutil.post_json
    keys = list(idents)
    batches = [keys[i:i + BATCH] for i in range(0, len(keys), BATCH)]
    entries, missing, error, done = {}, [], None, 0
    for n, batch in enumerate(batches):
        if n:
            sleep(PAUSE)
        if progress:
            progress(n + 1, len(batches))
        try:
            reply = _post_with_retry(post, {"identifiers": [idents[k] for k in batch]}, sleep)
        except netutil.NetError as exc:
            error = str(exc)
            break
        cards = reply.get("data") if isinstance(reply, dict) else None
        if not isinstance(cards, list):
            error = "the answer was not in the format expected"
            break
        wanted = set(batch)
        found = set()
        for i, card in enumerate(cards):
            if not isinstance(card, dict):
                continue
            e = _entry(card)
            k = "id:" + e["id"] if "id:" + e["id"] in wanted else \
                "sn:%s|%s" % (e["set"], e["number"])
            if k not in wanted:
                # last resort: the answer lists cards in the order they were asked for
                if len(cards) == len(batch) and not reply.get("not_found"):
                    k = batch[i]
                else:
                    continue
            entries[k] = e
            found.add(k)
        for ident in (reply.get("not_found") or []):
            if isinstance(ident, dict):
                k = _ident_key(ident)
                if k in wanted and k not in found:
                    missing.append(k)
                    found.add(k)
        missing.extend(k for k in batch if k not in found)
        done += 1
    return {"entries": entries, "missing": missing, "error": error, "batches": done, "total": len(batches)}


def _post_with_retry(post, payload, sleep):
    try:
        return post(SCRYFALL_URL, payload)
    except netutil.NetError as exc:
        if "429" in str(exc):          # asked too fast: wait a little, try once more
            sleep(2)
            return post(SCRYFALL_URL, payload)
        raise


class Cache:
    """What market.json holds, with quick lookups."""

    def __init__(self, data=None):
        data = data if isinstance(data, dict) else {}
        self.as_of = data.get("as_of")
        self.entries = dict(data.get("entries") or {})
        self.missing = set(data.get("missing") or [])
        self._by_id = None

    @property
    def by_id(self):
        if self._by_id is None:
            self._by_id = {e["id"]: e for e in self.entries.values() if e.get("id")}
        return self._by_id

    def entry_for(self, row):
        k = sn_key(row)
        e = self.entries.get(k) if k else None
        if e is None and row.get("sid"):
            e = self.entries.get("id:" + row["sid"]) or self.by_id.get(row["sid"])
        return e

    def is_unknown(self, row):
        k = row_key(row)
        return bool(k) and k in self.missing and self.entry_for(row) is None

    def merge(self, result, wanted_keys, touch=True):
        """Fold a fetch result in. Only printings still in the binder are kept.
        `touch=False` leaves the date of the last full update alone (a small lookup for new cards)."""
        entries = {k: v for k, v in self.entries.items() if k in wanted_keys}
        entries.update(result["entries"])
        missing = {k for k in self.missing if k in wanted_keys and k not in result["entries"]}
        missing.update(k for k in result["missing"] if k in wanted_keys)
        self.entries, self.missing = entries, missing
        self._by_id = None
        if (touch and not result["error"]) or not self.as_of:
            self.as_of = now_iso()

    def to_json(self):
        return {"as_of": self.as_of, "entries": self.entries, "missing": sorted(self.missing)}


def price_for(entry, finish):
    if not entry:
        return None
    return entry.get(FINISH_FIELD.get(finish, "usd"))


def load(prices_dir):
    return Cache(storage.read_json(os.path.join(prices_dir, FILE), None))


def save(prices_dir, cache):
    storage.write_json(os.path.join(prices_dir, FILE), cache.to_json())
