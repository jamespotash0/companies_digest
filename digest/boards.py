import os
from .filters import title_is_target
from .net import (
    get_json,
    post_json,
    strip_html,
    to_ts,
)
from .settings import JD_CHARS, env_int

# ATS watchlist — add ATS tokens for companies you're targeting (e.g. from the
# funding digest). Find the token in a company's careers URL:
#   boards.greenhouse.io/<TOKEN>   jobs.lever.co/<TOKEN>   jobs.ashbyhq.com/<TOKEN>
# Seeded with SMALL / early-stage (seed–Series B) NYC startups whose live boards
# carry PM / FDE / deployment / AI-strategist / solutions roles in your lane —
# each verified against the ATS's public API, location-checked to NYC, and kept
# small (≤~65 open roles, i.e. not the 400-role giants). The loop auto-adds more
# as companies raise; trim/extend freely.
GREENHOUSE_COMPANIES = [
    "alloy",         # fintech/identity · founding Forward Deployed Engineer
    "vestwell",      # fintech/retirement · Implementations Manager
    "lithic",        # fintech/cards (Privacy.com) · NYC
    "northspyre",    # PROPTECH · NYC (your Stringbean domain)
    "vts",           # PROPTECH · NYC (your domain)
    "honeycomb",     # PROPTECH/insurance · NYC (your domain)
]
LEVER_COMPANIES = []        # e.g. ["somestartup"]
ASHBY_COMPANIES = [
    # NYC-HQ, fitting roles now
    "edra",          # AI · AI Strategist (New York)
    "probook",       # AI · Deployment Strategist, Implementation Manager (Manhattan)
    "credal",        # AI/enterprise · Founding GTM Deployment Strategist (Brooklyn)
    "normalcomputing",  # AI · Forward Deployed Engineer (NYC)
    "valon",         # PROPTECH/mortgage · Deployment Strategist, Implementation (3 NYC roles)
    "rogo",          # fintech/AI · Product Manager | Agents (NYC)
    "kalshi",        # fintech/markets · Product Manager, Growth/Payments (NYC)
    "rho",           # fintech · Product Manager (NYC)
    "highbeam",      # fintech/SMB · NYC
    "eliseai",       # PROPTECH/AI · NYC · Solutions Engineer (you interviewed here)
    # SF/London-HQ but they DO staff NYC — the per-role location gate keeps only
    # their NYC postings, so seeding them just means "watch for NYC roles here".
    "decagon",       # AI agents · 24 NYC roles live
    "merge",         # unified API · 11 NYC roles live
    "distyl",        # AI/enterprise deployment (AI Strategist, AI Operator)
    "context",       # AI · Forward Deployed Engineer, Deployment Strategist
    "meticulous",    # AI/dev tools · Forward Deployed Engineer
    "parafin",       # fintech/embedded · Forward Deployed Engineer
    "method",        # fintech/payments · Solutions Engineer
    "stytch",        # dev tools/auth · Solutions Engineer
    "mangomint",     # AI/vertical SaaS · GTM Associate, AI Product (US-remote)
]

# Workday boards. Unlike the token-based ATSs above, a Workday board needs THREE
# parts you read from the careers URL:
#   https://<tenant>.<wd>.myworkdayjobs.com/<site>
# e.g. https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite
#   ->  {"tenant": "nvidia", "wd": "wd5", "site": "NVIDIAExternalCareerSite"}
# You can also use the shorthand string "tenant/wd/site". Big/late companies
# dominate Workday, so most matches will be filtered by the seniority gate — it's
# most useful for a handful of specific mid/large targets you want to track.
WORKDAY_COMPANIES = [
    # {"tenant": "nvidia", "wd": "wd5", "site": "NVIDIAExternalCareerSite"},
    # "someco/wd1/Careers",
]
WORKDAY_SEARCH = "product"   # server-side search to cut noise before scoring
WORKDAY_MAX_DETAIL = env_int("WORKDAY_MAX_DETAIL", 25)   # job-detail fetches per board


def fetch_greenhouse(token):
    out = []
    try:
        data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
        for j in data.get("jobs", []):
            out.append({
                "title": j.get("title", ""),
                "company": token,
                "location": (j.get("location") or {}).get("name", ""),
                "url": j.get("absolute_url", ""),
                "content": strip_html(j.get("content", ""))[:JD_CHARS],
                "posted_ts": to_ts(j.get("updated_at")),
                "source": "Greenhouse",
            })
    except Exception as e:
        print(f"[warn] greenhouse {token}: {e}")
    return out


