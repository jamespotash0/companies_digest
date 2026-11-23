import os
import re
from .settings import (
    ALLOW_US_ONSITE,
    ALLOW_US_REMOTE,
    NYC_KEYWORDS,
    env_int,
)

# Secondary metro: roles you'd take, but ranked below NYC. Kept OUT of
# NYC_KEYWORDS on purpose — profile.compiled.json overwrites NYC_KEYWORDS from
# profile.json's `locations`, and folding SF in there would make SF postings
# indistinguishable from NYC ones in the ranking. Set JOB_SECONDARY_LOCATIONS=""
# to go back to NYC-plus-US-remote only, or pass your own comma-separated list.
_SECONDARY_DEFAULT = (
    "san francisco,sf,south san francisco,bay area,san francisco bay area,"
    "palo alto,menlo park,mountain view,redwood city,sunnyvale,san mateo,"
    "oakland,berkeley,santa clara,burlingame,daly city,emeryville"
)
SECONDARY_LOC_KEYWORDS = [
    k.strip().lower()
    for k in os.environ.get("JOB_SECONDARY_LOCATIONS", _SECONDARY_DEFAULT).split(",")
    if k.strip()
]

# A bare national/remote US location (no city) — treated as US-remote and kept.
US_NATIONAL_LOC = {
    "united states", "usa", "us", "u.s.", "u.s.a.", "u.s.a", "america",
    "united states of america", "remote", "remote us", "us remote",
    "anywhere", "anywhere in the us", "nationwide",
}
US_LOC_HINTS = ["united states", "usa", "u.s.", "us-based", "us based",
                "remote - us", "remote, us", "remote (us", "remote us",
                "us remote", "anywhere in the us", "onsite or remote"]
# A "remote" role scoped to one state is not a US-remote role — "Remote, TX"
# means Texas residents. NY is absent on purpose (NYC_KEYWORDS owns it), and so
# are the ambiguous two-letter abbreviations that collide with English words
# ("or", "in", "me", "hi", "ok", "de", "la", "pa", "id"): those are matched by
# full name only, since a false positive here silently drops a real posting.
_STATE_ABBR = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "dc", "fl", "ga", "ia", "il",
    "ks", "ky", "ma", "md", "mi", "mn", "mo", "ms", "mt", "nc", "nd", "ne",
    "nh", "nj", "nm", "nv", "oh", "sc", "sd", "tn", "tx", "ut", "va", "vt",
    "wa", "wi", "wv", "wy",
}
_STATE_NAMES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "north carolina", "north dakota", "ohio", "oklahoma",
    "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming",
]
# Abbreviations only in the ", XX" slot a location string actually uses.
_TRAILING_STATE_RE = re.compile(r",\s*([a-z]{2})\b\s*$", re.I)


def _state_scoped(loc):
    """True if this location names one specific non-NY state."""
    m = _TRAILING_STATE_RE.search(loc)
    if m and m.group(1).lower() in _STATE_ABBR:
        return True
    return any(re.search(rf"(?<![a-z]){re.escape(n)}(?![a-z])", loc) for n in _STATE_NAMES)


# Location terms that mark a role as NOT US — drop these even when "remote".
NON_US_LOC_HINTS = [
    "united kingdom", "uk", "london", "england", "scotland", "ireland", "dublin",
    "canada", "toronto", "vancouver", "ontario", "europe", "european", "emea",
    "germany", "berlin", "munich", "france", "paris", "spain", "madrid",
    "netherlands", "amsterdam", "india", "bangalore", "bengaluru", "mumbai",
    "singapore", "australia", "sydney", "apac", "latam", "brazil", "mexico",
    "israel", "tel aviv", "poland", "portugal", "lisbon", "sweden", "stockholm",
    "dubai", "uae", "nigeria", "kenya", "south africa", "philippines", "worldwide",
]


def kw_matcher(keywords):
    """Word-boundary matcher for a keyword list. Substring matching is what makes
    a two-letter term like "sf" fire inside "Dusseldorf" — and it is the same bug
    the funding digest hit with "ai" inside "raises"."""
    if not keywords:
        return re.compile(r"(?!)")             # matches nothing
    alts = "|".join(re.escape(k) for k in sorted(keywords, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alts})(?![a-z0-9])", re.I)


_SECONDARY_RE = kw_matcher(SECONDARY_LOC_KEYWORDS)

