"""Imports past applications (the Job Tracker sheet exported as JSON rows) so stats cover the whole search."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from .db import DB


def _date(s: str) -> str | None:
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d 12:00")
        except ValueError:
            pass
    return None


def outcome_from_notes(notes: str) -> tuple[str | None, str | None]:
    """Pulls 'Rejected 10/13' style notes into an outcome and a date (year assumed 2026)."""
    m = re.search(r"\b(rejected|interview\w*|offer)\b[^0-9]{0,20}(\d{1,2})/(\d{1,2})", notes or "", re.I)
    if not m:
        return None, None
    kind = m.group(1).lower()
    kind = "interview" if kind.startswith("interview") else kind
    return kind, f"2026-{int(m.group(2)):02d}-{int(m.group(3)):02d} 12:00"


def import_rows(db: DB, rows: list[list[str]]) -> int:
    """rows: header first, columns Position | Company | Location | Date Applied | Salary | Found on | Applied via | Notes."""
    n = 0
    for r in rows[1:]:
        r = (r + [""] * 8)[:8]
        title, company, location, applied, pay, found_on, via, notes = (c.strip() for c in r)
        when = _date(applied)
        if not (company and when):  # recruiter leads and not-yet-submitted rows have no applied date
            continue
        jid = "tracker:" + hashlib.sha1(f"{company}|{title}".lower().encode()).hexdigest()[:10]
        if db.get(jid) is None:
            db.upsert_job({"id": jid, "ats": (via or "unknown").split(" ")[0].lower(), "company": company,
                           "title": title, "location": location, "url": "", "apply_url": "", "description": notes})
            n += 1
        kind, at = outcome_from_notes(notes)
        db.update(jid, status="submitted", submitted_at=when, found_at=when, pay=pay or None,
                  **({"outcome": kind, "outcome_at": at} if kind else {}))
    return n


def import_file(db: DB, path: Path) -> int:
    return import_rows(db, json.loads(Path(path).read_text()))
