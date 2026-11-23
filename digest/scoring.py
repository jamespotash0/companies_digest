import os
import re
from .filters import (
    FIELD_MISMATCH_FACTOR,
    JUNIOR_SIGNALS,
    SENIOR_SIGNALS,
    _domain_re,
    _fold_punct,
    _hits,
    _norm,
    _reads_senior,
    _skill_re,
    screen_facts,
)
from .settings import RESUME, env_int

# ==========================================================================
# SCORING WEIGHTS
# ==========================================================================
# What each signal is worth. These are the numbers that decide the ranking, so
# they're the first thing a fork with a different job hunt needs to change —
# e.g. a senior candidate flips the seniority weights, and someone hunting one
# specific title raises JOB_TITLE_POINTS and drops the domain weight. The
# per-signal caps stop one long keyword list from swamping the total.
TITLE_POINTS = env_int("JOB_TITLE_POINTS", 12)          # per target-title hit
TITLE_MAX = env_int("JOB_TITLE_MAX", 40)
JUNIOR_POINTS = env_int("JOB_SENIORITY_FIT_POINTS", 20)  # JD wants your years
# Penalties are given as positive magnitudes and always subtracted, so a stray
# "JOB_TOO_SENIOR_PENALTY=25" can't accidentally become a reward.
SENIOR_PENALTY = -abs(env_int("JOB_TOO_SENIOR_PENALTY", 25))
SKILL_POINTS = env_int("JOB_SKILL_POINTS", 3)           # per resume skill hit
SKILL_MAX = env_int("JOB_SKILL_MAX", 24)
DOMAIN_POINTS = env_int("JOB_DOMAIN_POINTS", 2)         # per domain hit
DOMAIN_MAX = env_int("JOB_DOMAIN_MAX", 12)
EARLY_STAGE_POINTS = env_int("JOB_EARLY_STAGE_POINTS", 6)
EARLY_SOFT_POINTS = env_int("JOB_EARLY_STAGE_SOFT_POINTS", 3)
LATE_STAGE_PENALTY = -abs(env_int("JOB_LATE_STAGE_PENALTY", 6))


# ==========================================================================
# SCORING v2 — weighted 0-100 fit, replacing a sum of however-many-things-matched
#
# The old scorer summed independent hit counts. Measured against 1,826 real
# postings that produced three systematic distortions:
#
#   1. VERBOSITY PAID. corr(JD length, score) = +0.39; skills +0.41, domains
#      +0.40. Both were presence-counts over an 8,000-char blob, so a wordy JD
#      scored higher for no better reason.
#   2. SENIORITY HAD A DEAD ZONE. JUNIOR_SIGNALS stopped at "2+ years" and
#      SENIOR_SIGNALS began at "5+ years", so 3-4 years — the band closest to a
#      2-3 year candidate — scored 0, identical to a JD that stated nothing.
#      Half the corpus scored 0 on the single largest signal, and the keyword
#      verdict disagreed with the JD's own stated number on 29%.
#   3. TITLE POINTS COUNTED REDUNDANT STEMS. "Forward Deployed Engineer" scored
#      24 (two overlapping stems) and "Product Manager" 12, though both are
#      exact target matches. The gap measured the stem list, not the fit.
#
# v2 scores each dimension 0..1 and multiplies by a fixed weight, so the total
# is bounded, comparable across sources (an X/Grok lead carries ~900 chars, an
# ATS posting 8,000), and every dimension is legible on its own.
# ==========================================================================
# v3: the score answers ONE question — how well does this role's experience bar
# fit yours — and uses keyword overlap only to separate roles that fit it about
# equally. Everything else is a FILTER, not a score:
#
#   title    — title_is_target() already decided the posting is in lane. Paying
#              points for it again just raised the floor under every posting.
#   location — a personal preference. location_tier() keeps or drops; it does
#              not make a badly-fitting role look better.
#   stage    — same. A late-stage company is filtered, not discounted.
#
# Why: an 8+ year PM req at a big lab used to score 58, because title (25) plus
# skills (20) plus stage (6) built a ~51 floor no matter how badly the seniority
# fit — 4 out of 30 — actually matched. The floor was the bias.
WEIGHTS = {
    "seniority fit": env_int("JOB_W_SENIORITY", 65),
    "resume skills": env_int("JOB_W_SKILLS", 25),
    "your domains": env_int("JOB_W_DOMAINS", 10),
}

# An absent signal is not a bad signal. A JD that never states years should not
# score like one that wants ten of them, so unknown earns a neutral fraction and
# is additionally recorded as a miss.
UNKNOWN_FRACTION = 0.55

