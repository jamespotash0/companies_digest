import html
import json
import re
from .filters import company_blurb, fit_sentence
from .location import BEST_MATCH_MIN
from .net import age_label
from .scoring import needs_relocation
from .settings import COMPANY_SIZE, RESUME
from .stage import funding_label

# --------------------------- render + send
# --------------------------- email (Apple-flavoured: hairlines, generous
# whitespace, one accent colour, system font stack). Every style is inline —
# Gmail strips <style> blocks — and the layout is tables so it survives Outlook.
FONT = ("-apple-system,BlinkMacSystemFont,'SF Pro Text','Helvetica Neue',"
        "Helvetica,Arial,sans-serif")
INK, INK2, INK3 = "#1d1d1f", "#6e6e73", "#86868b"   # primary / secondary / tertiary
HAIRLINE, CANVAS, CARD = "#e5e5ea", "#f5f5f7", "#ffffff"
ACCENT, GOOD, WARM = "#0066cc", "#248a3d", "#a2600d"


# Every chip carries a verdict, not just a fact. Green: this suits you. Grey:
# neutral, nothing to say. Amber: partly against you. Red: against you. The point
# is that the row can be read at a glance without reading any of the words.
TONES = {
    "good":    ("#1c7c3f", "#e9f6ed", "#c3e5cd"),
    "neutral": (INK2,      CANVAS,    HAIRLINE),
    "warn":    ("#8a5a00", "#fdf4e3", "#f0dcb4"),
    "bad":     ("#a3231b", "#fdecea", "#f5c9c4"),
}


def _chip(text, tone="neutral", bold=False):
    color, bg, border = TONES.get(tone, TONES["neutral"])
    return (f"<span style=\"display:inline-block;padding:3px 9px;margin:0 6px 6px 0;"
            f"border:1px solid {border};border-radius:7px;background:{bg};"
            f"color:{color};font-size:12px;line-height:16px;"
            f"font-weight:{600 if bold else 500};white-space:nowrap\">{text}</span>")


def match_line(item):
    """Removed. It printed the seniority gap a third time, after the chip and the
    fit sentence had both already said it. Kept as a no-op so callers that still
    reference it do not need editing."""
    return ""


def context_block(item):
    """The extra colour an X/Grok lead carries: who posted, what they said, and
    where to apply. A tweet is a lead, not a job page — without the quote and the
    poster you cannot tell whether it is worth a reply."""
    if not (item.get("note") or item.get("poster")):
        return ""
    rows = []
    who = " ".join(x for x in (item.get("poster", ""), item.get("poster_title", "")) if x)
    if who:
        # "summarized from" not "posted by": what follows is Grok's reading of
        # the post, not the poster's own words. See the note below.
        rows.append(f"<div style=\"font-size:12px;color:{INK3};margin:0 0 6px\">"
                    f"summarized from a post by {html.escape(who)}</div>")
    # The `quote` field is deliberately NOT rendered as a quotation.
    # tests/verify_grok_quotes.py fetched the source tweets across two live runs:
    # 2 of 13 and 2 of 12 quotes did not appear in the tweet at all — one was
    # garbled ("PM grass counts with us"), one was a synthesized headline
    # ("Hiring: Deployment Strategist — Assembled"). At a ~15% fabrication rate,
    # printing it in quotation marks next to a real person's handle would put
    # words in their mouth in an email that invites you to reply to them. The
    # field still feeds the scoring blob, where being approximate costs nothing.
    if item.get("note"):
        rows.append(f"<div style=\"font-size:13px;line-height:20px;color:{INK2}\">"
                    f"{html.escape(item['note'])}</div>")
    links = []
    if item.get("apply_url"):
        links.append(f"<a href=\"{html.escape(item['apply_url'])}\" "
                     f"style=\"color:{ACCENT};text-decoration:none\">Apply \u2192</a>")
    if item.get("url"):
        links.append(f"<a href=\"{html.escape(item['url'])}\" "
                     f"style=\"color:{ACCENT};text-decoration:none\">See the post \u2192</a>")
    if links:
        rows.append(f"<div style=\"margin:8px 0 0;font-size:13px\">"
                    + "&nbsp;&nbsp;\u00b7&nbsp;&nbsp;".join(links) + "</div>")
    return (f"<div style=\"font-family:{FONT};margin:10px 0 0;padding:12px 14px;"
            f"background:{CANVAS};border-radius:10px\">" + "".join(rows) + "</div>")


