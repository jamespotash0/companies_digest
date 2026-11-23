import json
import os

def load_dotenv(path=".env"):
    """Minimal .env loader so local runs pick up keys without exporting them.
    Runs before the source toggles below read the environment. In GitHub Actions
    there's no .env — those values come from repo secrets — so this is a no-op there."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


load_dotenv()

# Accept either name for the xAI/Grok key: the docs call it XAI_API_KEY, but
# GROK_API_KEY is the intuitive name, so honor both (XAI_API_KEY wins if both set).
if not os.environ.get("XAI_API_KEY") and os.environ.get("GROK_API_KEY"):
    os.environ["XAI_API_KEY"] = os.environ["GROK_API_KEY"]


# ==========================================================================
# SETTINGS FROM THE ENVIRONMENT
# ==========================================================================
# Everything a forker is likely to want different — scoring weights, gates,
# caps, which sources run — reads the environment first and falls back to the
# value written below it, so tuning the bot never means editing Python. Set
# them in .env locally, or as repo secrets/vars for scheduled Actions runs.
# Precedence: code default < profile.compiled.json < environment variable.
def env_int(name, default):
    """Integer setting; the default on anything unset or unparseable."""
    try:
        return int(str(os.environ.get(name, "")).strip())
    except (TypeError, ValueError):
        return default


def env_flag(name, default):
    """Boolean setting. 1/true/yes/on and 0/false/no/off; unset = the default."""
    v = str(os.environ.get(name, "")).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def env_list(name, default):
    """Comma-separated list, lowercased and stripped. An explicitly EMPTY value
    means an empty list — which is why this can't be `os.environ.get() or x`:
    JOB_SECONDARY_LOCATIONS="" has to be able to turn the tier off."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


# ==========================================================================
# YOUR RESUME PROFILE  — drives the qualification score
# ==========================================================================
RESUME = {
    # Titles you're targeting (matched against posting titles).
    "target_titles": [
        # Core PM + tight product-family variants only — kept deliberately narrow.
        "product manager", "associate product manager", "apm",
        "ai product manager", "technical product manager",
        "founding product manager", "founding pm",
        "product owner", "product strategy",
        # Product operations (incl. "Product Operations Manager" / "Associate").
        "product operations", "product ops",
        # Forward-deployed family — the named titles only, not the wider
        # solutions-engineer / implementation / strategist net. "forward deployed"
        # as a stem already covers the FD PM / associate FDE variants; the explicit
        # entries keep the intent readable and score them a little harder.
        "forward deployed", "forward deployed engineer",
        "forward deployed product manager", "associate forward deployed engineer",
        "deployment strategist",
        # Founder's-associate family — the generalist first-hire role at seed-stage
        # startups, where the work is product in everything but name. Apostrophe
        # variants are all listed because titles arrive punctuated every which way
        # (and _norm() folds the curly apostrophe to a straight one first).
        "founder's associate", "founders associate", "founder associate",
        "founding associate", "founder's office", "founders office",
        "office of the founder",
        # "AI product builder" and its cousins — the title startups reach for when
        # the job is prototype-to-production product work on an AI surface.
        "ai product builder", "product builder", "ai prototyper", "ai builder",
        # Product engineer, associate level only — a product-facing build role, not
        # a general SWE req. Deliberately NOT the bare "product engineer" stem, which
        # is how backend/full-stack reqs are titled at most startups.
        "associate product engineer",
    ],
    # Skills / tools from your resume (keyword overlap with the JD).
    "skills": [
        "roadmap", "roadmapping", "prd", "prds", "ai prototyping", "prototyping",
        "ux", "ui", "ui/ux", "product discovery", "discovery", "competitive analysis",
        "gtm", "go-to-market", "stakeholder", "cross-functional", "agile", "scrum",
        "sprint", "standup", "user research", "prioritization", "acceptance criteria",
        "self-serve", "vendor management", "customer success", "onboarding",
        "jira", "sql", "figma", "confluence", "notion", "hubspot", "posthog",
        "airtable", "git", "typescript", "python", "java",
        "0 to 1", "0-to-1", "zero to one", "multi-tenant", "b2b", "saas",
        "api", "quote-to-cash", "quoting",
        # AI-native product work — the vocabulary a 2026 JD uses for the job you
        # actually do. Without these, a posting whose whole premise is "build
        # product with Claude Code" scored only on figma/ux/ui and read as a
        # weak match (the Book of the Month Product Engineer req is the case).
        "claude code", "claude", "agentic", "ai agents", "prompt engineering",
        "ai-native", "ai native",
        # Spec-to-ship vocabulary. "prd"/"roadmap" above is how a PM job is
        # described; these are how the same work is described at a startup that
        # doesn't use the word PRD.
        "spec", "specs", "qa", "experiment", "experimentation", "a/b", "a/b testing",
        "multivariate", "data analysis", "landing page",
    ],
    # Domains you have real exposure to (bonus points).
    "domains": [
        "ai", "artificial intelligence", "workflow", "automation", "agent",
        "llm", "proptech", "real estate", "construction", "vendor",
        "accessibility", "captioning", "telecom", "enterprise",
        "developer", "infrastructure", "fintech", "b2b", "saas",
    ],
    # Your years of experience, compared against whatever bar a posting states.
    # There was briefly a second number for product-titled years only, compared
    # against bars qualified as "N years of PRODUCT MANAGEMENT experience". It
    # worked, but it made every seniority verdict depend on parsing which KIND
    # of experience a sentence meant — a lot of machinery riding on a regex, for
    # a distinction of half a year. One number, one comparison.
    "years": 3,
}

