import html
import os
import re
from .location import kw_matcher
from .settings import RESUME, SENIORITY_EXCLUDE, env_list

# Software-engineering (coding) roles to hard-exclude — you want product /
# deployment / solutions, NOT SWE. Note "Forward Deployed Engineer" and
# "Solutions Engineer" are KEPT (not coding-IC roles); only titles like
# "...Software Engineer", backend/frontend/full-stack, data/ML eng, etc. drop.
SWE_EXCLUDE = env_list("JOB_SWE_EXCLUDE", [
    "software engineer", "software developer", "swe ", "backend", "back-end",
    "front end", "front-end", "frontend", "full stack", "full-stack", "fullstack",
    "web developer", "mobile engineer", "ios engineer", "android engineer",
    "data engineer", "machine learning engineer", "ml engineer", "devops",
    "site reliability", " sre", "security engineer", "firmware", "game developer",
    "developer advocate",
])

# Same-title-stem, wrong job. Rogo posts a "Forward Deployed Investor" and a
# "Forward Deployed Banker" — real NYC roles that match the "forward deployed"
# stem and are finance jobs, not product ones. Found by running the gate over a
# real board dump; there is no way to guess these from first principles.
OFF_LANE_EXCLUDE = env_list("JOB_OFF_LANE_EXCLUDE", [
    "investor", "banker", "trader", "analyst, investment", "recruiter",
    "account executive", "sales representative", "sdr", "bdr",
])

# Words in the JD body that signal a junior/early-career fit (boost).
JUNIOR_SIGNALS = env_list("JOB_JUNIOR_SIGNALS", [
    "2+ years", "1+ year", "0-2", "1-3 years", "2-3 years", "associate",
    "early career", "early-career", "entry level", "entry-level", "new grad",
    "junior", "recent graduate", "1-2 years",
])

# Words that signal it wants a lot more experience than you have (penalty).
SENIOR_SIGNALS = env_list("JOB_SENIOR_SIGNALS",
                          ["5+ years", "6+ years", "7+ years", "8+ years",
                           "10+ years", "minimum of 5", "minimum of 6",
                           "minimum of 7"])


# --------------------------- filtering & scoring
# "lead" as a standalone word is senior, but the SENIORITY_EXCLUDE substring
# "lead " (trailing space) misses a title that ENDS in "Lead" (e.g. "GTM Lead",
# "Product Lead"). Match it on a word boundary instead — \blead\b won't fire on
# "leading"/"leader"/"leadership".
_LEAD_RE = re.compile(r"\blead\b")


def _norm(text):
    """Lowercase and fold the punctuation job boards vary on, so one entry in
    target_titles matches "Founder's Associate", "Founder\u2019s Associate" and
    "Founders Associate" alike."""
    return (text or "").lower().replace("\u2019", "'").replace("\u2018", "'") \
                       .replace("\u2013", "-").replace("\u2014", "-")


def _fold_punct(text):
    """Fold the punctuation that separates spellings of the same title.

    Hyphens and slashes become spaces, so "Forward-Deployed" matches the stems
    written "Forward Deployed". Apostrophes are dropped and "founders" is
    singularised, so "Founder's Associate", "Founders Associate" and "Founder
    Associate" are one thing — otherwise the stem list has to enumerate every
    spelling, and a compiled profile only ever guesses a couple of them.

    Applied for MATCHING only; the original string is what gets displayed.
    """
    t = re.sub(r"['\u2018\u2019]", "", text or "")          # founder's -> founders
    t = re.sub(r"\bfounders\b", "founder", t)              # founders  -> founder
    return re.sub(r"\s+", " ", re.sub(r"[-/\u2010-\u2015]+", " ", t)).strip()