# Board fetchers set company=<ats token> ("david-ai", "usenourish"), which reads
# like a database key in the email. Not a rename — just presentation.
_TOKEN_UPPER = {"ai", "ml", "hq", "io", "us", "gtm", "api", "crm", "vc"}


def pretty_company(name):
    n = (name or "").strip()
    if not n or " " in n:
        return n                                   # already a real display name
    words = [w for w in re.split(r"[-_]+", n) if w]
    return " ".join(w.upper() if w.lower() in _TOKEN_UPPER else w.capitalize()
                    for w in words)


def fit_word(q):
    """The score as something you can act on."""
    # Calibrated to the real distribution, where the best posting scores ~94 and
    # the median ~47. "Strong fit" is meant to be uncommon.
    if q >= 88:
        return "Strong fit"
    if q >= BEST_MATCH_MIN:
        return "Good fit"
    if q >= 50:
        return "Worth a look"
    return "A stretch"


def card(item, dim=False):
    esc = html.escape
    title_size = "15px" if dim else "17px"
    facts = item.get("facts") or {}
    tier = item.get("loc_tier") or ""
    chips = []

    # --- where. ONE chip, not two. The posting's own location string carries the
    # detail; the tier is only appended when it adds something the string does
    # not already say ("Remote" -> "Remote · US"), which is what stopped the row
    # reading "Remote" followed immediately by "US-remote".
    loc = (item.get("location") or "").strip()
    tier_word = {"nyc": "NYC", "secondary": "SF/Bay", "us-remote": "US",
                 "us-other": "US"}.get(tier, "")
    loc_text = esc(loc) if loc else esc(tier_word or "\u2014")
    if loc and tier_word and tier_word.lower() not in loc.lower() \
            and not (tier == "nyc" and "new york" in loc.lower()):
        loc_text += f" \u00b7 {tier_word}"
    # Coloured by whether you could actually take it, not by which metro it is
    # in. A remote role at an SF company is not amber — you'd do it from home.
    relocate = needs_relocation(tier, item.get("work_mode"))
    if relocate:
        loc_text += " \u00b7 relocate"
    chips.append(_chip(loc_text,
                       "warn" if relocate else "good" if tier == "nyc" else "neutral"))

    # --- the experience bar. Stated ONCE, with the comparison built in, so the
    # fit sentence below has nothing left to repeat.
    if facts.get("years"):
        req = facts.get("years_min")
        mine = RESUME.get("years", 0)
        gap = (req - mine) if req is not None else 0
        if facts.get("field_mismatch"):
            # The number is beside the point: they want years of a different
            # career. Say which one, so the card explains itself.
            chips.append(_chip(f"{esc(facts['years'])} in "
                               f"{esc(facts['field_mismatch'])}", "bad", bold=True))
        else:
            chips.append(_chip(f"{esc(facts['years'])} \u00b7 you have {mine:g}",
                               "good" if gap <= 0 else "warn" if gap <= 2 else "bad",
                               bold=gap > 2))

    # --- how you'd work it. One chip: the parsed work model when the JD states
    # one, else what the location implies. These used to be two separate chips
    # saying the same thing ("on-site" and "Onsite").
    mode = facts.get("work_model") or {
        "onsite": "Onsite", "hybrid": "Hybrid", "remote": "Remote"}.get(
            item.get("work_mode") or "", "")
    # "Remote" is only worth a chip when the location line did NOT already say
    # so. A remote-listed role captioned "Remote" twice is the same redundancy
    # as "Remote" followed by "US-remote".
    if mode.lower().startswith("remote") and "remote" in (loc or "").lower():
        mode = ""
    if mode:
        # Never amber on its own: the location chip already carries the "you'd
        # have to move" verdict, and colouring both said it twice.
        chips.append(_chip(esc(mode)))

    if facts.get("sponsorship"):
        chips.append(_chip(esc(facts["sponsorship"]),
                           "bad" if facts["sponsorship"].startswith("No") else "good"))

    sz = COMPANY_SIZE.get((item.get("company") or "").lower())
    if sz:
        chips.append(_chip(f"~{sz} people", "good" if sz <= 200 else "neutral"))


    # 🚀 marks a posting at a company the funding digest saw raise recently — the
    # reason the req exists, and the best cold-outreach hook you get.
    if item.get("funding"):
        chips.append(_chip("\U0001F680 " + esc(funding_label(item["funding"])),
                           "good", bold=True))
    elif item.get("round"):
        chips.append(_chip(esc(item["round"])))

    facts_row = ""
    # What the company does, then what the role asks for. Leads skip it: their
    # content is a summary of a hiring post, not the company's own words.
    about = "" if item.get("lead") else company_blurb(item.get("company"), item.get("content"))
    about_row = (f"<div style=\"font-family:{FONT};margin:11px 0 0;font-size:13px;"
                 f"line-height:20px;color:{INK2}\">{esc(about)}</div>") if about else ""
    fit = fit_sentence(item)
    fit_row = (f"<div style=\"font-family:{FONT};margin:11px 0 0;font-size:13px;"
               f"line-height:20px;color:{INK}\">{fit}</div>") if fit else ""

    comp = esc(pretty_company(item.get("company"))) or "\u2014"
    # Pay sits with the company name, where you look first, not in the chip row.
    pay = " + ".join(esc(facts[k]) for k in ("comp", "equity") if facts.get(k))
    if pay:
        comp += f"&nbsp;&nbsp;\u00b7&nbsp;&nbsp;<span style=\"color:{INK}\">{pay}</span>"

    # Quiet footer: when + where it came from on the left, the link on the right.
    age = age_label(item.get("posted_ts"))
    fresh = age in ("today", "1d ago")
    meta = " \u00b7 ".join(x for x in (
        f"<span style=\"color:{GOOD};font-weight:600\">posted {age}</span>" if fresh
        else (f"posted {age}" if age else ""),
        f"via {esc(item['source'])}" if item.get("source") else "") if x)
    footer = (
        f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" width=\"100%\" "
        f"style=\"margin:12px 0 0;border-top:1px solid {HAIRLINE}\"><tr>"
        f"<td style=\"font-family:{FONT};padding:10px 0 0;font-size:12px;color:{INK3}\">{meta}</td>"
        f"<td align=\"right\" style=\"font-family:{FONT};padding:10px 0 0;font-size:13px;"
        f"font-weight:600;white-space:nowrap\">"
        # A lead's own block already links the post, so no second link here.
        + ("" if item.get("lead") else
           f"<a href=\"{esc(item['url'])}\" style=\"color:{ACCENT};text-decoration:none\">"
           f"View role \u2192</a>")
        + "</td></tr></table>")
    # A word, not a number. The number is a ranking device for the code; what you
    # need to know is whether you clear this role's bar. Leads get no verdict at
    # all — see the note in main(): there is not enough in a hiring post to
    # justify one, and a confident-looking label on a guess is worse than none.
    lead = item.get("lead")
    score_text = "" if lead else fit_word(item["q"])
    strong = (not lead) and item["q"] >= BEST_MATCH_MIN
    score_color = GOOD if strong else INK3
    score_bg = "#eaf6ee" if strong else CANVAS
    return (
        f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" width=\"100%\" "
        f"style=\"background:{CARD};border:1px solid {TONES['good'][2] if strong else HAIRLINE};"
        f"border-radius:14px;margin:0 0 10px\"><tr>"
        f"<td style=\"padding:16px 18px\">"
        f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" width=\"100%\"><tr>"
        f"<td valign=\"top\" style=\"font-family:{FONT}\">"
        f"<a href=\"{esc(item['url'])}\" style=\"color:{INK};text-decoration:none;"
        f"font-size:{title_size};font-weight:600;line-height:22px\">"
        f"{esc(item['title'] or 'Hiring')}</a>"
        f"<div style=\"margin:3px 0 9px;font-size:13px;color:{INK2}\">"
        f"{comp}</div>"
        f"<div style=\"margin:0 0 -6px\">{''.join(chips)}</div>"
        f"{facts_row}"
        f"</td>"
        + (f"<td valign=\"top\" width=\"86\" align=\"right\" style=\"font-family:{FONT}\">"
           f"<span style=\"display:inline-block;padding:4px 10px;"
           f"border-radius:9px;background:{score_bg};color:{score_color};"
           f"font-size:12px;font-weight:600;white-space:nowrap\">{score_text}</span>"
           f"</td>" if score_text else "")
        + f"</tr></table>"
        f"{about_row}"
        f"{fit_row}"
        f"{context_block(item)}"
        f"{match_line(item)}"
        f"{footer}"
        f"</td></tr></table>"
    )