# --- Title gates. All three are env-overridable comma-separated lists, since a
# fork with a different seniority or lane needs exactly these changed and
# nothing else: a senior candidate sets JOB_SENIORITY_EXCLUDE="intern,junior"
# to invert the gate, and an engineer clears JOB_SWE_EXCLUDE="" outright.
# Titles that mean "too senior" — hard-exclude if present.
SENIORITY_EXCLUDE = env_list("JOB_SENIORITY_EXCLUDE", [
    "senior", "sr.", "sr ", "staff", "principal", "lead ", "director",
    "head of", "vp", "vice president", " ii", " iii", "manager of managers",
    "group product", "chief",
])

# ==========================================================================
# SOURCES
# ==========================================================================
# Each source is individually switchable — a forker who wants ATS boards only,
# or who burned through an aggregator's free tier, turns one off without
# touching code.
USE_REMOTIVE = env_flag("JOB_USE_REMOTIVE", True)
USE_ARBEITNOW = env_flag("JOB_USE_ARBEITNOW", True)
USE_HN_WHOISHIRING = env_flag("JOB_USE_HN", True)

USE_WORKDAY = env_flag("JOB_USE_WORKDAY", True)

# The Muse — free, keyless job API with location filtering. Reaches startups not
# on our ATS watchlist. https://www.themuse.com/developers/api/v2
USE_MUSE = env_flag("JOB_USE_MUSE", True)

