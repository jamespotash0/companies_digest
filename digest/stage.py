import os
import re
from .filters import _hits, _norm
from .location import kw_matcher
from .scoring import EARLY_STAGE_POINTS, LATE_STAGE_PENALTY
from .settings import env_int, env_list

# --- Stage bias: nudge seed / Series A / B to the top -------------------
EARLY_STAGE_SIGNALS = [
    "seed", "series a", "series b", "pre-seed", "pre seed", "early stage",
    "early-stage", "founding team", "stealth", "backed by", "raised our",
]
LATE_STAGE_SIGNALS = [
    # Terms that can only be about the company itself.
    "publicly traded", "public company", "nasdaq:", "nyse:", "publicly listed",
    "series d", "series e", "series f",
    # ...and terms that describe the company ONLY when it is talking about
    # itself. "fortune 500" and "enterprise-scale" appear far more often as a
    # description of who a startup SELLS TO: "deploy them in secure environments
    # to fortune 500 companies" is a seed-stage JD, and matching it dropped
    # three real early-stage roles from the digest. These require a
    # self-referential phrase nearby — see _late_self_reference().
    "fortune 500", "enterprise-scale", "1,000 employees", "5,000 employees",
    "10,000 employees", "20,000 employees", "thousands of employees", "ipo",
]

# The subset above that needs "we are / our company / joining a ..." nearby
# before it counts as evidence about the employer.
CUSTOMER_AMBIGUOUS = {
    "fortune 500", "enterprise-scale", "ipo", "1,000 employees",
    "5,000 employees", "10,000 employees", "20,000 employees",
    "thousands of employees",
}
_SELF_REF = re.compile(
    r"\b(we are|we're|our company|our team of|join(?:ing)? (?:a|an|our)|"
    r"the company is|company is a|we have grown|we employ)\b", re.I)

# Softer early-stage tells — the language a seed-to-B company uses when the JD
# never names a round. The Courted APM posting is the case: "high-growth SaaS
# company shipping fast", "small, focused team", "Everyone builds" — plainly a
# startup, and it scored no stage bonus at all because it said no round out loud.
#
# Individually these are boilerplate: half the postings on earth say they move
# fast. So a single hit earns NOTHING — EARLY_STAGE_SOFT_MIN must land before the
# bonus applies, which is what separates a cluster of startup language from one
# stray recruiter phrase. Any late-stage tell suppresses them outright, since a
# scale-up describes itself in exactly this vocabulary too. Worth half a named
# round, because that is roughly how much less certain the read is.
EARLY_STAGE_SOFT = [
    "small team", "small, focused team", "founding engineer", "first product hire",
    "early employee", "wear many hats", "from the ground up", "everyone builds",
    "high agency", "scrappy", "generalist", "ground floor", "shipping fast",
    "ship fast", "high-growth", "high growth", "startup", "0 to 1", "zero to one",
]
# How many soft tells must land before the bonus applies. Env-tunable:
# raise it to 3 for a stricter read, or set it to 1 to trust a single tell.
EARLY_STAGE_SOFT_MIN = env_int("JOB_EARLY_STAGE_SOFT_MIN", 2)


_SOFT_RE = _LATE_RE = None


def _soft_re():
    global _SOFT_RE
    if _SOFT_RE is None:
        _SOFT_RE = kw_matcher(EARLY_STAGE_SOFT)
    return _SOFT_RE


def late_stage_prose(content):
    """Late-stage tells that are actually ABOUT THIS COMPANY.

    An unqualified keyword match conflates "is a big company" with "sells to big
    companies" — the second is how most seed-stage AI startups describe
    themselves, and it was dropping them from the digest.
    """
    c = _norm(content)
    out = []
    for hit in _hits(_late_re(), c):
        if hit not in CUSTOMER_AMBIGUOUS:
            out.append(hit)
            continue
        m = re.search(rf"(?<![a-z0-9]){re.escape(hit)}(?![a-z0-9])", c)
        window = c[max(0, m.start() - 120):m.start()] if m else ""
        if _SELF_REF.search(window):
            out.append(hit)
    return out


def _late_re():
    global _LATE_RE
    if _LATE_RE is None:
        _LATE_RE = kw_matcher(LATE_STAGE_SIGNALS)
    return _LATE_RE


# A recent raise is shown as a badge — it is the best cold-outreach hook you get
# — but it no longer moves the score. Whether a company just raised says nothing
# about whether you clear the bar for the role.
FUNDED_BONUS = 0