def section(label, sub, items, dim=False):
    if not items:
        return ""
    return (
        f"<div style=\"font-family:{FONT};margin:26px 0 12px\">"
        f"<div style=\"font-size:13px;font-weight:600;color:{INK};"
        f"letter-spacing:.02em\">{label}"
        f"<span style=\"color:{INK3};font-weight:400\">&nbsp;&nbsp;{sub}</span></div>"
        f"</div>" + "".join(card(i, dim=dim) for i in items)
    )


def scoring_footer():
    """Removed at the owner's request. It explained the ranking machinery, which
    is not something you act on when reading job postings."""
    return ""


def _shell(inner):
    return (
        f"<div style=\"background:{CANVAS};padding:28px 0;margin:0\">"
        f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" width=\"100%\" "
        f"style=\"background:{CANVAS}\"><tr><td align=\"center\" style=\"padding:0 16px\">"
        f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" width=\"640\" "
        f"style=\"width:100%;max-width:640px\"><tr><td>{inner}</td></tr></table>"
        f"</td></tr></table></div>")


def discovery_banner():
    """A one-line status of today's discovery step, read from the breadcrumb
    discover.py leaves. Green when it added companies, grey when it ran but found
    nothing new, red when it failed — so you can tell at a glance that the loop
    that feeds the companies digest is alive. Empty string if discovery didn't run."""
    try:
        with open("discover_status.json") as f:
            st = json.load(f)
    except Exception:
        return ""
    total = st.get("watchlist_total", "?")
    if not st.get("ok"):
        tone = "bad"
        msg = f"Discovery failed: {html.escape(str(st.get('error') or 'unknown'))}"
    elif st.get("added"):
        # added_names are "ashby:david-ai" — drop the provider, show "David AI".
        names = ", ".join(pretty_company(n.split(":")[-1])
                          for n in st.get("added_names", [])[:8])
        tone = "good"
        msg = f"Discovery added {st.get('added')} companies: {html.escape(names)}"
    else:
        tone = "neutral"
        msg = f"Discovery checked {st.get('probed', 0)} companies, none new"
    color, bg, border = TONES[tone]
    return (f"<div style=\"font-family:{FONT};font-size:12px;line-height:17px;color:{color};"
            f"background:{bg};border:1px solid {border};border-radius:10px;"
            f"padding:8px 12px;margin:16px 0 0\">"
            f"{msg} \u00b7 watching {total} boards</div>")


