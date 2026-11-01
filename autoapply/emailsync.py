"""Turns ATS and recruiter emails into events (confirmed, interview, rejected, offer, recruiter) and
files a copy of each one under data/proof/<week>/ so there is a record if a benefits office asks."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from . import alerts
from .db import DB

ATS_SENDERS = [
    "greenhouse.io", "greenhouse-mail.io", "lever.co", "ashbyhq.com", "myworkday.com", "myworkdayjobs.com",
    "rippling.com", "workablemail.com", "smartrecruiters.com", "kula.ai", "clearcompany.com", "eightfold.ai",
    "icims.com", "jobvite.com", "bamboohr.com",
]

# Checked in order: a rejection often also says "thank you for applying".
RULES = [
    ("offer", r"pleased to (extend|offer)|offer letter|formal offer"),
    ("rejected", r"unfortunately|not (to )?(be )?mov(e|ing) forward|other candidates|decided to (pursue|proceed)"
                 r"|not (been )?selected|position (has been|was) filled|no longer (being )?consider|won.t be moving"
                 r"|move forward with (another|other)|another candidate|regret to inform|decided not to"
                 r"|will not be (moving|proceeding)|not (be )?(a|the right) (match|fit) at this time"),
    ("interview", r"schedule (a|an|your|time)|interview (with|invitation|availability)|invite you to"
                  r"|availability for (a|an)|next step(s)? (is|in our|would be)|phone screen|recruiter (call|screen)"),
    ("confirmed", r"thank(s| you) for (applying|your application|your interest)|application (was |has been )?"
                  r"(received|submitted)|we('ve| have) received your application|received your application"),
]
RANK = {"confirmed": 1, "interview": 2, "rejected": 3, "offer": 4}

# Recruiter outreach arrives from anyone, so it's found by Gmail search rather than by sender.
RECRUITER_QUERIES = [
    '-category:promotions -category:social -from:jobalerts-noreply@linkedin.com '
    '(recruiter OR "talent acquisition" OR "right to represent" OR RTR OR "your background" '
    'OR "your profile" OR "your experience" OR "InMail" OR staffing) -subject:"Job search brief"',
]
RECRUITER = re.compile(
    r"recruit|talent acquisition|staffing|right to represent|\brtr\b|inmail|your (background|profile|experience)"
    r"|(great|exciting|new) (opportunity|role)|reach(ing)? out|open to (new )?(roles|opportunities)", re.I)
NOT_RECRUITER = re.compile(r"newsletter|unsubscribe from this list|webinar|job alert|jobs you may be interested"
                           r"|proxy ?vote|shareholder|important information|account statement|linkedin premium|premium (free )?trial", re.I)
# Account emails from an ATS that aren't news about the application.
NOT_STATUS = re.compile(r"sign-?in link|verification code|verify your (email|account)|reset your password"
                        r"|security code|confirm you.re a human", re.I)


# Recruiters write as people. Alerts, banks and marketing come from these.
BULK_SENDER = re.compile(r"no-?reply|donotreply|do-not-reply|alert|notification|newsletter|marketing|news@|info@"
                         r"|indeed|glassdoor|ziprecruiter|dice\.com|monster\.com|wellfound|builtin"
                         r"|americanexpress|aexp|bank|chase\.com|fidelity|vanguard|schwab|proxyvote|paypal|venmo", re.I)
# LinkedIn relays real recruiter messages (InMail, messages) but everything else from it is bulk.
LINKEDIN_PERSON = re.compile(r"inmail|hit-reply|messaging|messages-noreply", re.I)


def is_recruiter(name: str, addr: str, subject: str, text: str) -> bool:
    a = (addr or "").lower()
    if "linkedin.com" in a:
        if not LINKEDIN_PERSON.search(a) or re.search(r"premium|job alert|jobs? (for|you)", subject, re.I):
            return False
    elif BULK_SENDER.search(a) or BULK_SENDER.search(name or ""):
        return False
    blob = f"{name} {subject}\n{text[:3000]}"
    return bool(RECRUITER.search(blob)) and not NOT_RECRUITER.search(f"{subject}\n{text[:1500]}")


def week_dir(proof_root: Path, when: datetime) -> Path:
    from .stats import week_start
    return proof_root / week_start(when.date()).isoformat()


def save_proof(proof_root: Path | None, when: datetime, label: str, kind: str, raw: bytes, text: str, headers: str) -> str | None:
    """Writes the original .eml plus a readable .txt; returns the .eml path."""
    if not (proof_root and raw):
        return None
    d = week_dir(proof_root, when)
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{when:%Y-%m-%d_%H%M}_{re.sub(r'[^A-Za-z0-9]+', '-', label).strip('-')[:40] or 'unknown'}_{kind}"
    (d / f"{stem}.eml").write_bytes(raw)
    (d / f"{stem}.txt").write_text(headers + "\n\n" + text)
    return str(d / f"{stem}.eml")


def classify(subject: str, text: str) -> str | None:
    blob = f"{subject}\n{text[:4000]}"
    if NOT_STATUS.search(subject):
        return None
    for kind, pat in RULES:
        if re.search(pat, blob, re.I):
            return kind
    return None


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def match_job(jobs: list[dict], name: str, addr: str, subject: str, text: str) -> dict | None:
    """The applied job whose company shows up in the sender, subject or body (longest name wins)."""
    hay = _key(f"{name} {addr} {subject} {text[:3000]}")
    best = None
    for j in jobs:
        k = _key(j["company"])
        if len(k) >= 3 and k in hay and (best is None or len(k) > len(_key(best["company"]))):
            best = j
    return best


def apply_event(db: DB, jobs: list[dict], msg_id: str, name: str, addr: str, subject: str, when, text: str,
                raw: bytes = b"", proof_root: Path | None = None) -> str | None:
    from_ats = any(d in addr.lower() for d in ATS_SENDERS)
    if subject.startswith("Job search brief"):
        kind = None  # our own morning brief quotes other emails
    else:
        kind = classify(subject, text)
        recruiter = is_recruiter(name, addr, subject, text)
        # A recruiter's "next steps" isn't an interview, but a confirmation or rejection is what it says.
        if recruiter and not from_ats and kind not in ("confirmed", "rejected", "offer"):
            kind = "recruiter"
        elif not from_ats and not recruiter and kind and not match_job(jobs, name, addr, subject, text):
            kind = None  # status words in an email about none of your applications: newsletters, banks, alerts
    if not kind:
        db.delete_event(msg_id)  # filed under older rules that no longer match
        return None
    job = match_job(jobs, name, addr, subject, text)
    at = when.strftime("%Y-%m-%d %H:%M")
    old = db.event_kind(msg_id)
    if old == kind:
        return None  # seen before
    if old:
        db.delete_event(msg_id)  # rules improved since it was filed: refile it
    proof = save_proof(proof_root, when, job["company"] if job else (name or addr), kind, raw, text,
                       f"From: {name} <{addr}>\nDate: {when:%Y-%m-%d %H:%M}\nSubject: {subject}")
    db.add_event({"id": msg_id, "job_id": job["id"] if job else None, "kind": kind, "at": at,
                  "sender": f"{name} <{addr}>" if name else addr, "subject": subject[:200], "proof": proof})
    if kind == "recruiter":
        return kind
    if job:
        cur = db.get(job["id"])
        if kind == "confirmed" and cur["status"] == "filled":
            # Submit was pressed but the page never confirmed; the ATS email settles it.
            db.update(job["id"], status="submitted", submitted_at=at, error=None)
        if RANK[kind] >= RANK.get(cur.get("outcome") or "", 0):
            db.update(job["id"], outcome=kind, outcome_at=at)
    return kind


def rebuild(db: DB, days: int) -> int:
    """Forgets email events from the last N days (and their proof copies) so the next sync refiles them."""
    from datetime import timedelta
    cut = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    n = 0
    for e in db.events():
        if e["kind"] != "applied" and (e["at"] or "") >= cut:
            for p in (e["proof"], (e["proof"] or "")[:-4] + ".txt" if (e["proof"] or "").endswith(".eml") else None):
                if p:
                    Path(p).unlink(missing_ok=True)
            db.delete_event(e["id"])
            n += 1
    return n


def sync(db: DB, days: int = 60, extra_senders: list[str] | None = None, proof_root: Path | None = None) -> dict:
    jobs = db.by_status("submitted", "filled")
    counts: dict[str, int] = {}
    for msg in alerts.messages(days, ATS_SENDERS + (extra_senders or []), gmail_queries=RECRUITER_QUERIES):
        kind = apply_event(db, jobs, *msg, proof_root=proof_root)
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
    return counts
