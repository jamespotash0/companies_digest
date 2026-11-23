#!/usr/bin/env python3
"""
Companies digest — newest postings for your target roles, ranked by how well
they match your resume (primary) and company size (secondary tiebreak).

FREE sources, no paid API:
  - Remotive API (remote tech jobs, includes descriptions)   [default on]
  - Arbeitnow API (open job board)                            [default on]
  - Hacker News "Who is hiring" thread via Algolia            [optional]
  - The Muse (keyless, has locations)                         [default on]
  - Greenhouse / Lever / Ashby / Workday public boards        [watchlist]
  - Workable / SmartRecruiters / Rippling public boards       [watchlist]
Plus, with a free key: Adzuna (aggregator) and X/Grok (founder hiring posts).

The watchlist is fed by getro.py (VC portfolio boards, bulk) and discover.py
(web search, long tail); boards are fetched concurrently because that list runs
to the hundreds.

Runs 3x/day (9am, 1pm, 4pm ET) via GitHub Actions. A seen-cache prevents the
1pm/4pm runs from repeating what the 9am run already sent.

Honest limits: free sources rarely expose company headcount, so "size" ranking
is best-effort via the COMPANY_SIZE lookup you fill in. Qualification match is
the reliable ranker.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from digest.boards import (
    ASHBY_COMPANIES,
    GREENHOUSE_COMPANIES,
    LEVER_COMPANIES,
    WORKDAY_COMPANIES,
    fetch_ashby,
    fetch_greenhouse,
    fetch_lever,
    fetch_rippling,
    fetch_smartrecruiters,
    fetch_workable,
    fetch_workday,
)
from digest.filters import screen_facts, title_is_target
from digest.location import BEST_MATCH_MIN, location_tier, work_mode
from digest.mailer import send_email, subject_for
from digest.render import build_html, discovery_banner
from digest.scoring import score_breakdown
from digest.settings import (
    BOARD_WORKERS,
    COMPANY_SIZE,
    FRESH_ONLY_DAYS,
    GROK_SEPARATE_EMAIL,
    MAX_AGE_DAYS,
    MAX_ITEMS,
    MAX_ITEMS_REST,
    MAX_YEARS,
    PREFER_LARGER,
    REMOTE_OK,
    REQUIRE_NYC,
    RESUME,
    SEEN_FILE,
    SEEN_TTL_DAYS,
    SEND_WHEN_EMPTY,
    SUBJECT_X,
    USE_ADZUNA,
    USE_ARBEITNOW,
    USE_GROK_X,
    USE_HN_WHOISHIRING,
    USE_MUSE,
    USE_REMOTIVE,
    USE_WORKDAY,
)
from digest.sources import (
    fetch_adzuna,
    fetch_arbeitnow,
    fetch_grok_x,
    fetch_hn,
    fetch_muse,
    fetch_remotive,
)
from digest.stage import (
    EXCLUDE_LATE_STAGE,
    _load_funded,
    _load_stage,
    funding_note,
    late_stage_prose,
    stage_note,
    stage_points,
)

def size_score(company):
    n = COMPANY_SIZE.get((company or "").lower())
    if n is None:
        return 0
    import math
    val = math.log10(max(n, 1))          # ~0..4+
    return val if PREFER_LARGER else -val


def job_id(j):
    return hashlib.sha1((j["company"] + "|" + j["title"] + "|" + j["url"]).encode()).hexdigest()[:16]


# --------------------------- seen cache (dedupe across the day's 3 runs)
def load_seen():
    try:
        with open(SEEN_FILE) as f:
            data = json.load(f)
    except Exception:
        data = {}
    cutoff = datetime.now().timestamp() - SEEN_TTL_DAYS * 86400
    return {k: v for k, v in data.items() if v > cutoff}


def save_seen(seen):
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(seen, f)
    except Exception as e:
        print(f"[warn] could not write seen file: {e}")


def main():
    # LOOP: pull in ATS tokens auto-detected by the funding digest
    gh, lv, ash = list(GREENHOUSE_COMPANIES), list(LEVER_COMPANIES), list(ASHBY_COMPANIES)
    wk, sr, rip = [], [], []
    try:
        import watchlist as _wl
        auto = _wl.get_tokens()
        gh  = list(dict.fromkeys(gh  + auto.get("greenhouse", [])))
        lv  = list(dict.fromkeys(lv  + auto.get("lever", [])))
        ash = list(dict.fromkeys(ash + auto.get("ashby", [])))
        # Workable / SmartRecruiters / Rippling have no hand-maintained seed
        # list — every token comes from getro.py reading a real apply URL.
        wk  = list(dict.fromkeys(auto.get("workable", [])))
        sr  = list(dict.fromkeys(auto.get("smartrecruiters", [])))
        rip = list(dict.fromkeys(auto.get("rippling", [])))
        extra = len(gh) - len(GREENHOUSE_COMPANIES) + len(lv) - len(LEVER_COMPANIES) \
                + len(ash) - len(ASHBY_COMPANIES) + len(wk) + len(sr) + len(rip)
        if extra:
            print(f"[loop] +{extra} auto-detected companies from funding digest")
        # Real company sizes harvested from the VC boards, so size_score() ranks
        # on data instead of the mostly-empty hand-filled COMPANY_SIZE dict.
        sizes = _wl.get_sizes()
        if sizes:
            for name, n in sizes.items():
                COMPANY_SIZE.setdefault(name, n)
            print(f"[loop] {len(sizes)} company sizes from the watchlist")
    except Exception as e:
        print(f"[info] watchlist not available: {e}")
    # A fresh checkout has an empty watchlist, so the ATS boards -- the source
    # that finds most postings -- contribute nothing and the run looks like "not
    # much is hiring". getro.py fills it in one pass from public VC job boards.
    if len(gh) + len(lv) + len(ash) + len(wk) + len(sr) + len(rip) < 20:
        print("[setup] the ATS watchlist is nearly empty, so most job boards are "
              "not being read. Run:  python getro.py")
    _load_funded()
    _load_stage()

    raw = []
    # Remotive is remote-only and Arbeitnow is mostly EU/remote — under a strict
    # NYC gate (REQUIRE_NYC on, REMOTE_OK off) they yield ~nothing, so skip the
    # calls entirely instead of fetching hundreds of postings just to discard
    # them. Flip REMOTE_OK=True (or REQUIRE_NYC=False) to use them again.
    remote_useful = REMOTE_OK or not REQUIRE_NYC
    if USE_REMOTIVE and remote_useful:   raw += fetch_remotive()
    elif USE_REMOTIVE:                   print("[info] skipping Remotive (remote-only vs NYC gate)")
    if USE_ARBEITNOW and remote_useful:  raw += fetch_arbeitnow()
    elif USE_ARBEITNOW:                  print("[info] skipping Arbeitnow (remote/EU vs NYC gate)")
    if USE_HN_WHOISHIRING: raw += fetch_hn()
    # Free aggregators that DO expose location, so they survive the NYC/US-remote
    # gate: The Muse (keyless) and Adzuna (free app id/key). These reach roles at
    # companies not on our ATS watchlist — e.g. a LinkedIn-surfaced posting that
    # Google-for-Jobs/Adzuna also indexes.
    if USE_MUSE:   raw += fetch_muse()
    if USE_ADZUNA: raw += fetch_adzuna()
    # X / Grok: founder "we're hiring" posts that never hit a job board.
    if USE_GROK_X: raw += fetch_grok_x()
    # Board fetches run concurrently. Serially, a watchlist in the hundreds —
    # which getro.py produces in a single sweep — would not finish inside the
    # Actions run; these are independent read-only calls to six different hosts,
    # so the only real limit is politeness per host.
    # Workable 429s concurrent callers but takes ~6 req/s from one, so all its
    # boards go to ONE worker, run one at a time, queued first so it overlaps the
    # rest.
    boards = ([(lambda ts: [j for t in ts for j in fetch_workable(t)], wk)]
              + [(fetch_greenhouse, t) for t in gh]
              + [(fetch_lever, t) for t in lv]
              + [(fetch_ashby, t) for t in ash]
              + [(fetch_smartrecruiters, t) for t in sr]
              + [(fetch_rippling, t) for t in rip])
    if USE_WORKDAY:
        boards += [(fetch_workday, e) for e in WORKDAY_COMPANIES]
    if boards:
        t0 = datetime.now(timezone.utc).timestamp()
        with ThreadPoolExecutor(max_workers=BOARD_WORKERS) as ex:
            for lst in ex.map(lambda fa: fa[0](fa[1]), boards):
                raw += lst
        print(f"[info] {len(boards) - 1 + len(wk)} boards fetched in "
              f"{datetime.now(timezone.utc).timestamp() - t0:.1f}s")
    print(f"[info] fetched {len(raw)} raw postings")

    seen = load_seen()
    if not seen:
        # Not an error — but worth saying, because the first digest is much
        # bigger than every later one and that surprises people into thinking
        # the filters are broken.
        print("[setup] no seen-jobs history yet: this first run treats every "
              f"posting as new, so expect a fuller digest than usual "
              f"(capped at {MAX_ITEMS}).")
    now_ts = datetime.now(timezone.utc).timestamp()
    age_cutoff = (now_ts - MAX_AGE_DAYS * 86400) if MAX_AGE_DAYS else 0
    picked, ids_this_run, seen_titles = [], set(), set()
    dropped_late = 0
    dropped_years = 0
    dropped_stale = 0

    for j in raw:
        if not j.get("title") or not j.get("url"):
            continue
        # HN comments have freeform titles; only title-filter the structured sources
        if j["source"] != "HN Who's Hiring" and not title_is_target(j["title"]):
            continue
        if j["source"] == "HN Who's Hiring" and not any(
            x in j["content"].lower() for x in RESUME["target_titles"]):
            continue
        # Recency gate: drop postings older than MAX_AGE_DAYS when the source
        # gives a date. Unknown-date sources fall through (kept, no age shown).
        posted = j.get("posted_ts")
        if age_cutoff and posted and posted < age_cutoff:
            continue
        # A board only just added to the watchlist arrives with its entire back
        # catalogue, all of it unseen and therefore "new". Without this, adding
        # one company floods a digest with roles posted weeks ago — which is
        # what makes the email look repetitive and stale.
        if FRESH_ONLY_DAYS and posted and posted < now_ts - FRESH_ONLY_DAYS * 86400:
            dropped_stale += 1
            continue
        jid = job_id(j)
        if jid in seen or jid in ids_this_run:
            continue
        # The same role surfaced by two boards — or posted by one company to
        # several cities — is collapsed to one entry keyed on company + title.
        # That happens AFTER scoring and sorting, not here: a multi-city posting
        # like "Oscar / Product Manager, Network" lands as NYC, SF and on-site
        # variants, and dropping on first sight kept whichever the board
        # happened to return first. Now the NYC one wins because it scores highest.
        j["_tkey"] = (j.get("company", "").lower().strip(), j["title"].lower().strip())
        tier = location_tier(j.get("location", ""), j.get("content", ""))
        if REQUIRE_NYC and not tier:
            continue
        # Experience-bar filter. Applied before scoring because it is a
        # preference, not a judgement: a stated bar well above yours means the
        # role is not for you, however well the keywords line up. An UNSTATED
        # bar always passes — silence is not a ten-year requirement.
        facts = screen_facts(j.get("content", ""))
        want_years = facts.get("years_min")
        if MAX_YEARS and want_years is not None and want_years > MAX_YEARS:
            dropped_years += 1
            continue

        mode = work_mode(j.get("location", ""), j.get("content", ""))
        bd = score_breakdown(j["title"], j["content"], tier, mode)
        if bd["total"] <= 0:
            continue

        # Stage is a FILTER, not a discount. What the company IS beats what the
        # JD says — a known round is a fact, the prose tells were only ever a
        # fallback. A clearly late-stage company is dropped outright rather than
        # ranked down, because "seed to Series B" is a preference, not a
        # tiebreak: no keyword score should be able to argue you into a public
        # company.
        stage = stage_note(j.get("company", ""))
        if EXCLUDE_LATE_STAGE:
            pts = stage_points(stage.get("round")) if stage else None
            late_by_round = pts is not None and pts < 0
            late_by_prose = pts is None and bool(late_stage_prose(j["content"]))
            if late_by_round or late_by_prose:
                dropped_late += 1
                continue

        j["loc_tier"] = tier
        j["work_mode"] = mode
        j["funding"] = funding_note(j.get("company", ""))
        j["stage"] = stage
        j["breakdown"] = bd
        j["facts"] = facts
        j["q"] = bd["total"]
        j["_id"] = jid
        picked.append(j)
        ids_this_run.add(jid)

    # Rank by resume match first, then freshest, then company-size preference.
    picked.sort(key=lambda x: (x["q"], x.get("posted_ts") or 0, size_score(x["company"])),
                reverse=True)
    # Now collapse duplicates, keeping the best-scoring variant of each role —
    # which, given the location bonus, is the NYC posting whenever there is one.
    deduped = []
    for j in picked:
        if j["_tkey"] in seen_titles:
            continue
        seen_titles.add(j["_tkey"])
        deduped.append(j)
    if len(deduped) < len(picked):
        print(f"[info] collapsed {len(picked) - len(deduped)} duplicate postings "
              f"(same company + title, kept the best-ranked location)")
    # The two sections are capped SEPARATELY. One combined cap made the
    # "Worth a look" section unreachable: there are consistently more than
    # MAX_ITEMS postings scoring above BEST_MATCH_MIN, so a single [:MAX_ITEMS]
    # slice never reached the sub-threshold ones and they were dropped rather
    # than relegated. Capping each section means "below BEST_MATCH_MIN goes in
    # the quieter area" is actually true, and the quiet area stays bounded.
    best = [j for j in deduped if j["q"] >= BEST_MATCH_MIN][:MAX_ITEMS]
    rest = [j for j in deduped if j["q"] < BEST_MATCH_MIN][:MAX_ITEMS_REST]
    print(f"[info] {len(best)} at/above {BEST_MATCH_MIN} (cap {MAX_ITEMS}), "
          f"{len(rest)} below (cap {MAX_ITEMS_REST}), "
          f"from a pool of {len(deduped)}")
    picked = best + rest

    for j in picked:
        seen[j["_id"]] = now_ts
    save_seen(seen)

    if dropped_stale:
        print(f"[filter] dropped {dropped_stale} posting(s) older than "
              f"{FRESH_ONLY_DAYS} days (JOB_FRESH_ONLY_DAYS=0 to keep them)")
    if dropped_years:
        print(f"[filter] dropped {dropped_years} posting(s) asking more than "
              f"{MAX_YEARS} years (JOB_MAX_YEARS=0 to keep them)")
    if dropped_late:
        print(f"[filter] dropped {dropped_late} posting(s) at late-stage/public "
              f"companies (JOB_EXCLUDE_LATE_STAGE=0 to keep them)")
    # X/Grok leads are a different kind of thing from a board posting — a person
    # said they are hiring, often with no application link, and acting on one
    # means replying to a human. Mixed in they read as low-confidence noise.
    # X/Grok items are LEADS, not postings. A founder saying "we're hiring" often
    # never says for what, never states a years bar, and carries 200 characters
    # of summary rather than an 8,000-character JD — so the fit score has almost
    # nothing to work with and lands every one of them in the same narrow band.
    # Scoring them anyway invented precision that was not there. They are sorted
    # by recency and shown without a fit verdict; the summary does the work.
    grok = [j for j in picked if j.get("source") == "X/Grok"]
    for j in grok:
        j["lead"] = True
    grok.sort(key=lambda x: x.get("posted_ts") or 0, reverse=True)
    if GROK_SEPARATE_EMAIL and grok:
        picked = [j for j in picked if j.get("source") != "X/Grok"]
    else:
        grok = []

    print(f"[info] {len(picked)} new matching postings this run"
          + (f" (+{len(grok)} X/Grok leads in their own email)" if grok else ""))
    if grok:
        send_email(build_html(grok, title="Hiring posts from X"),
                   subject=subject_for(SUBJECT_X), thread="x")
    if picked or SEND_WHEN_EMPTY:
        send_email(build_html(picked, banner=discovery_banner()))
    elif grok:
        print("[info] only X/Grok leads this run — nothing for the main digest")
    else:
        print("[info] nothing new this run — skipping email (set SEND_WHEN_EMPTY=1 to override)")


if __name__ == "__main__":
    main()