def build_html(items, title="Companies digest", banner=""):
    header = (
        f"<div style=\"font-family:{FONT}\">"
        f"<div style=\"font-size:26px;font-weight:600;color:{INK};letter-spacing:-.02em\">"
        f"{html.escape(title)}</div>"
        f"</div>") + banner
    if not items:
        return _shell(header + (
            f"<div style=\"font-family:{FONT};margin:22px 0 0;padding:20px 18px;"
            f"background:{CARD};border:1px solid {HAIRLINE};border-radius:14px;"
            f"font-size:14px;color:{INK2}\">Nothing new this run.</div>"))

    # Leads are not ranked, so they are not split into match tiers either.
    if items and all(i.get("lead") for i in items):
        summary = (f"{len(items)} hiring post{'' if len(items) == 1 else 's'}, "
                   f"newest first")
        header += (f"<div style=\"font-family:{FONT};margin:9px 0 0;font-size:13px;"
                   f"color:{INK2}\">{summary}</div>")
        return _shell(header
                      + "<div style=\"height:14px\"></div>"
                      + "".join(card(i) for i in items))

    best = [i for i in items if i["q"] >= BEST_MATCH_MIN]
    rest = [i for i in items if i["q"] < BEST_MATCH_MIN]
    summary = (f"{len(items)} new posting{'' if len(items) == 1 else 's'}"
               + (f" \u00b7 {len(best)} strong match{'' if len(best) == 1 else 'es'}"
                  if best else ""))
    header += (f"<div style=\"font-family:{FONT};margin:9px 0 0;font-size:13px;"
               f"color:{INK2}\">{summary}</div>")
    return _shell(
        header
        + section("Best matches", "", best)
        + section("Worth a look", "a fit, with something that doesn't line up",
                  rest, dim=True)
        + scoring_footer())