def _reads_senior(t):
    """Seniority check on a folded title, ignoring any target stem it contains:
    a stem you asked for ("lead product manager", "chief of staff") can't veto
    itself, but "Senior Lead Product Manager" still has "senior" left over."""
    # Longest first, or "product manager" eats the middle of "lead product
    # manager" and strands the "lead".
    for x in sorted(RESUME["target_titles"], key=len, reverse=True):
        t = t.replace(_fold_punct(x), " ")
    return any(x in t for x in SENIORITY_EXCLUDE) or bool(_LEAD_RE.search(t))


def title_is_target(title):
    t = _fold_punct(_norm(title))
    if _reads_senior(t):
        return False
    if any(x in t for x in SWE_EXCLUDE):      # no software-engineering roles
        return False
    if any(x in t for x in OFF_LANE_EXCLUDE):  # right stem, wrong profession
        return False
    # Stems are folded the same way as the title, so a stem written
    # "founder's associate" matches a title folded to "founder associate".
    return any(_fold_punct(x) in t for x in RESUME["target_titles"])


# --------------------------- screening facts pulled straight from the JD
# The five things you'd otherwise open the posting to learn. All regex, no API
# call: these phrasings are formulaic enough that a model would be overkill.
_YEARS_RANGE_RE = re.compile(
    r"(\d{1,2})\s*(?:-|\u2013|\u2014|to)\s*(\d{1,2})\+?\s*(?:\+\s*)?years?", re.I)
_YEARS_MIN_RE = re.compile(
    r"(?:at least|minimum of|min\.?\s*of|\bover\b)?\s*(\d{1,2})\s*\+\s*years?"
    r"|(?:at least|minimum of)\s*(\d{1,2})\s*years?", re.I)
# $120,000 - $160,000 / $120k-$160k / 120k–160k
_MONEY_RE = re.compile(r"\$\s?(\d{2,3})(?:,(\d{3})|\s?k)\b", re.I)
# Days in office. Three phrasings, tried in order — the office word can come
# BEFORE the count ("in the office 3-4 days per week", the Courted JD that read
# as plain "Onsite"), AFTER it ("3 days a week in office"), or be absent when the
# surrounding JD has already established hybrid/onsite. Counts may be a range.
_DAYS_N = r"\d(?:\s*(?:-|\u2013|\u2014|to)\s*\d)?"
_DAYS_IN_OFFICE_RES = [
    re.compile(rf"(?:in|at|from)\s+(?:the\s+)?office[^.\n]{{0,20}}?({_DAYS_N})\s*days?", re.I),
    re.compile(rf"({_DAYS_N})\s*days?\s*(?:a|per)\s*week\s*(?:in|at|from)\b", re.I),
    re.compile(rf"({_DAYS_N})\s*days?\s*(?:a|per)\s*week\b", re.I),
]


def _days_in_office(c):
    """'3' or '3-4' — normalized, or None. Returns the low end too, so a count
    under 5 can correct an 'Onsite' read to what it actually is: hybrid."""
    for rx in _DAYS_IN_OFFICE_RES:
        m = rx.search(c)
        if m:
            d = re.sub(r"\s*(?:-|\u2013|\u2014|to)\s*", "\u2013", m.group(1))
            return d, int(re.match(r"\d", d).group(0))
    return None
_NO_SPONSOR_RE = re.compile(
    r"(?:not|unable to|cannot|can't|do(?:es)?n't)\s+(?:be\s+able\s+to\s+)?"
    r"(?:provide|offer|sponsor|support)[^.]{0,40}(?:visa|sponsor)", re.I)
_SPONSOR_RE = re.compile(r"(?:visa sponsorship (?:is )?available|we (?:do )?sponsor|"
                         r"sponsorship (?:is )?(?:available|offered))", re.I)