def fetch_lever(token):
    out = []
    try:
        data = get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
        for j in data:
            out.append({
                "title": j.get("text", ""),
                "company": token,
                "location": (j.get("categories") or {}).get("location", ""),
                "url": j.get("hostedUrl", ""),
                "content": strip_html(j.get("descriptionPlain", j.get("description", "")))[:JD_CHARS],
                "posted_ts": to_ts(j.get("createdAt")),
                "source": "Lever",
            })
    except Exception as e:
        print(f"[warn] lever {token}: {e}")
    return out


def fetch_ashby(token):
    out = []
    try:
        data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
        for j in data.get("jobs", []):
            out.append({
                "title": j.get("title", ""),
                "company": token,
                "location": j.get("location", ""),
                "url": j.get("jobUrl", ""),
                "content": strip_html(j.get("descriptionPlain", ""))[:JD_CHARS],
                "posted_ts": to_ts(j.get("publishedAt")),
                "source": "Ashby",
            })
    except Exception as e:
        print(f"[warn] ashby {token}: {e}")
    return out


# --- Workable / SmartRecruiters / Rippling -------------------------------
# Three more public, keyless ATS APIs, added because getro.py surfaces tokens
# for them constantly. All three share a shape the first three don't: the board
# LIST call returns no job description, only titles and locations. The digest
# scores on JD text, so each needs a per-job detail call — bounded by
# BOARD_MAX_DETAIL exactly as fetch_workday() bounds its own, since a large
# board would otherwise spend hundreds of requests on roles the title gate is
# about to discard anyway.
BOARD_MAX_DETAIL = int(os.environ.get("BOARD_MAX_DETAIL", "25"))


def _loc_str(d, *keys):
    """Flatten an ATS location object to the 'City, Region, Country' string the
    location gate expects."""
    if not isinstance(d, dict):
        return str(d or "")
    parts = [str(d.get(k)).strip() for k in keys if d.get(k)]
    return ", ".join(p for p in parts if p and p.lower() != "none")


def fetch_workable(token):
    out = []
    try:
        data = post_json(f"https://apply.workable.com/api/v1/accounts/{token}/jobs",
                         {"query": ""})
        jobs = data.get("results") or []
    except Exception as e:
        print(f"[warn] workable {token}: {e}")
        return out
    fetched = 0
    for j in jobs:
        shortcode = j.get("shortcode") or ""
        if not shortcode:
            continue
        loc = _loc_str(j.get("location") or {}, "city", "region", "country")
        if (j.get("remote") or (j.get("workplace") or "").lower() == "remote") \
                and "remote" not in loc.lower():
            loc = ("Remote, " + loc) if loc else "Remote"
        content, posted = "", j.get("published")
        # Only spend a detail call on a title we'd actually keep.
        if fetched < BOARD_MAX_DETAIL and title_is_target(j.get("title", "")):
            try:
                d = get_json(f"https://apply.workable.com/api/v1/accounts/{token}/jobs/{shortcode}")
                content = strip_html(" ".join(
                    x for x in (d.get("description"), d.get("requirements"), d.get("benefits")) if x))
                posted = d.get("published") or posted
                fetched += 1
            except Exception as e:
                print(f"[warn] workable detail {token}/{shortcode}: {e}")
        out.append({
            "title": j.get("title", ""),
            "company": token,
            "location": loc,
            "url": f"https://apply.workable.com/{token}/j/{shortcode}/",
            "content": content[:JD_CHARS],
            "posted_ts": to_ts(posted),
            "source": "Workable",
        })
    return out


def fetch_smartrecruiters(token):
    out = []
    try:
        data = get_json("https://api.smartrecruiters.com/v1/companies/"
                        f"{token}/postings?limit=100")
        jobs = data.get("content") or []
    except Exception as e:
        print(f"[warn] smartrecruiters {token}: {e}")
        return out
    fetched = 0
    for j in jobs:
        jid = j.get("id") or ""
        company = ((j.get("company") or {}).get("name") or token)
        loc = _loc_str(j.get("location") or {}, "city", "region", "country")
        if (j.get("location") or {}).get("remote") and "remote" not in loc.lower():
            loc = ("Remote, " + loc) if loc else "Remote"
        url = f"https://jobs.smartrecruiters.com/{token}/{jid}"
        content = ""
        if jid and fetched < BOARD_MAX_DETAIL and title_is_target(j.get("name", "")):
            try:
                d = get_json("https://api.smartrecruiters.com/v1/companies/"
                             f"{token}/postings/{jid}")
                sections = (d.get("jobAd") or {}).get("sections") or {}
                content = strip_html(" ".join(
                    (sections.get(k) or {}).get("text", "")
                    for k in ("jobDescription", "qualifications",
                              "additionalInformation", "companyDescription")))
                url = d.get("postingUrl") or url
                fetched += 1
            except Exception as e:
                print(f"[warn] smartrecruiters detail {token}/{jid}: {e}")
        out.append({
            "title": j.get("name", ""),
            "company": company,
            "location": loc,
            "url": url,
            "content": content[:JD_CHARS],
            "posted_ts": to_ts(j.get("releasedDate")),
            "source": "SmartRecruiters",
        })
    return out