# Hits needed to saturate a dimension. Combined with the requirements-block
# restriction below this is what bounds the verbosity effect: past this many, a
# longer JD earns nothing more.
# Hits needed to saturate a dimension. Set high on purpose: 100 should be
# essentially unreachable, and a genuinely great match should read 75-90.
# Saturating at 6 skills meant any reasonably-worded posting maxed the keyword
# dimensions, so clearing the experience bar was enough to hit the ceiling and
# "perfect" stopped meaning anything. Twelve distinct resume skills inside the
# REQUIREMENTS block is rare; measured over 43 real in-lane postings this puts
# the best at 94, none above, and the median at 47.
SKILL_SATURATION = env_int("JOB_SKILL_SATURATION", 12)
DOMAIN_SATURATION = env_int("JOB_DOMAIN_SATURATION", 4)

# --- the requirements block -------------------------------------------------
# Scoring skills over the WHOLE posting rewards marketing copy. What matters is
# the block that states what they actually want. Found in 41/42 real postings,
# and far more uniform in length than the JD as a whole (median 2.8k vs 5.1k),
# which drops corr(JD length, skill hits) from +0.43 to +0.26.
_REQ_HEAD = re.compile(
    r"(what you.{0,12}(bring|have|ll need)|who you are|about you|qualifications|"
    r"requirements|we.{0,4}re looking for|you have|your (background|experience)|"
    r"minimum qualifications|basic qualifications|what we.{0,12}looking for|"
    r"you.{0,3}ll need)", re.I)
_REQ_STOP = re.compile(
    r"(benefits|perks|compensation|salary|equal opportunity|eeo|what we offer|"
    r"our values|why join|interview process)", re.I)


def requirements_block(content):
    """The part of a JD that states what they want, or the whole thing if we
    cannot find it (never empty — a failed match must not zero the score)."""
    c = content or ""
    m = _REQ_HEAD.search(c)
    if not m:
        return c
    tail = c[m.start():]
    stop = _REQ_STOP.search(tail, 200)
    block = tail[:stop.start()] if stop else tail[:4000]
    return block if len(block) > 250 else c


# How much a stated bar ABOVE your experience should cost. Deliberately a
# gradient and never a gate: a stated bar is soft in practice, especially at the
# seed-to-Series-B startups this is pointed at, and gating on it hid half the
# in-lane market at 2.5 years. One or two years over is the "apply anyway" band
# and barely costs; a five-year gap ranks low but stays visible.
# Gap between the stated bar and your years -> what the role is worth. Beyond a
# four-year gap it hits the floor: "8+ years" against three is not a long shot,
# it is a different job, and the score should read as such.
STRETCH_CURVE = [(0, 1.00), (1, 0.85), (2, 0.60), (3, 0.32), (4, 0.12)]
STRETCH_FLOOR = 0.02


def seniority_fraction(facts, content=""):
    """(fraction, explanation) for how well the stated bar fits your experience.

    Returns (None, reason) when the JD states nothing, so the caller can apply
    the neutral fraction rather than a zero.
    """
    req = facts.get("years_min")
    if req is None:
        return None, "no years-of-experience stated"
    # A bar in somebody else's field is not a seniority question at all: the
    # years asked for are years of a career you have not had.
    if facts.get("field_mismatch"):
        return FIELD_MISMATCH_FACTOR, (f"wants {facts.get('years')} in "
                                       f"{facts['field_mismatch']}, not product")
    mine = RESUME.get("years", 0)
    gap = req - mine
    if gap <= 0:
        return 1.0, f"wants {facts.get('years')}, you have {mine}"
    frac = STRETCH_FLOOR
    for limit, value in STRETCH_CURVE:
        if gap <= limit:
            frac = value
            break
    else:
        frac = STRETCH_FLOOR
    return frac, f"wants {facts.get('years')} vs your {mine}"


def _title_fraction(hits):
    """Any target-title match is a real match — the gate already decided the
    posting is in lane — so this is a narrow band, not a per-stem sum. The most
    SPECIFIC stem matched breaks ties, so "forward deployed product manager"
    edges out a bare "product manager" without doubling it.
    """
    if not hits:
        return 0.0
    words = max(len(h.split()) for h in hits)
    return min(1.0, 0.75 + 0.25 * (min(words, 4) - 1) / 3)


# Location matters, but not as "which metro". What matters is whether the role
# can be done from where you live. A San Francisco company hiring REMOTE is as
# workable from Brooklyn as a New York company hiring on-site; a San Francisco
# company hiring on-site is a house move. Ranking by metro put those two SF roles
# side by side and split the two workable ones apart.
#
# So the only geographic question the score asks is: would this require moving?
RELOCATION_FACTOR = float(os.environ.get("JOB_RELOCATION_FACTOR", "0.55"))

