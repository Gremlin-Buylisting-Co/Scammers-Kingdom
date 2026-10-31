"""Run every test: `python tests/run_tests.py`. Standard library only.
Prints a PASS or FAIL line per check and, at the end, "N passed, N failed"."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import H  # noqa: E402
import t_core  # noqa: E402
import t_lookup  # noqa: E402
import t_page  # noqa: E402
import t_prices  # noqa: E402
import t_server  # noqa: E402

ORDER = [
    t_core.import_tests,
    t_core.exportimport_tests,
    t_core.persist_tests,
    t_prices.links_tests,
    t_prices.ckfile_tests,
    t_page.filter_tests,
    t_server.hand_tests,
    t_prices.ck_tests,
    t_prices.market_tests,
    t_prices.back_tests,
    t_prices.plan_tests,
    t_lookup.lookup_tests,
    t_server.server_tests,
    t_page.page_tests,
    t_server.public_tests,
    t_server.docs_tests,
]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    h = H()
    h.guard_network()
    try:
        for fn in ORDER:
            h.run(fn)
        h.section("NETWORK")
        h.eq(h.blocked, [], "nothing tried to reach a host outside 127.0.0.1")
    finally:
        h.cleanup()
    h.summary()
    return 1 if h.failed else 0


if __name__ == "__main__":
    sys.exit(main())