def screen_facts(content):
    """Years wanted, comp, work model, equity, sponsorship — the facts that let
    you rule a posting in or out at a glance instead of opening it."""
    c = _norm(content or "")
    f = {}

    # Years: a stated range beats a bare "5+ years", and the FIRST mention is
    # almost always the requirement (later ones are "10 years in the industry"
    # boilerplate about the founders).
    at = None
    m = _YEARS_RANGE_RE.search(c)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if 0 <= lo <= hi <= 25:
            f["years"] = f"{lo}\u2013{hi} yrs"
            f["years_min"] = lo
            at = m.end()
    if "years" not in f:
        m = _YEARS_MIN_RE.search(c)
        if m:
            n = int(m.group(1) or m.group(2))
            if 0 < n <= 25:
                f["years"] = f"{n}+ yrs"
                f["years_min"] = n
                at = m.end()
    if at is not None:
        kind, term = experience_field(content, at)
        if kind == "other":
            f["field_mismatch"] = term


    # Comp: two salary-shaped numbers = a posted band. Anything outside a
    # plausible salary window is a fundraise figure or an ARR brag, not pay.
    nums = []
    for m in _MONEY_RE.finditer(c):
        v = int(m.group(1)) * 1000 + (int(m.group(2)) if m.group(2) else 0)
        if 50_000 <= v <= 500_000:
            nums.append(v)
    if len(nums) >= 2 and max(nums) > min(nums):
        f["comp"] = f"${min(nums) // 1000}k\u2013${max(nums) // 1000}k"
    elif len(nums) == 1:
        f["comp"] = f"~${nums[0] // 1000}k"

    # Work model — check hybrid first: a hybrid JD says "remote" too.
    if "hybrid" in c:
        d = _days_in_office(c)
        f["work_model"] = f"Hybrid \u00b7 {d[0]}d in office" if d else "Hybrid"
    elif re.search(r"fully remote|remote[- ]first|100% remote", c):
        f["work_model"] = "Remote"
    elif re.search(r"\bon[- ]?site\b|\bin[- ]office\b|in the office", c):
        # A JD that never says "hybrid" but asks for 3-4 days in the office IS
        # hybrid; calling it Onsite reads harsher than the job actually is.
        d = _days_in_office(c)
        if d and d[1] < 5:
            f["work_model"] = f"Hybrid \u00b7 {d[0]}d in office"
        else:
            f["work_model"] = f"Onsite \u00b7 {d[0]}d/wk" if d else "Onsite"

    if re.search(r"\bequity\b|stock options|\brsus?\b", c):
        f["equity"] = "Equity"
    if _NO_SPONSOR_RE.search(c):
        f["sponsorship"] = "No visa sponsorship"
    elif _SPONSOR_RE.search(c):
        f["sponsorship"] = "Sponsors visas"
    return f


# How these read when written by a person. Blanket .upper() turned "saas" into
# "SAAS"; blanket sentence-case turned "ai" into "Ai". Neither is how anyone
# writes them, and the digest prints these terms verbatim.
_TERM_CASE = {
    "apis": "APIs", "llms": "LLMs", "prds": "PRDs", "kpis": "KPIs",
    "saas": "SaaS", "paas": "PaaS", "iaas": "IaaS", "devops": "DevOps",
    "github": "GitHub", "postgres": "Postgres", "typescript": "TypeScript",
    "javascript": "JavaScript", "nodejs": "Node.js", "figma": "Figma",
    "notion": "Notion", "jira": "Jira", "looker": "Looker", "python": "Python",
    "a/b": "A/B", "ui/ux": "UI/UX", "go-to-market": "go-to-market",
}
_ACRONYMS = {"ai", "llm", "llms", "ui", "ux", "api", "apis", "sql", "b2b", "b2c",
             "gtm", "prd", "prds", "qa", "crm", "ml", "nlp", "kpi", "roi",
             "sdk", "cli", "seo", "arr", "sso"}


def _pretty_term(t):
    """'ai' -> 'AI', 'saas' -> 'SaaS'. Multi-word terms get each word treated,
    so "ai agents" reads "AI agents" rather than being left alone."""
    low = t.lower()
    if low in _TERM_CASE:
        return _TERM_CASE[low]
    if low in _ACRONYMS:
        return low.upper()
    if " " in low:
        return " ".join(_pretty_term(w) for w in low.split(" "))
    return t


