from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
import hashlib
import os
import re
import smtplib
from .settings import (
    EMAIL_PASS,
    EMAIL_TO,
    EMAIL_USER,
    SMTP_HOST,
    SMTP_PORT,
    SUBJECT_JOBS,
    env_flag,
)

def from_header():
    """The display name on the digest, so it doesn't look like mail from you.

    DIGEST_FROM_NAME sets it outright ("Job Radar", "Roles Bot", anything).
    Falling back to "Job Bot (@<handle>)" for compatibility with DIGEST_HANDLE.

    The ADDRESS cannot be changed here — SMTP will only send as the account you
    authenticated with, and Gmail rewrites a From: it has not verified. To send
    from a different address you have to add it in Gmail as "Send mail as" and
    verify it, then point EMAIL_USER at that account. The display name is the
    part you can freely choose, and it is what actually shows in an inbox list.
    """
    name = os.environ.get("DIGEST_FROM_NAME", "").strip()
    if not name:
        handle = os.environ.get("DIGEST_HANDLE", "").strip()
        if not handle:
            m = re.match(r"[A-Za-z]+", (EMAIL_USER or "").split("@")[0])
            handle = m.group(0).capitalize() if m else "you"
        name = f"Job Bot (@{handle})"
    return formataddr((name, EMAIL_USER))


def subject_for(template):
    """Fill a subject template. Only {date} is substituted; a template with no
    placeholder is used as-is, which is how "X Latest Hiring" stays static."""
    try:
        return template.format(date=datetime.now().strftime("%b %-d"))
    except (KeyError, IndexError):
        return template          # a stray brace shouldn't break the send


# Threading. Each digest is a reply to an imaginary first message that never
# existed but has a stable, made-up Message-ID — so every "Job Posting digest"
# references the same root and the mail client files them as one conversation,
# while the subject line keeps its date. Threading on References is what the
# standard actually specifies; the alternative is making every subject
# identical, which threads by accident and costs you the date.
#
# The root is derived from the recipient so two people running their own fork
# never collide, and is stable across runs because it is a hash, not a UUID.
THREAD_EMAILS = env_flag("JOB_THREAD_EMAILS", True)


def thread_root(kind):
    """A stable Message-ID for the conversation a digest belongs to."""
    seed = f"{kind}|{EMAIL_TO or EMAIL_USER or 'local'}"
    return f"<{hashlib.sha1(seed.encode()).hexdigest()[:24]}.digest@job-search-automation>"


def send_email(html_body, subject=None, thread="jobs"):
    if not (EMAIL_USER and EMAIL_PASS and EMAIL_TO):
        print("[info] email creds not set — printing digest:\n")
        print(re.sub(r"<[^>]+>", "", html_body))
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject or subject_for(SUBJECT_JOBS)
    msg["From"] = from_header()
    msg["To"] = EMAIL_TO
    if THREAD_EMAILS:
        root = thread_root(thread)
        msg["Message-ID"] = make_msgid(domain="job-search-automation")
        msg["In-Reply-To"] = root
        msg["References"] = root
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
        srv.starttls()
        srv.login(EMAIL_USER, EMAIL_PASS)
        srv.sendmail(EMAIL_USER, [a.strip() for a in EMAIL_TO.split(",")], msg.as_string())
    print(f"[ok] sent companies digest to {EMAIL_TO}")
