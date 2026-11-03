"""Reads job-alert emails (LinkedIn, Built In, Indeed...) over IMAP so Claude can pull roles out of them.

Gmail: turn on 2-step verification, create an app password, and put GMAIL_USER and
GMAIL_APP_PASSWORD in .env. Reads All Mail, Spam and Trash. Read-only: messages are fetched with BODY.PEEK and stay unread.
"""
from __future__ import annotations

import email
import email.header
import email.utils
import html
import imaplib
import os
import re
from datetime import datetime, timedelta
from email.message import Message

DEFAULT_SENDERS = [
    "jobalerts-noreply@linkedin.com",
    "jobs-noreply@linkedin.com",
    "builtin.com",
    "alert@indeed.com",
    "match.indeed.com",  # Indeed's job-match emails (donotreply@match.indeed.com)
    "glassdoor.com",
    "wellfound.com",
    "higheredjobs.com",
    "ziprecruiter.com",
    "dice.com",
]


def html_to_text(s: str) -> str:
    """Keeps link targets ("Title (url)"), since the job URL is the point of an alert."""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r'(?is)<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', lambda m: f" {m.group(2)} ({m.group(1)}) ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    s = re.sub(r"[ \t ]+", " ", s)
    return "\n".join(line.strip() for line in s.splitlines() if line.strip())


def message_text(msg: Message) -> str:
    plain, rich = None, None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html") or part.get_filename():
            continue
        body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        if ctype == "text/html" and rich is None:
            rich = html_to_text(body)
        elif ctype == "text/plain" and plain is None:
            plain = body
    # HTML keeps the real job links; plain-text alerts often hide them behind tracking redirects.
    return (rich or plain or "").strip()


FOLDER_FLAGS = ("\\All", "\\Junk", "\\Trash")  # All Mail leaves out Spam and Trash, so read those too


def folders(m) -> list[str]:
    """All Mail, Spam and Trash by their special-use flags, so it works in any Gmail language ("Bin", "Papierkorb")."""
    found = {}
    _, rows = m.list()
    for row in rows or []:
        line = row.decode(errors="replace") if isinstance(row, bytes) else str(row)
        mt = re.match(r'\((?P<flags>[^)]*)\)\s+"[^"]*"\s+(?P<name>.+)$', line)
        if not mt:
            continue
        for f in FOLDER_FLAGS:
            if f.lower() in mt["flags"].lower():
                found[f] = mt["name"].strip()
    return [found[f] for f in FOLDER_FLAGS if f in found] or ['"[Gmail]/All Mail"', '"[Gmail]/Spam"', '"[Gmail]/Trash"']


def messages(days: int, senders: list[str], mailbox: str | None = None, gmail_queries: list[str] = ()):
    """Yields (Message-ID, from name, from address, subject, datetime, text, raw bytes) for mail from
    any sender substring, or matching any Gmail search query, in the last `days` days. Searches All Mail,
    Spam and Trash (or just `mailbox`). Read-only: each folder is opened with readonly=True and bodies are
    fetched with BODY.PEEK, so nothing is marked read, moved or deleted."""
    user, pw = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if not (user and pw):
        raise RuntimeError("set GMAIL_USER and GMAIL_APP_PASSWORD in .env")
    since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
    seen = set()
    with imaplib.IMAP4_SSL("imap.gmail.com") as m:
        m.login(user, pw)
        boxes = [mailbox] if mailbox else folders(m)
        for box in boxes:
            if m.select(box, readonly=True)[0] != "OK":
                if box == boxes[0]:
                    m.select("INBOX", readonly=True)
                else:
                    continue
            ids = set()
            for sender in senders:
                _, data = m.search(None, "SINCE", since, "FROM", f'"{sender}"')
                ids.update(data[0].split())
            for q in gmail_queries:
                # X-GM-RAW takes Gmail's own search syntax (newer_than:, OR, -category:).
                _, data = m.search(None, "X-GM-RAW", '"' + f"in:anywhere newer_than:{days}d {q}".replace('"', '\\"') + '"')
                ids.update(data[0].split())
            for i in sorted(ids, key=int):
                _, data = m.fetch(i, "(BODY.PEEK[])")
                msg = email.message_from_bytes(data[0][1])
                mid = msg.get("Message-ID") or f"imap:{box}:{i.decode()}"
                if mid in seen:
                    continue
                seen.add(mid)
                name, addr = email.utils.parseaddr(msg.get("From", ""))
                try:
                    when = email.utils.parsedate_to_datetime(msg.get("Date"))
                except (TypeError, ValueError):
                    when = datetime.now()
                subject = str(email.header.make_header(email.header.decode_header(msg.get("Subject", ""))))
                yield (mid, name, addr, subject, when, message_text(msg), data[0][1])


def fetch(days: int = 1, senders: list[str] | None = None, max_chars: int = 8000) -> list[dict]:
    return [{"from": addr, "subject": subject, "date": when.isoformat(), "text": text[:max_chars]}
            for _, _, addr, subject, when, text, _ in messages(days, senders or DEFAULT_SENDERS)]


def sent_mail(days: int):
    """Yields (Message-ID, datetime, [to/cc addresses], subject, In-Reply-To) for mail you sent in the last
    `days` days. Headers only, read-only: used to tell which emails you've already answered."""
    user, pw = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if not (user and pw):
        raise RuntimeError("set GMAIL_USER and GMAIL_APP_PASSWORD in .env")
    with imaplib.IMAP4_SSL("imap.gmail.com") as m:
        m.login(user, pw)
        m.select('"[Gmail]/All Mail"', readonly=True)
        _, data = m.search(None, "X-GM-RAW", f'"in:sent newer_than:{days}d"')
        for i in data[0].split():
            _, d = m.fetch(i, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID DATE TO CC SUBJECT IN-REPLY-TO)])")
            msg = email.message_from_bytes(d[0][1])
            try:
                when = email.utils.parsedate_to_datetime(msg.get("Date"))
            except (TypeError, ValueError):
                continue
            to = [a.lower() for _, a in email.utils.getaddresses(msg.get_all("To", []) + msg.get_all("Cc", [])) if a]
            subject = str(email.header.make_header(email.header.decode_header(msg.get("Subject", ""))))
            yield (msg.get("Message-ID") or f"imap-sent:{i.decode()}", when, to, subject, (msg.get("In-Reply-To") or "").strip())