def fit_sentence(item):
    """One line on what the work actually is.

    Deliberately says nothing about years or location: both are chips directly
    above, and repeating them was how a single card came to state the experience
    bar three times — once as a chip, once here, and once in a trailing gap line.
    """
    bd = item.get("breakdown") or {}
    by = {label: terms for label, _, terms in (bd.get("parts") or [])}
    doms = by.get("your domains") or []
    skills = by.get("resume skills") or []
    bits = []
    if doms:
        bits.append(", ".join(_pretty_term(d) for d in doms[:3]))
    if skills:
        bits.append("asks for " + ", ".join(_pretty_term(k) for k in skills[:3]))
    if not bits:
        return ""
    return " \u00b7 ".join(html.escape(b) for b in bits)


# --- what the company does -------------------------------------------------
# One sentence lifted from the company's own JD. No LLM call: nearly every JD
# opens with "Etched is building..." or "At Rhoda AI, we're building...". On 38
# live boards this found a usable line for ~30; the rest get no about line,
# which beats a wrong one.
_ABOUT_VERB = (r"(?:is|are|builds?|building|helps?|helping|makes?|provides?|develops?|"
               r"creates?|powers?|enables?|delivers?|delivering|designs?|automates?|"
               r"connects?|transforms?|pioneer\w*|disrupt\w*|on a mission|the team behind)")
_ABOUT_SUBJECT = re.compile(
    r"^(?:about (?:us|[\w&.' -]{1,40}?)\s*[:\-\u2013\u2014]?\s*)?"
    r"(?:at ([\w&.' -]{1,40}?),\s*)?"
    r"([\w&.'\u2019-]+(?:\s+[\w&.'\u2019-]+){0,3}?)"
    rf"(?:\s*\([^)]*\))?,?\s+(?:\w+\s+){{0,2}}?{_ABOUT_VERB}\b", re.I)
# A sentence about the role or the perks, not the company. "X is hiring a GM"
# has the company as its subject and still says nothing about what X does.
_ABOUT_JUNK = re.compile(
    r"\b(hiring|seeking|looking for|offer|benefit|salary|401|pto|equal opportunity|"
    r"candidates?|you'?ll|your|team is growing|this role|believe|employees|"
    r"committed to)\b", re.I)
