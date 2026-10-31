"""Breaks key rules in the code one at a time and checks that the tests notice each one.

    python tests/mutate.py [--scratch FOLDER]

A copy of the project is made in the scratch folder; each change is applied to that copy only,
the test suite is run, and the change is undone. Every change must make the suite fail.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (file, exact text in the file, replacement, what rule it breaks)
MUTATIONS = [
    ("plan.py", 'left = g[0]["ck"]["wants"]', "left = 10 ** 9", "CK takes more copies than it wants"),
    ("plan.py", 'return (e["pct"] is None, -(e["pct"] or 0), -e["cash"], r["name"].lower(),', 'return (e["pct"] is None, -(e["pct"] or 0), 0, r["name"].lower(),', "ties no longer prefer the higher CK price"),
    ("plan.py", "get = max(0, value - fee)", "get = value", "the bag fee is not taken off"),
    ("plan.py", '(100 + S["credit_bonus"])', "100", "credit is not cash plus the bonus"),
    ("plan.py", '"FALSE" if l["row"]["finish"] == "nonfoil" else "TRUE"', '"TRUE" if l["row"]["finish"] == "nonfoil" else "FALSE"', "foil flag reversed in the CK file"),
    ("plan.py", 'CK_IMPORT_VARIANT_STYLE = "paren"', 'CK_IMPORT_VARIANT_STYLE = "plain"', "variants written without their variation"),
    ("plan.py", 'S["ex_share"] if cond == "EX"', 'S["vg_share"] if cond == "EX"', "EX paid at the VG share"),
    ("plan.py", "return max(1, (nm * share_for", "return max(0, (nm * share_for", "a copy can pay less than a cent"),
    ("plan.py", "k = min(size - fill, c)", "k = c", "bags are not cut at the bag size"),
    ("plan.py", '"missing": max(0, size - cards)', '"missing": 0', "a partly filled bag does not say how many would fill it"),
    ("ck_prices.py", 'r"\\betched\\b"', 'r"etched"', "'Sketched' counted as etched"),
    ("ck_prices.py", 'entry.get("wants", 0) > 0', 'entry.get("wants", 0) >= 0', "CK counted as buying with nothing wanted"),
    ("ck_prices.py", 'params.append(("filter[singles]", "1"))', 'params.append(("filter[foils]", "1"))', "non-foil link asks for foils"),
    ("ck_prices.py", "(exact + starts + inside)[:limit]", "(inside + starts + exact)[:limit]", "search order reversed"),
    ("ck_prices.py", '("filter[name]", str(name).split(" // ")[0])', '("filter[name]", str(name))', "double-faced name not cut at ' // '"),
    ("ck_prices.py", "            storage.replace_retry(latest, prev, replace=replace, sleep=sleep)\n", "            pass\n", "the old list is not kept as ck_previous.json"),
    ("ck_prices.py", "rounding=ROUND_HALF_UP", "rounding=ROUND_DOWN", "prices rounded down"),
    ("market.py", "BATCH = 75", "BATCH = 100", "requests of more than 75"),
    ("market.py", "PAUSE = 0.1", "PAUSE = 0.01", "requests faster than 10 a second"),
    ("market.py", '"etched": "usd_etched"}', '"etched": "usd_foil"}', "etched priced as foil"),
    ("collection.py", 'self.rows = [r for r in self.rows if r["src"] != "import"]', "self.rows = []", "Replace removes hand-added cards"),
    ("collection.py", 'return "\\ufeff" + buf.getvalue()', "return buf.getvalue()", "export has no byte order mark"),
    ("collection.py", "HISTORY = 20", "HISTORY = 5", "undo keeps fewer than 20 changes"),
    ("collection.py", "q < 0 or q > MAX_QTY", "q > MAX_QTY", "a negative quantity is accepted"),
    ("collection.py", 'raise EditError(404, "That card is no longer in your binder.")', 'raise EditError(400, "That card is no longer in your binder.")', "unknown row answers 400"),
    ("collection.py", 'if lang.lower() not in ("", "english", "en"):', "if False:", "non-English cards not listed apart"),
    ("collection.py", 'hit["qty"] = clamp_qty(hit["qty"] + row["qty"])', 'hit["qty"] = hit["qty"]', "merging rows loses the quantity"),
    ("collection.py", "    if header != EXPORT_HEADER:\n        if all(", "    if False:\n        if all(", "any file accepted by Import My Collection"),
    ("collection.py", '            self.rows = []\n            idx = None', "            idx = None", "Import My Collection Replace keeps the old rows"),
    ("server.py", 'if method == "POST" and headers.get("x-requested-with") != "ScammersKingdom":', "if False:", "POST needs no header from the page"),
    ("server.py", 'def bind_server(app, start=START_PORT, tries=PORT_TRIES, host="127.0.0.1"):', 'def bind_server(app, start=START_PORT, tries=PORT_TRIES, host="127.0.0.2"):', "not bound to 127.0.0.1"),
    ("server.py", '            self.state["last_export"] = self.clock().isoformat(timespec="seconds")\n', "            pass\n", "the last export time is not kept"),
    ("server.py", "if ck_prices.is_buying(after.get(k)) and not ck_prices.is_buying(before.get(k)):", "if ck_prices.is_buying(after.get(k)):", "every bought card counted as back"),
    ("server.py", 'if self.update["running"]:\n                return None', 'if False:\n                return None', "two updates can run at once"),
    ("server.py", "REMIND_DAYS = 14", "REMIND_DAYS = 30", "reminder after 30 days"),
    ("server.py", 'if host not in ("127.0.0.1", "localhost"):', "if False:", "any host name accepted"),
    ("storage.py", "def write_json(path, obj, retries=3,", "def write_json(path, obj, retries=0,", "no retry when a write fails"),
    ("static/index.html", "PAGE=100", "PAGE=50", "50 rows per page"),
    ("static/index.html", "out[r.id]=t;left-=t;", "out[r.id]=r.qty;left-=t;", "the page ignores CK's wanted limit"),
    ("static/index.html", 'normalize("NFD").replace(/[\\u0300-\\u036f]/g,"")', 'normalize("NFC")', "accents not ignored in the filter"),
    ("static/index.html", "return list.filter(function(x){return sel[x.r.id];}).map(function(x){return x.r.id;});", "return Object.keys(sel).map(Number);", "bulk actions reach hidden rows"),
    ("static/index.html", 'return UUID.test(sid)?"https', 'return true?"https', "picture address for any text"),
    ("static/index.html", 'target="_blank" rel="noopener noreferrer">Card Kingdom\'s CSV import page', 'target="_blank">Card Kingdom\'s CSV import page', "CSV import link without rel"),
    ("static/index.html", "var fee=S.bag_fee, g=Math.max(0,value-fee);", "var fee=S.bag_fee, g=value;", "the page forgets the bag fee"),
    ("static/index.html", "rel=\"noopener noreferrer\">Sell on CK</a>'", "\">Sell on CK</a>'", "Sell on CK link without rel"),
    ("static/index.html", "if(!an&&a.pct!==b.pct) return b.pct-a.pct;", "if(!an&&a.pct!==b.pct) return a.pct-b.pct;", "the page ranks worst first"),
    ("static/index.html", "Not affiliated with Card Kingdom.", "", "footer text missing"),
]


def run_suite(workdir, scratch):
    env = dict(os.environ, SK_TEST_TMP=scratch, PYTHONDONTWRITEBYTECODE="1")
    try:
        p = subprocess.run([sys.executable, os.path.join(workdir, "tests", "run_tests.py")], cwd=workdir, env=env,
                           capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return False, "timed out"
    m = re.search(r"(\d+) passed, (\d+) failed", p.stdout)
    if p.returncode != 0 or not m or int(m.group(2)) > 0:
        return False, (m.group(0) if m else "crashed")
    return True, m.group(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", default=None)
    ap.add_argument("--only", default=None, help="comma-separated change numbers to run (default: all)")
    args = ap.parse_args()
    scratch = args.scratch or tempfile.mkdtemp(prefix="sk-mutate-")
    os.makedirs(scratch, exist_ok=True)
    work = os.path.join(scratch, "copy")
    if os.path.exists(work):
        shutil.rmtree(work)
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git", "__pycache__", "ScammersKingdom-data", "build", "dist"))
    ok, info = run_suite(work, scratch)
    print("baseline: %s (%s)" % ("clean" if ok else "FAILING", info))
    if not ok:
        return 2
    missed, total, noticed = [], 0, 0
    only = {int(x) for x in args.only.split(",")} if args.only else None
    for n, (rel, old, new, what) in enumerate(MUTATIONS, 1):
        if only and n not in only:
            continue
        path = os.path.join(work, rel)
        with open(path, encoding="utf-8", newline="") as f:
            original = f.read()
        if original.count(old) != 1:
            print("BAD   %2d %s: text found %d times (%s)" % (n, rel, original.count(old), what))
            missed.append(what)
            continue
        total += 1
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(original.replace(old, new))
            clean, info = run_suite(work, scratch)
        finally:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(original)
        if clean:
            print("MISSED %2d %s: %s" % (n, rel, what))
            missed.append(what)
        else:
            noticed += 1
            print("caught %2d %s: %s (%s)" % (n, rel, what, info))
    print("%d of %d changes noticed" % (noticed, total))
    shutil.rmtree(work, ignore_errors=True)
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