# Stage is a preference, so it filters rather than scores. A company that is
# publicly traded or clearly late-stage is dropped; everything else is kept and
# its stage is shown. Set JOB_EXCLUDE_LATE_STAGE=0 to keep them.
# Off by default. Dropping a company for being late-stage costs you real roles,
# and the prose evidence for "late-stage" is unreliable — see SELF_LATE_SIGNALS.
# Set JOB_EXCLUDE_LATE_STAGE=1 to turn it on.
EXCLUDE_LATE_STAGE = os.environ.get(
    "JOB_EXCLUDE_LATE_STAGE", "0").lower() in ("1", "true", "yes")


_FUNDED = {}
_FUNDED_BY_TOKEN = {}


def _load_funded():
    """Index the funding digest's recently-raised companies two ways: by
    normalized name (Muse/Adzuna/HN give real names) and by ATS token (the board
    fetchers set company=<token>)."""
    global _FUNDED, _FUNDED_BY_TOKEN
    try:
        import watchlist as _wl
        _FUNDED = _wl.get_funded()
    except Exception as e:
        print(f"[info] funding flags unavailable: {e}")
        return
    _FUNDED_BY_TOKEN = {t: v for v in _FUNDED.values() for t in v.get("tokens", [])}
    if _FUNDED:
        print(f"[loop] {len(_FUNDED)} recently-funded companies flagged "
              f"({len(_FUNDED_BY_TOKEN)} with a known ATS board)")


# What each round is worth as a stage signal, from the permanent stage record
# rather than from whatever the JD chose to say about itself. Your band is seed
# through Series B; C is neutral; D and beyond is the same penalty a JD bragging
# about being public would earn.
# Which rounds count as "your band" is itself a fork-level choice: someone
# targeting growth-stage companies sets JOB_EARLY_ROUNDS="series b,series c,
# series d" and JOB_LATE_ROUNDS="ipo,public,acquired". Anything named in neither
# list is neutral (0), which is what "series c" is by default.
EARLY_ROUNDS = env_list("JOB_EARLY_ROUNDS",
                        ["pre-seed", "pre seed", "seed", "series a", "series b"])
LATE_ROUNDS = env_list("JOB_LATE_ROUNDS",
                       ["series d", "series e", "series f", "series g",
                        "ipo", "public", "acquired"])
# Rounds we rank as neither: a known fact worth 0, which still beats the JD's
# own prose about itself (see stage_points -> None, which means "no fact").
NEUTRAL_ROUNDS = env_list("JOB_NEUTRAL_ROUNDS", ["series c"])
STAGE_POINTS = {r: EARLY_STAGE_POINTS for r in EARLY_ROUNDS}
STAGE_POINTS.update({r: 0 for r in NEUTRAL_ROUNDS if r not in STAGE_POINTS})
STAGE_POINTS.update({r: LATE_STAGE_PENALTY for r in LATE_ROUNDS if r not in STAGE_POINTS})

_STAGE = {}
_STAGE_BY_TOKEN = {}


def _load_stage():
    """Company -> known round, indexed by name and by ATS token (board fetchers
    set company=<token>), exactly like the funded index."""
    global _STAGE, _STAGE_BY_TOKEN
    try:
        import watchlist as _wl
        _STAGE = _wl.get_stage()
    except Exception as e:
        print(f"[info] stage facts unavailable: {e}")
        return
    _STAGE_BY_TOKEN = {t: v for v in _STAGE.values() for t in v.get("tokens", [])}
    if _STAGE:
        print(f"[stage] {len(_STAGE)} companies with a known round")


def stage_note(company):
    """The known round for a posting's company, or {}."""
    c = (company or "").lower().strip()
    if not c:
        return {}
    return _STAGE_BY_TOKEN.get(c) or _STAGE.get(c) or {}


def stage_points(round_name):
    """Points for a named round, or None if we don't rank it."""
    r = _norm(round_name or "").strip()
    for k in sorted(STAGE_POINTS, key=len, reverse=True):
        if k in r:
            return STAGE_POINTS[k]
    return None


def funding_note(company):
    """The raise behind a posting's company, or {} — matched on ATS token first
    (exact) and then on the normalized company name."""
    c = (company or "").lower().strip()
    if not c:
        return {}
    return _FUNDED_BY_TOKEN.get(c) or _FUNDED.get(c) or {}


def funding_label(fund):
    """'raised $12M Series A' — the short badge shown next to a posting."""
    if not fund:
        return ""
    bits = " ".join(x for x in (fund.get("amount"), fund.get("round")) if x)
    return f"raised {bits}".strip() if bits else "recently funded"