# Ranking bonus per location tier, added to the qualification score. NYC first —
# on-site, hybrid or remote, it's the one place an office is a plus rather than a
# move. US-remote now outranks SF/Bay on purpose: a remote role is doable from
# NYC today, whereas on-site in the Bay means relocating, which is the same cost
# as any other out-of-town office. On-site elsewhere in the US ranks last but is
# no longer thrown away.
# Location is a personal preference, not a measure of fit: location_tier() keeps
# or drops a posting, and the tier is shown as a chip. It deliberately awards no
# points — a NYC posting that wants ten years is still a role you won't get, and
# a location bonus was making exactly that kind of posting look competitive.
LOC_LABEL = {"nyc": "NYC", "secondary": "SF/Bay", "us-remote": "US-remote",
             "us-other": "US on-site"}

# Work arrangement, shown as a chip so an on-site role is never a surprise. All
# three are acceptable, so this labels rather than filters — the geography tier
# above is what does the ranking.
def work_mode(location, content):
    """"hybrid" / "remote" / "onsite" — best-effort from the location line, with
    the JD as a fallback. Hybrid wins over remote when a posting says both,
    because "remote-friendly, hybrid in NYC" means you're expected in an office."""
    loc = (location or "").lower().strip()
    if "hybrid" in loc:
        return "hybrid"
    if "remote" in loc or "anywhere" in loc:
        return "remote"
    # A bare "United States" / "Nationwide" is a national posting, not an office
    # in a city called United States.
    if loc in US_NATIONAL_LOC:
        return "remote"
    body = (content or "")[:4000].lower()
    if "hybrid" in body:
        return "hybrid"
    if not loc and "remote" in body:
        return "remote"
    return "onsite"


# Postings at or above this land in "Best matches"; the rest go in a quieter
# second section rather than being dropped — a 30-point posting is still a lead.
#
# 40, not 50: at 50 a genuinely good posting that simply never named a funding
# round or asked for a specific number of years sat in "Worth a look", because
# those two signals alone are worth 26 points. The gates upstream (title,
# seniority, location) have already thrown away everything off-lane, so what
# reaches the scorer is a shortlist — and the line between its halves should
# reflect resume fit, not whether the JD happened to mention its Series A.
BEST_MATCH_MIN = env_int("JOB_BEST_MATCH_MIN", 75)


def location_tier(location, content):
    """Which geography bucket a posting falls in — "" means it doesn't qualify.

    "nyc"       — the focus metro.
    "secondary" — SF / Bay Area (SECONDARY_LOC_KEYWORDS), kept but ranked lower.
    "us-remote" — US-national or remote-with-no-non-US-marker (needs ALLOW_US_REMOTE).
    "us-other"  — a named US city outside NYC/the Bay: on-site, kept but ranked
                  last (needs ALLOW_US_ONSITE). Note "Remote, TX" is NOT this —
                  a state-scoped remote role means residents of that state.
    """
    loc = (location or "").strip().lower()
    if loc:
        # Trust an explicit location: a posting that says "San Francisco" isn't
        # NYC just because its JD name-drops a New York office.
        if any(k in loc for k in NYC_KEYWORDS):
            return "nyc"
        if any(t in loc for t in NON_US_LOC_HINTS):
            return ""                          # remote-EU / remote-worldwide → drop
        if _SECONDARY_RE.search(loc):
            return "secondary"                 # on-site SF/Bay — allowed, ranked below NYC
        if not ALLOW_US_REMOTE:
            return ""
        if _state_scoped(loc):
            # A named US state. "Remote, TX" is still a drop — that means Texas
            # RESIDENTS, not a role you can take from Brooklyn. But "Austin, TX"
            # is an on-site role in the US, which is now kept and ranked last.
            if "remote" in loc or "anywhere" in loc:
                return ""
            return "us-other" if ALLOW_US_ONSITE else ""
        if loc in US_NATIONAL_LOC:
            return "us-remote"                 # bare "United States" / "Remote"
        if any(t in loc for t in US_LOC_HINTS):
            return "us-remote"                 # "Remote - US", "US-based", ...
        if "remote" in loc or "anywhere" in loc:
            return "us-remote"                 # remote, no non-US marker → assume US-ok
        return ""                              # a city we didn't match → on-site elsewhere
    # No location field (e.g. HN "Who's hiring") — fall back to the body text.
    body = (content or "").lower()
    if any(k in body for k in NYC_KEYWORDS):
        return "nyc"
    if any(t in body for t in NON_US_LOC_HINTS):
        return ""
    if _SECONDARY_RE.search(body):
        return "secondary"
    if ALLOW_US_REMOTE and "remote" in body:
        return "us-remote"
    return ""


def location_ok(location, content):
    """Keep NYC, SF/Bay, and (when ALLOW_US_REMOTE) US-remote roles. Drops
    on-site-elsewhere (Austin, Seattle) and anything explicitly non-US."""
    return bool(location_tier(location, content))


# Back-compat alias (older callers referenced is_nyc).
is_nyc = location_ok
