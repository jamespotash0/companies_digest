from urllib.parse import quote_plus
import json
import os
import re
import urllib.request
from .net import get_json, strip_html, to_ts
from .settings import (
    JD_CHARS,
    JOB_SEARCH_QUERIES,
    MAX_AGE_DAYS,
    REMOTIVE_SEARCHES,
)

# --------------------------- fetchers (each returns list of normalized dicts)
def fetch_remotive():
    out = []
    for q in REMOTIVE_SEARCHES:
        try:
            data = get_json(f"https://remotive.com/api/remote-jobs?search={quote_plus(q)}")
            for j in data.get("jobs", [])[:40]:
                out.append({
                    "title": j.get("title", ""),
                    "company": j.get("company_name", ""),
                    "location": j.get("candidate_required_location", "Remote"),
                    "url": j.get("url", ""),
                    "content": strip_html(j.get("description", ""))[:1200],
                    "posted_ts": to_ts(j.get("publication_date")),
                    "source": "Remotive",
                })
        except Exception as e:
            print(f"[warn] remotive '{q}': {e}")
    return out


def fetch_arbeitnow():
    out = []
    try:
        data = get_json("https://www.arbeitnow.com/api/job-board-api")
        for j in data.get("data", [])[:100]:
            out.append({
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": j.get("location", ""),
                "url": j.get("url", ""),
                "content": strip_html(j.get("description", ""))[:1200],
                "posted_ts": to_ts(j.get("created_at")),
                "source": "Arbeitnow",
            })
    except Exception as e:
        print(f"[warn] arbeitnow: {e}")
    return out


def fetch_hn():
    out = []
    try:
        s = get_json("https://hn.algolia.com/api/v1/search_by_date"
                     "?query=Ask%20HN%20Who%20is%20hiring&tags=story&hitsPerPage=1")
        if not s.get("hits"):
            return out
        story_id = s["hits"][0]["objectID"]
        c = get_json(f"https://hn.algolia.com/api/v1/search?tags=comment,story_{story_id}"
                     "&hitsPerPage=200")
        for h in c.get("hits", []):
            body = strip_html(h.get("comment_text", ""))
            if not body:
                continue
            first = body.split("\n")[0][:120]
            out.append({
                "title": first,
                "company": first.split("|")[0].strip()[:60] if "|" in first else "",
                "location": "",
                "url": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "content": body[:1200],
                "posted_ts": to_ts(h.get("created_at_i")),
                "source": "HN Who's Hiring",
            })
    except Exception as e:
        print(f"[warn] hn: {e}")
    return out


def fetch_muse():
    """The Muse — free, keyless. Location-filtered to New York + Flexible/Remote;
    the downstream US-remote gate keeps only the ones that qualify."""
    out = []
    # Muse categories that hold our target roles. 'Product Management' is the core;
    # the others catch FDE / deployment / solutions-flavored postings.
    cats = ["Product Management", "Project Management", "Sales", "Data Science"]
    from urllib.parse import urlencode
    for page in range(1, 3):                       # 2 pages ≈ 40 postings/loc
        params = [("page", page), ("location", "New York, NY"),
                  ("location", "Flexible / Remote")]
        params += [("category", c) for c in cats]
        try:
            data = get_json("https://www.themuse.com/api/public/jobs?" + urlencode(params))
        except Exception as e:
            print(f"[warn] muse page {page}: {e}")
            break
        results = data.get("results", [])
        if not results:
            break
        for j in results:
            locs = ", ".join(l.get("name", "") for l in j.get("locations", []))
            comp = (j.get("company") or {}).get("name", "")
            out.append({
                "title": j.get("name", ""),
                "company": comp,
                "location": locs,
                "url": (j.get("refs") or {}).get("landing_page", ""),
                "content": strip_html(j.get("contents", ""))[:JD_CHARS],
                "posted_ts": to_ts(j.get("publication_date")),
                "source": "The Muse",
            })
    return out


def fetch_adzuna():
    """Adzuna — free aggregator (indexes many boards + company career pages).
    Queried for US roles in/around New York; the location gate filters further."""
    app_id = os.environ.get("ADZUNA_APP_ID", "")
    app_key = os.environ.get("ADZUNA_APP_KEY", "")
    if not (app_id and app_key):
        return []
    from urllib.parse import urlencode
    out = []
    for q in JOB_SEARCH_QUERIES:
        params = urlencode({
            "app_id": app_id, "app_key": app_key,
            "what_phrase": q,                       # exact-phrase title match
            "where": "New York",
            "distance": 80,                         # km — covers the NYC metro
            "max_days_old": MAX_AGE_DAYS or 21,
            "results_per_page": 25, "content-type": "application/json",
        })
        try:
            data = get_json(f"https://api.adzuna.com/v1/api/jobs/us/search/1?{params}")
        except Exception as e:
            print(f"[warn] adzuna '{q}': {e}")
            continue
        for j in data.get("results", []):
            loc = (j.get("location") or {}).get("display_name", "")
            out.append({
                "title": j.get("title", ""),
                "company": (j.get("company") or {}).get("display_name", ""),
                "location": loc,
                "url": j.get("redirect_url", ""),
                "content": strip_html(j.get("description", ""))[:JD_CHARS],
                "posted_ts": to_ts(j.get("created")),
                "source": "Adzuna",
            })
    return out


