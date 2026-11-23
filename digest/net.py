from datetime import datetime, timezone
from watchlist import open_json
import html
import json
import re
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (companies-digest)"}


def get_json(url, timeout=25):
    return open_json(urllib.request.Request(url, headers=UA), timeout)


def post_json(url, payload, timeout=25):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={**UA, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST")
    return open_json(req, timeout)


def strip_html(t):
    """Markup -> plain text.

    Unescape FIRST, then strip. Greenhouse (and Ashby's `description`) return the
    JD as HTML-escaped markup — `&lt;li&gt;5+ years&lt;/li&gt;` — so stripping
    first matches no tags at all, and the unescape then turns the entities back
    into visible `<li>` litter in the text we score and quote. Two passes,
    because unescaping can itself reveal a tag."""
    t = html.unescape(t or "")
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    return re.sub(r"[ \t\xa0]+", " ", t).strip()


def to_ts(value):
    """Best-effort parse of a date/timestamp from any source -> unix seconds, or
    None if unknown. Handles ISO-8601 strings and epoch seconds/milliseconds."""
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        v = float(value)
        return v / 1000.0 if v > 1e12 else v          # milliseconds -> seconds
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)       # naive -> assume UTC
        return dt.timestamp()
    except Exception:
        return None


def age_label(ts):
    if not ts:
        return ""
    days = (datetime.now(timezone.utc).timestamp() - ts) / 86400.0
    if days < 1:
        return "today"
    if days < 2:
        return "1d ago"
    return f"{int(days)}d ago"