# Among roles you could actually take, your own metro still edges the rest —
# no commute, no timezone, you can meet people. That is a TIEBREAK, so it is a
# multiplier and not points: points would lift every NYC posting including the
# bad ones, which is exactly the floor this scoring was rebuilt to remove. A
# poorly-fitting NYC role stays poorly fitting (0.3 x 1.0), while between two
# strong ones the local one wins.
#
# The effect is deliberately small. "NYC on-site" and "SF company, remote"
# should rank close together, because both are jobs you can do from home.
HOME_METRO_FACTOR = 1.0
ELSEWHERE_FACTOR = float(os.environ.get("JOB_ELSEWHERE_FACTOR", "0.95"))


def location_factor(tier, work_mode):
    """What this role's geography multiplies its fit by.

    1.00  your metro — the full conjunction can only reach 100 here
    0.95  doable from home but not local (US-remote, a remote role elsewhere)
    0.55  you would have to move
    """
    if needs_relocation(tier, work_mode):
        return RELOCATION_FACTOR
    if not tier or tier == "nyc":
        return HOME_METRO_FACTOR
    return ELSEWHERE_FACTOR


def needs_relocation(tier, work_mode):
    """True if taking this role means living somewhere you don't.

    Your metro is always fine, whatever the work mode. Anywhere else is fine ONLY
    if the role is remote. Unknown work mode outside your metro is treated as
    on-site, which is what an unqualified city on a posting usually means.
    """
    if not tier or tier == "nyc":
        return False          # your metro, or geography unknown — never penalise
    if tier == "us-remote":
        return False
    return (work_mode or "onsite") != "remote"


def score_breakdown(title, content, tier=None, work_mode=None):
    """A 0-100 fit score AND the evidence behind it.

    The score is dominated by whether you clear the role's experience bar, read
    from the whole posting — the stated number when there is one, the JD's own
    seniority language when there is not. Keyword overlap is the tiebreak
    between roles that fit that bar about equally, never the thing that carries
    a badly-fitting role.

    Returns {"total", "parts": [(label, points, terms)], "misses": [(label, why)]}.
    Title, location and stage are absent on purpose: they are filters applied
    elsewhere, and scoring them put a floor under every posting that survived.
    """
    t, c = _norm(title), _norm(content)
    parts, misses = [], []

    def add(label, frac, terms):
        pts = int(round(WEIGHTS[label] * frac))
        if pts:
            parts.append((label, pts, terms))
        return pts

    # --- experience bar: the whole question, read from the whole posting.
    facts = screen_facts(content)
    frac, why = seniority_fraction(facts, c)
    if frac is None:
        frac, why = _seniority_from_prose(t, c)
    add("seniority fit", frac, [why])
    if frac < 1.0:
        misses.append(("seniority", why))

    # --- keywords, as the tiebreak — and SCALED BY the experience fit, not added
    # beside it. Additively, a role wanting 8 years still collected the full 35
    # keyword points and landed around 20; multiplied, it lands near zero, which
    # is the honest answer. Keyword overlap separates roles you could get; it
    # cannot carry one you are five years short of.
    req = _norm(requirements_block(content))
    skills_hit = _hits(_skill_re(), t + " " + req)
    if skills_hit:
        add("resume skills",
            min(1.0, len(skills_hit) / SKILL_SATURATION) * frac, skills_hit)
    else:
        misses.append(("skills", "no overlap with your resume skills"))

    domains_hit = _hits(_domain_re(), t + " " + c)
    if domains_hit:
        add("your domains",
            min(1.0, len(domains_hit) / DOMAIN_SATURATION) * frac, domains_hit)
    else:
        misses.append(("domains", "not in a domain you've worked in"))

    # --- would this mean moving? Applied as a scale over everything rather than
    # a subtraction, for the same reason keywords are: a role you would have to
    # relocate for is worth less across the board, not worth "the same minus a
    # fixed amount". Roles you can actually do keep their full score, so NYC
    # on-site and SF-remote land next to each other.
    geo = location_factor(tier, work_mode)
    if geo != 1.0:
        parts = [(l, int(round(p * geo)), t) for l, p, t in parts]
    if needs_relocation(tier, work_mode):
        misses.append(("location", "you'd have to move for this one"))

    return {"total": sum(p[1] for p in parts), "parts": parts, "misses": misses}


# Seniority when the posting states no number: the union of what the rest of the
# JD says. Weaker evidence than a stated bar, so it never reaches full marks.
def _seniority_from_prose(title, content):
    jr = [x for x in JUNIOR_SIGNALS if x in content]
    sr = [x for x in SENIOR_SIGNALS if x in content]
    senior_title = _reads_senior(_fold_punct(title))
    if senior_title:
        return 0.30, "the title itself reads senior"
    if jr and not sr:
        return 0.90, f"reads early-career ({jr[0]})"
    if sr and not jr:
        return 0.40, f"the posting leans senior ({sr[0]})"
    if jr and sr:
        return UNKNOWN_FRACTION, "the posting states two different bars"
    return UNKNOWN_FRACTION, "no experience bar stated"


def qualification_score(title, content):
    return score_breakdown(title, content)["total"]
