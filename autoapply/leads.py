"""Leads: every role that job-alert emails (LinkedIn, Indeed and others) mention, kept so you can sort and
prioritize them later. Only a couple a day get tailored; the rest wait here instead of being forgotten.

Parsing is plain Python on the alert's text part: LinkedIn and Indeed list each job as a few short lines
(title, company, location, sometimes salary) ending with "View job: <url>". No model is needed.
"""
from __future__ import annotations

import email
import re
from datetime import datetime

from . import alerts

NOISE = re.compile(r"actively hiring|connection|alumni|apply with|easy apply|promoted|your job alert|^hi\b|^hello\b|"
                   r"work setting:|^new\b.*\bjobs?\b|matches your|be an early applicant|^view job|^apply now|^\W*$", re.I)
SEPARATOR = re.compile(r"^-{5,}$|^_{5,}$|^={5,}$")
VIEW = re.compile(r"^(?:view job|view this job|see job|apply now)\s*:?\s*(https?://\S+)", re.I)
SCHEMA = """
create table if not exists leads (
  key text primary key,            -- company|title, normalised: the same role from LinkedIn and Indeed is one lead
  title text, company text, location text, salary text, url text, source text,
  first_seen text, last_seen text, seen integer default 1,
  score real default 0, status text default 'new', job_id text
);
"""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def key(company: str, title: str) -> str:
    return f"{_norm(company)}|{_norm(title)}"


def plain_text(raw: bytes) -> str:
    msg = email.message_from_bytes(raw)
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_type() == "text/plain" and not part.get_filename():
            return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
    return ""


def source_of(addr: str) -> str:
    a = (addr or "").lower()
    for name in ("linkedin", "indeed", "glassdoor", "ziprecruiter", "builtin", "wellfound", "dice", "higheredjobs"):
        if name in a:
            return {"builtin": "Built In", "ziprecruiter": "ZipRecruiter", "higheredjobs": "HigherEdJobs"}.get(name, name.capitalize())
    return a.split("@")[-1] or "email"


def parse(text: str) -> list[dict]:
    """Jobs from an alert's plain text: the short lines just above each "View job: <url>"."""
    lines = [l.strip() for l in (text or "").splitlines()]
    out = []
    for i, line in enumerate(lines):
        m = VIEW.match(line)
        if not m:
            continue
        block = []
        for prev in reversed(lines[max(0, i - 12):i]):
            if SEPARATOR.match(prev) or VIEW.match(prev):
                break
            block.insert(0, prev)
        salary = next((re.sub(r"(?i)^salary:\s*", "", b) for b in block if re.match(r"(?i)^salary:|^\$\d", b)), "")
        keep = [b for b in block if b and len(b) <= 110 and not NOISE.search(b) and not re.match(r"(?i)^salary:|^\$\d", b)]
        if len(keep) < 2:
            continue
        title, company = keep[0], keep[1]
        location = keep[2] if len(keep) > 2 else ""
        url = m.group(1).split("?")[0] if "linkedin.com" in m.group(1) else m.group(1)
        out.append({"title": title[:200], "company": company[:120], "location": location[:120],
                    "salary": salary[:80], "url": url[:1000]})
    return out


def ensure(db):
    db.conn.executescript(SCHEMA)


def save(db, found: list[dict], source: str, when: datetime, search: dict, terms: set) -> int:
    """Adds new leads and bumps ones seen again. Returns how many were new."""
    from .rank import score
    ensure(db)
    new = 0
    at = when.strftime("%Y-%m-%d %H:%M")
    for j in found:
        k = key(j["company"], j["title"])
        if not k.strip("|") or "|" not in k or not k.split("|")[0] or not k.split("|")[1]:
            continue
        row = db.conn.execute("select seen, source from leads where key = ?", (k,)).fetchone()
        if row:
            srcs = sorted(set(filter(None, (row["source"] or "").split(", "))) | {source})
            db.conn.execute("update leads set seen = seen + 1, last_seen = max(last_seen, ?), source = ?, "
                            "salary = coalesce(nullif(salary, ''), ?) where key = ?", (at, ", ".join(srcs), j["salary"], k))
            continue
        s = score({"title": j["title"], "description": "", "found_at": at}, search, terms, now=when)
        db.conn.execute("insert into leads (key, title, company, location, salary, url, source, first_seen, last_seen, score) "
                        "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (k, j["title"], j["company"], j["location"], j["salary"], j["url"], source, at, at, round(s, 2)))
        new += 1
    db.conn.commit()
    return new


def link_tracked(db):
    """A lead whose role is now tracked (added from the company's site) points at that job."""
    ensure(db)
    by_key = {key(j["company"], j["title"]): j for j in db.all() if j.get("company") and j.get("title")}
    for r in db.conn.execute("select key from leads where job_id is null").fetchall():
        j = by_key.get(r["key"])
        if j:
            st = {"submitted": "applied", "filled": "ready", "skipped": "dismissed"}.get(j["status"], "tracked")
            db.conn.execute("update leads set job_id = ?, status = case when status = 'starred' and ? = 'tracked' "
                            "then 'starred' else ? end where key = ?", (j["id"], st, st, r["key"]))
    db.conn.commit()


def sync(db, settings, days: int = 3) -> dict:
    """Reads alert emails (All Mail, Spam, Trash; read-only) and files every role in them as a lead."""
    from .rank import profile_terms
    ensure(db)
    search, terms = settings.raw.get("search") or {}, profile_terms(settings.root)
    senders = settings.raw.get("alert_senders") or alerts.DEFAULT_SENDERS
    emails = jobs = new = 0
    for _, _, addr, _, when, text, raw in alerts.messages(days, senders):
        found = parse(plain_text(raw)) or parse(text)
        emails += 1
        jobs += len(found)
        new += save(db, found, source_of(addr), when.replace(tzinfo=None) if when.tzinfo else when, search, terms)
    link_tracked(db)
    return {"emails": emails, "roles": jobs, "new": new}


def rows(db, status: str = "") -> list[dict]:
    ensure(db)
    q, args = "select * from leads", ()
    if status:
        q, args = q + " where status = ?", (status,)
    return [dict(r) for r in db.conn.execute(q + " order by (status = 'starred') desc, score desc, last_seen desc", args)]


def set_status(db, k: str, status: str):
    if status not in ("new", "starred", "dismissed"):
        raise ValueError(status)
    ensure(db)
    db.conn.execute("update leads set status = ? where key = ?", (status, k))
    db.conn.commit()


def starred_for_lookup(db, limit: int = 8) -> list[dict]:
    """Starred leads not tracked yet, in the shape the alerts step reads (data/daily/alert-roles.json)."""
    link_tracked(db)
    return [{"company": r["company"], "title": r["title"], "link": r["url"], "why": "you starred it"}
            for r in db.conn.execute("select * from leads where status = 'starred' and job_id is null "
                                     "order by score desc limit ?", (limit,))]
