#!/usr/bin/env python3
"""detect_and_add on a company whose board is already on the watchlist.

_probe_company skips tokens we already hold, so a company Getro had added read
as "no board": it sat in `pending`, re-probed daily for nothing, and its funding
row carried no tokens, so its postings never got the "just raised" flag.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("JOB_IGNORE_PROFILE", "1")
sys.path.insert(0, os.path.dirname(HERE))

import watchlist as W                 # noqa: E402
from harness import case, equal, main  # noqa: E402


@case
def test_company_already_on_watchlist_is_resolved_not_pending():
    # Getro already added greenhouse:homelight. Discovery then finds HomeLight
    # raising: it must not be marked board-less, and its funding row must carry
    # the token so the digest can flag its postings.
    d = {p: [] for p in W.PROVIDERS}
    d.update(greenhouse=["homelight"], checked=[], pending={}, funded={}, stage={})
    load, save, probe = W._load, W._save, W._probe_company
    W._load, W._save = (lambda: d), (lambda _d: None)
    W._probe_company = lambda *a, **k: (_ for _ in ()).throw(AssertionError("probed"))
    try:
        W.detect_and_add("HomeLight", "homelight.com", funding={"round": "Series A"})
    finally:
        W._load, W._save, W._probe_company = load, save, probe
    equal(d["pending"], {}, "pending")
    equal(d["funded"]["homelight"].get("tokens"), ["homelight"], "funded tokens")


if __name__ == "__main__":
    main("watchlist")