# Adzuna — free aggregator (indexes many boards + company sites). Needs a free
# app id + key from https://developer.adzuna.com ; auto-disables if unset.
USE_ADZUNA = env_flag("JOB_USE_ADZUNA", True) and bool(
    os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"))

# X / Grok Live Search — surfaces founder "we're hiring" posts that never hit a
# board (the Courted-APM-on-LinkedIn case). Needs XAI_API_KEY; auto-disables if
# unset. Cost is ~$0.005 per search (xAI X-search tool), so ~cents/month.
USE_GROK_X = env_flag("JOB_USE_GROK_X", True) and bool(os.environ.get("XAI_API_KEY"))

# X/Grok leads get their own email — see the send block in main(). A person
# saying "we're hiring" is a different kind of lead from a board posting:
# often no application link, and acting on it means replying to a human
# rather than filling in a form. Mixed in, they read as low-confidence noise.
GROK_SEPARATE_EMAIL = env_flag("JOB_GROK_SEPARATE_EMAIL", True)

# Email subjects. Templates, so you can reword them without touching code;
# "{date}" is substituted, anything else is used verbatim.
#
# Both carry the date on purpose: these arrive daily and Gmail threads on
# subject, so a static subject collapses every day's email into one conversation
# with the newest hidden under the "show trimmed content" fold.
SUBJECT_JOBS = os.environ.get("DIGEST_SUBJECT_JOBS", "Job Posting digest - {date}")
SUBJECT_X = os.environ.get("DIGEST_SUBJECT_X", "X Hiring Posts - {date}")

# How much of each job description we keep. 1500 was too small to be honest:
# the requirements block ("5+ years of...") and the pay band live near the BOTTOM
# of a JD, so a 1500-char window scored every posting on its marketing preamble
# and never saw the two facts that actually disqualify one.
JD_CHARS = int(os.environ.get("JOB_JD_CHARS", "8000"))
# Concurrent ATS board fetches. Each board is a different host, so this is
# parallelism across services rather than pressure on any one of them.
BOARD_WORKERS = int(os.environ.get("BOARD_WORKERS", "8"))

REMOTIVE_SEARCHES = env_list("JOB_REMOTIVE_QUERIES", [
    "product manager", "associate product manager",
    "technical product manager", "ai product manager",
    "forward deployed", "forward deployed product manager",
    "associate forward deployed engineer", "deployment strategist",
    "associate product engineer", "product operations",
    "product owner", "founders associate", "ai product builder"])

# Compact query set reused by the aggregators (Muse/Adzuna) and the X search.
# Fewer, broader phrases than REMOTIVE_SEARCHES to stay within free-tier call
# budgets; the resume title/seniority filter downstream does the fine sorting.
JOB_SEARCH_QUERIES = env_list("JOB_SEARCH_QUERIES", [
    "product manager", "associate product manager",
    "ai product manager", "forward deployed engineer",
    "forward deployed product manager", "product operations",
    "deployment strategist", "founders associate",
    "ai product builder"])

# Company size lookup (lowercased name -> approx employees). Fill in the ones
# you care about; unknown companies rank neutral.
COMPANY_SIZE = {
    # "ramp": 1000, "somestartup": 25,
}
PREFER_LARGER = False   # False = small/early companies rank higher (your fit).
                        # Env: JOB_PREFER_LARGER=1 (applied below the profile).

# --- Location: keep NYC roles, plus US-remote roles ----------------------
REQUIRE_NYC = True
REMOTE_OK = False   # True = enable the global remote boards (Remotive/Arbeitnow)

# Keep US-remote roles too, not just NYC-tagged ones. Many early-startup roles
# (e.g. Mangomint's "GTM Associate, AI Product") are listed as a bare "United
# States" or "Remote — US" with no city, so a strict NYC-substring gate drops
# them even though you could do them from NYC. This widens the gate to: NYC, OR
# US-remote / US-national, while still dropping on-site-elsewhere (SF, Austin)
# and non-US roles. Set JOB_ALLOW_US_REMOTE=0 to go back to strict NYC-only.
ALLOW_US_REMOTE = os.environ.get("JOB_ALLOW_US_REMOTE", "1").lower() not in ("0", "false", "no")

# On-site outside NYC used to be dropped outright. It isn't any more: hybrid,
# on-site and remote are all acceptable, with NYC strongly preferred for the
# ones that put you in an office. So a role on-site in a named US state is KEPT
# and ranked last, rather than never being seen. Set JOB_ALLOW_US_ONSITE=0 to go
# back to dropping it.
ALLOW_US_ONSITE = os.environ.get("JOB_ALLOW_US_ONSITE", "1").lower() not in ("0", "false", "no")

NYC_KEYWORDS = [
    "new york", "nyc", "new york city", "brooklyn", "manhattan",
    "ny,", " ny ", ", ny", "queens", "n.y.",
]

MAX_ITEMS = int(os.environ.get("JOB_MAX_ITEMS", "30"))
# Separate cap for the below-threshold "Worth a look" section, so relegating a
# posting doesn't cost a slot in the main list.
MAX_ITEMS_REST = int(os.environ.get("JOB_MAX_ITEMS_REST", "15"))
SEEN_FILE = "seen_jobs.json"
# How long a posting stays in the "already emailed you this" cache.
SEEN_TTL_DAYS = env_int("JOB_SEEN_TTL_DAYS", 14)

# Only email when a run actually turns up a new posting. The seen-cache means
# almost everything lands in the morning run — the 1pm/4pm runs then found
# nothing new and still emailed an empty "no new postings" digest, which was pure
# noise. With this on (default), an empty run stays silent; a genuinely fresh
# midday role still reaches you same-day. Set SEND_WHEN_EMPTY=1 to always send
# (e.g. as a daily heartbeat, or while debugging).
SEND_WHEN_EMPTY = os.environ.get("SEND_WHEN_EMPTY", "").lower() in ("1", "true", "yes")

# Recency: drop postings older than this many days, using the board's own
# posted/updated timestamp. Newer postings also rank higher (tiebreak after the
# resume match). Sources that don't expose a date are kept and shown without an
# age. Set to 0 to disable age filtering entirely. Overridable via profile.json.
MAX_AGE_DAYS = 21

# Only show postings actually posted in the last N days. Separate from
# MAX_AGE_DAYS, which is about ranking; this is about the digest being a feed of
# what is NEW. Running daily, anything older than about a week either already
# appeared in an earlier email or arrived with a board that was just added to
# the watchlist. 0 disables.
FRESH_ONLY_DAYS = 10

# Hard ceiling on the experience bar you are willing to see. A role asking eight
# years is not one you get with two or three, and ranking it low still left it in
# the list. 0 disables the filter. A posting that states NO bar always passes —
# silence is not a requirement, and about half of all postings state nothing.
# The repo default is deliberately loose; set `max_years` in YOUR profile.json.
MAX_YEARS = 5

# ==========================================================================
# Optional compiled profile (from setup_profile.py). If profile.compiled.json
# exists it overrides RESUME + location/size/recency settings above — so anyone
# can configure everything from profile.json + their resume without editing code.
# Absent = the defaults above apply. Those defaults are the repo author's own
# job search, so a fork that skips setup_profile.py gets somebody else's
# filters — which is why the digest says so loudly at startup.
# ==========================================================================
# Tests must exercise the CODE, not whatever profile happens to be on this
# machine. Without this the suite passes locally against a personal profile and
# in CI against the defaults — two different subjects, one green tick.
IGNORE_PROFILE = os.environ.get("JOB_IGNORE_PROFILE", "").lower() in ("1", "true", "yes")


def _apply_profile():
    global NYC_KEYWORDS, REMOTE_OK, REQUIRE_NYC, PREFER_LARGER, MAX_AGE_DAYS
    global MAX_YEARS
    if IGNORE_PROFILE:
        return
    try:
        with open("profile.compiled.json") as f:
            p = json.load(f)
    except FileNotFoundError:
        # Say so LOUDLY. Without a profile the digest runs on the target titles,
        # skills and locations baked into this file — which are one specific
        # person's job search. A fork that skips setup_profile.py otherwise gets
        # a plausible-looking digest for somebody else's career, with nothing
        # anywhere saying that is what happened.
        print("[profile] NO profile.compiled.json — falling back to the filters "
              "baked into digest/settings.py, which are the repo author's, not "
              "yours. Run: python setup_profile.py")
        return
    except Exception as e:
        print(f"[profile] could not read profile.compiled.json ({e}) — using "
              f"the built-in filters. Run: python setup_profile.py")
        return
    for k in ("target_titles", "skills", "domains", "years"):
        if p.get(k):
            RESUME[k] = p[k]
    if p.get("location_keywords") is not None:
        NYC_KEYWORDS = [k.lower() for k in p["location_keywords"]]
        REQUIRE_NYC = bool(NYC_KEYWORDS)
    if "remote_ok" in p:
        REMOTE_OK = bool(p["remote_ok"])
    if "prefer_larger" in p:
        PREFER_LARGER = bool(p["prefer_larger"])
    if p.get("max_age_days") is not None:
        MAX_AGE_DAYS = int(p["max_age_days"])
    if p.get("max_years") is not None:
        MAX_YEARS = int(p["max_years"])
    print(f"[profile] using profile.compiled.json — {len(RESUME['target_titles'])} titles, "
          f"{len(NYC_KEYWORDS)} location keywords, remote_ok={REMOTE_OK}, "
          f"max_age_days={MAX_AGE_DAYS}, max_years={MAX_YEARS}")


_apply_profile()

# The environment gets the last word: a forker running the repo's committed
# profile can still retune geography or recency for one run without recompiling
# it, and Actions can override per-workflow. Unset vars keep whatever the
# profile (or the default above) decided.
RESUME["target_titles"] = env_list("JOB_TARGET_TITLES", RESUME["target_titles"])
RESUME["skills"] = env_list("JOB_SKILLS", RESUME["skills"])
RESUME["domains"] = env_list("JOB_DOMAINS", RESUME["domains"])
RESUME["years"] = env_int("JOB_YEARS", RESUME["years"])
NYC_KEYWORDS = env_list("JOB_PRIMARY_LOCATIONS", NYC_KEYWORDS)
REQUIRE_NYC = env_flag("JOB_REQUIRE_LOCATION", bool(NYC_KEYWORDS))
REMOTE_OK = env_flag("JOB_REMOTE_OK", REMOTE_OK)
PREFER_LARGER = env_flag("JOB_PREFER_LARGER", PREFER_LARGER)
MAX_AGE_DAYS = env_int("JOB_MAX_AGE_DAYS", MAX_AGE_DAYS)
FRESH_ONLY_DAYS = env_int("JOB_FRESH_ONLY_DAYS", FRESH_ONLY_DAYS)
MAX_YEARS = env_int("JOB_MAX_YEARS", MAX_YEARS)

# Secrets
EMAIL_USER = os.environ.get("EMAIL_USER", "")
EMAIL_PASS = os.environ.get("EMAIL_PASS", "")
EMAIL_TO   = os.environ.get("EMAIL_TO", EMAIL_USER)
SMTP_HOST  = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT  = int(os.environ.get("SMTP_PORT", "587"))