def fetch_grok_x():
    """xAI Grok X (Twitter) search via the Agent Tools API (the current API — the
    older Live Search / search_parameters form returns 410 Gone). Surfaces recent
    founder/recruiter "we're hiring" posts and returns them as normalized postings
    whose URL is the tweet. Needs XAI_API_KEY (GROK_API_KEY is aliased to it).
    Non-fatal on any error."""
    api_key = os.environ.get("XAI_API_KEY", "")
    if not api_key:
        return []
    model = os.environ.get("XAI_MODEL", "grok-4.5")
    days = int(os.environ.get("XAI_LOOKBACK_DAYS", "5"))
    roles = ", ".join(JOB_SEARCH_QUERIES)
    prompt = (
        f"Search X for posts from the last {days} days where a founder, recruiter, "
        f"or employee is HIRING for a role like: {roles}. Focus on early-stage US "
        "startups, New York or San Francisco / Bay Area or US-remote. Only real, "
        "currently-open roles (ignore generic threads, advice, and 'looking for "
        "work' posts). Return ONLY a JSON array, no prose, no code fences:\n"
        '[{"company":"","role":"","location":"","tweet_url":"","poster":"@handle",'
        '"poster_title":"their role at the company, e.g. co-founder & CEO",'
        '"posted":"YYYY-MM-DD","quote":"the most relevant sentence from the post, '
        'verbatim, <=200 chars","summary":"2-3 sentences: what the company does, '
        'what the role actually involves, and any seniority/experience bar stated",'
        '"funding":"latest round if the post or their profile mentions it, e.g. '
        '\'Series A, $15M, Mar 2026\'","apply_url":"careers/application link if the '
        'post gives one"}]\n'
        "The summary and quote are what I read to decide whether to reply, so make "
        "them specific — name the product and the actual scope of the job, never "
        "just restate the title. "
        "Use \"\" for anything you cannot determine. Up to 15 items."
    )
    payload = {
        "model": model,
        "input": [{"role": "user", "content": prompt}],
        "tools": [{"type": "x_search"}],
    }
    try:
        req = urllib.request.Request(
            "https://api.x.ai/v1/responses",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"}, method="POST")
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        print(f"[warn] grok/x search: {e}")
        return []
    # The final answer is the output item of type "message"; earlier items are
    # reasoning + tool calls. Fall back to output_text if present.
    text = data.get("output_text") or ""
    if not text:
        for item in data.get("output", []):
            if item.get("type") == "message":
                for c in item.get("content", []):
                    if c.get("type") == "output_text":
                        text += c.get("text", "")
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        print("[info] grok/x: no postings parsed")
        return []
    try:
        leads = json.loads(m.group(0))
    except Exception:
        print("[info] grok/x: unparseable JSON")
        return []
    out = []
    for l in leads:
        role = (l.get("role") or "").strip()
        if not role:
            continue
        company = (l.get("company") or "").strip()
        # A tweet URL is ideal; if the model didn't capture one, fall back to a
        # search so the lead is still actionable (and passes the URL gate).
        url = (l.get("tweet_url") or "").strip()
        if not url:
            url = "https://www.google.com/search?q=" + quote_plus(
                f"{company} {role} hiring")
        summary = (l.get("summary") or "").strip()
        quote = (l.get("quote") or "").strip()
        poster = (l.get("poster") or "").strip()
        if poster and not poster.startswith("@"):
            poster = "@" + poster
        # The scoring blob wants every scrap of text; the display fields are kept
        # separate so the email can lay them out instead of dumping one string.
        out.append({
            "title": role,
            "company": company,
            # Prompt constrains to US/NYC/SF; default blanks to US so the location
            # gate keeps them (a model-supplied non-US location is still honored).
            "location": (l.get("location") or "United States").strip(),
            "url": url,
            "content": " ".join(x for x in (summary, quote, l.get("funding") or "") if x)[:900],
            "posted_ts": to_ts(l.get("posted") or ""),
            "source": "X/Grok",
            "note": summary[:400],
            "quote": quote[:220],
            "poster": poster,
            "poster_title": (l.get("poster_title") or "").strip()[:60],
            "round": (l.get("funding") or "").strip()[:60],
            "apply_url": (l.get("apply_url") or "").strip(),
        })
    print(f"[info] grok/x: {len(out)} hiring posts")
    return out