def fetch_rippling(token):
    out = []
    try:
        jobs = get_json(f"https://api.rippling.com/platform/api/ats/v1/board/{token}/jobs")
        if not isinstance(jobs, list):
            return out
    except Exception as e:
        print(f"[warn] rippling {token}: {e}")
        return out
    fetched = 0
    for j in jobs:
        uuid = j.get("uuid") or ""
        loc = (j.get("workLocation") or {}).get("label", "") if isinstance(j.get("workLocation"), dict) else ""
        content, posted, company = "", None, token
        # Rippling's list call carries no date and no JD at all, so an
        # undetailed posting can't clear the recency gate or score. Detail is
        # where everything useful lives.
        if uuid and fetched < BOARD_MAX_DETAIL and title_is_target(j.get("name", "")):
            try:
                d = get_json("https://api.rippling.com/platform/api/ats/v1/board/"
                             f"{token}/jobs/{uuid}")
                desc = d.get("description") or {}
                if isinstance(desc, dict):
                    content = strip_html(" ".join(str(v) for v in desc.values() if v))
                else:
                    content = strip_html(str(desc))
                posted = d.get("createdOn")
                company = d.get("companyName") or token
                if not loc:
                    wls = d.get("workLocations") or []
                    loc = ", ".join(w.get("label", "") for w in wls if isinstance(w, dict))
                fetched += 1
            except Exception as e:
                print(f"[warn] rippling detail {token}/{uuid}: {e}")
        out.append({
            "title": j.get("name", ""),
            "company": company,
            "location": loc,
            "url": j.get("url") or f"https://ats.rippling.com/{token}/jobs/{uuid}",
            "content": content[:JD_CHARS],
            "posted_ts": to_ts(posted),
            "source": "Rippling",
        })
    return out


def _wd_cfg(entry):
    """Accept either a dict or the shorthand string 'tenant/wd/site'."""
    if isinstance(entry, str):
        parts = [p for p in entry.strip().strip("/").split("/") if p]
        if len(parts) == 3:
            return {"tenant": parts[0], "wd": parts[1], "site": parts[2]}
        return {}
    return entry or {}


def fetch_workday(entry):
    """Pull a Workday board via its public cxs JSON API.

    The list endpoint gives title + location but no description, so we fetch the
    per-job detail ONLY for postings whose title already passes the target-title
    filter — bounding detail calls (Workday boards are large and senior-heavy)."""
    out = []
    cfg = _wd_cfg(entry)
    tenant, wd, site = cfg.get("tenant"), cfg.get("wd"), cfg.get("site")
    if not (tenant and wd and site):
        print(f"[warn] workday: incomplete config {entry!r}")
        return out
    base = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
    search = cfg.get("search", WORKDAY_SEARCH)
    try:
        listings, offset = [], 0
        for _ in range(10):                       # up to 10 pages x 20 = 200
            data = post_json(base + "/jobs",
                             {"appliedFacets": {}, "limit": 20, "offset": offset,
                              "searchText": search})
            batch = data.get("jobPostings", [])
            listings += batch
            offset += 20
            if not batch or offset >= data.get("total", 0):
                break
        fetched = 0
        for jp in listings:
            title = jp.get("title", "")
            if not title or not title_is_target(title):
                continue                          # skip detail call for non-matches
            if fetched >= WORKDAY_MAX_DETAIL:
                break
            ext = jp.get("externalPath", "")
            try:
                info = (get_json(base + ext) or {}).get("jobPostingInfo", {}) or {}
                fetched += 1
            except Exception as e:
                print(f"[warn] workday detail {tenant}{ext}: {e}")
                continue
            url = info.get("externalUrl") or \
                f"https://{tenant}.{wd}.myworkdayjobs.com/{site}{ext}"
            out.append({
                "title": info.get("title", title),
                "company": tenant,
                "location": info.get("location", jp.get("locationsText", "")),
                "url": url,
                "content": strip_html(info.get("jobDescription", ""))[:JD_CHARS],
                "posted_ts": to_ts(info.get("startDate")),
                "source": "Workday",
            })
    except Exception as e:
        print(f"[warn] workday {tenant}: {e}")
    return out