_ABOUT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _alnum(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _names_company(words, company):
    """"Bishop Fox" names board token "bishopfox"; "Redox" names "redoxengine"."""
    w, c = _alnum(words), _alnum(company)
    return len(w) >= 3 and bool(c) and (w.startswith(c) or c.startswith(w))


def company_blurb(company, content, limit=200):
    """What the company does, in one sentence from its own JD, or ""."""
    fallback = ""
    for s in _ABOUT_SPLIT.split((content or "")[:4000]):
        s = re.sub(r"\s+", " ", s).strip(" -\u2022*#")
        if not 40 <= len(s) <= 400 or _ABOUT_JUNK.search(s):
            continue
        m = _ABOUT_SUBJECT.match(s)
        if not m:
            continue
        at, subj = m.group(1), m.group(2)
        if (at and _names_company(at, company)) or _names_company(subj, company):
            fallback = s
            break
        if not fallback and re.fullmatch(r"we(?:'re|\u2019re| are)?", subj, re.I):
            fallback = s
    if len(fallback) > limit:
        fallback = fallback[:limit].rsplit(" ", 1)[0] + "\u2026"
    return fallback


# Built on first use, not at import: _apply_profile() may have replaced
# RESUME["skills"]/["domains"] from profile.compiled.json by then.
_SKILL_RE = _DOMAIN_RE = None


def _skill_re():
    global _SKILL_RE
    if _SKILL_RE is None:
        _SKILL_RE = kw_matcher(RESUME["skills"])
    return _SKILL_RE


def _domain_re():
    global _DOMAIN_RE
    if _DOMAIN_RE is None:
        _DOMAIN_RE = kw_matcher(RESUME["domains"])
    return _DOMAIN_RE


# Spellings of the SAME skill. Without this "roadmap" and "roadmapping", or
# "prd"/"prds", or "ui"/"ux"/"ui/ux", each counted separately — so a JD that
# happened to use two spellings of one idea outscored a JD that used one
# spelling of two ideas. Kept deliberately small: this collapses obvious
# synonyms, it is not a thesaurus.
_SYNONYMS = {
    "roadmapping": "roadmap",
    "prds": "prd",
    "ux": "ui", "ui/ux": "ui",
    "gtm": "go-to-market",
    "prototyping": "ai prototyping",
    "0-to-1": "0 to 1", "zero to one": "0 to 1",
    "artificial intelligence": "ai",
    "agents": "agent",
    "workflows": "workflow",
}
# Deliberately NOT here: "real estate" -> "proptech" or "machine learning" ->
# "ai". Those are near-synonyms, not spellings of one word, and collapsing them
# would change what the digest SHOWS you matched on — the email prints these
# terms, so a mapping that rewrites meaning is a mapping that lies.


def _canon(term):
    return _SYNONYMS.get(term, term)


def _hits(rx, blob):
    """Distinct matched terms, in first-seen order (the email prints these).

    Distinct by MEANING, not by spelling — see _SYNONYMS.
    """
    seen, out = set(), []
    for m in rx.finditer(blob):
        k = _canon(m.group(0).lower())
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


# --- seniority --------------------------------------------------------------
# WHAT KIND of experience a posting is asking for. "3 years of experience" is a
# question about you; "3 years of experience in banking operations" is a question
# about a career you have not had, and the number is beside the point. Read from
# the words right after the bar.
#
# This is NOT the old two-numbers idea. There is still one `years`. The question
# here is whether the experience being demanded is the kind you have at all.
YOUR_FIELD = [
    "product", "product management", "product manager", "product owner",
    "product operations", "product ops", "pm", "apm", "product delivery",
    "delivery", "product development", "technical product", "ai product",
    "software", "saas", "startup", "startups", "technology", "tech",
]
# Fields where the years asked for are somebody else's career. Deliberately a
# short list of the ones that actually show up on postings that otherwise pass
# the title gate — a "Product Manager, Banking" wanting 3 years IN BANKING is a
# different job from a product role at a bank.
OTHER_FIELDS = [
    "banking", "investment banking", "capital markets", "trading", "accounting",
    "audit", "tax", "actuarial", "underwriting", "insurance claims",
    "operations", "supply chain", "logistics", "manufacturing", "warehouse",
    "sales", "quota", "account management", "business development",
    "marketing", "brand", "advertising", "public relations", "seo",
    "recruiting", "talent acquisition", "human resources", "payroll",
    "consulting", "management consulting", "investment", "private equity",
    "venture capital", "legal", "law", "compliance", "clinical", "nursing",
    "teaching", "hardware", "mechanical", "civil engineering", "construction management",
]
FIELD_MISMATCH_FACTOR = float(os.environ.get("JOB_FIELD_MISMATCH_FACTOR", "0.2"))


def experience_field(content, at=None):
    """('mine'|'other'|'unspecified', matched_term) for the experience demanded.

    Only the words immediately after the number count, and whichever kind is
    named FIRST wins — "3 years of product experience in a banking environment"
    is a product bar, "3 years of banking operations experience" is not.
    """
    c = _norm(content or "")
    window = c[at:at + 70] if at is not None else c[:200]
    mine = min(((window.find(f), f) for f in YOUR_FIELD if f in window),
               default=None)
    other = min(((window.find(f), f) for f in OTHER_FIELDS if f in window),
                default=None)
    if mine and other:
        return ("mine", mine[1]) if mine[0] < other[0] else ("other", other[1])
    if other:
        return "other", other[1]
    if mine:
        return "mine", mine[1]
    return "unspecified", ""
