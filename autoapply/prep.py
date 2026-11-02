"""Interview prep packs. When an email is sorted as an interview, the next daily run researches the company's
interview process and writes a prep pack to data/prep/<company>-<id>.md; the dashboard shows it.

The agent finds out what to prepare with `autoapply list --prep`. A pack is due when an application has an
interview email and either no pack yet or one older than the latest interview email (a new stage).
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from html import escape
from pathlib import Path

from .db import DB


def prep_path(data_dir: Path, job: dict) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", (job["company"] or "company").lower()).strip("-")[:40] or "company"
    return data_dir / "prep" / f"{slug}-{hashlib.sha1(job['id'].encode()).hexdigest()[:6]}.md"


def _interview_emails(db: DB) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for e in db.events():  # settled only, newest first
        if e["kind"] == "interview" and e["job_id"]:
            out.setdefault(e["job_id"], []).append(e)
    return out


def interviews(db: DB, data_dir: Path) -> list[dict]:
    """Every application with an interview email, newest first, with its prep pack's state."""
    out = []
    for job_id, evs in _interview_emails(db).items():
        job = db.get(job_id)
        if not job:
            continue
        p = prep_path(data_dir, job)
        latest = evs[0]["at"] or ""
        made = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if p.exists() else None
        out.append({"job": job, "path": p, "latest": latest, "emails": evs, "made": made,
                    "state": "ready" if made and made >= latest else ("update" if made else "due")})
    return sorted(out, key=lambda r: r["latest"], reverse=True)


def needed(db: DB, data_dir: Path) -> list[dict]:
    """What the daily run should prepare: everything without a pack, or with one from before the latest stage."""
    rows = []
    for r in interviews(db, data_dir):
        if r["state"] == "ready":
            continue
        j = r["job"]
        rows.append({
            "job_id": j["id"], "company": j["company"], "title": j["title"], "posting": j["url"],
            "prep_path": str(r["path"]), "update": r["state"] == "update",
            "description": (j["description"] or "")[:6000],
            "interview_emails": [{"at": e["at"], "from": e["sender"], "subject": e["subject"], "text": (e["body"] or "")[:2500]}
                                 for e in r["emails"][:3]],
        })
    return rows


def render(md: str) -> str:
    """A small, safe Markdown subset (headings, lists, bold, italics, links, quotes, tables as text).
    Everything is escaped first: pack text comes partly from the web."""
    html, in_list, para = [], None, []

    def inline(t: str) -> str:
        t = escape(t)
        t = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>', t)
        t = re.sub(r"(?<![\"'>=])(https?://[^\s<]+)", r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>', t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", t)
        return re.sub(r"`([^`]+)`", r"<code>\1</code>", t)

    def flush():
        nonlocal in_list
        if para:
            html.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()
        if in_list:
            html.append(f"</{in_list}>")
            in_list = None

    for line in md.splitlines():
        s = line.rstrip()
        if not s.strip():
            flush()
            continue
        if m := re.match(r"^(#{1,4})\s+(.*)", s):
            flush()
            n = min(len(m.group(1)) + 1, 4)
            html.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
        elif m := re.match(r"^\s*(?:[-*]|(\d+)[.)])\s+(.*)", s):
            if para:
                html.append(f"<p>{inline(' '.join(para))}</p>")
                para.clear()
            tag = "ol" if m.group(1) else "ul"
            if in_list != tag:
                if in_list:
                    html.append(f"</{in_list}>")
                html.append(f"<{tag}>")
                in_list = tag
            html.append(f"<li>{inline(m.group(2))}</li>")
        elif s.startswith(">"):
            flush()
            html.append(f"<blockquote>{inline(s.lstrip('> '))}</blockquote>")
        elif re.match(r"^\|?\s*:?-{3,}", s):
            continue  # table divider rows
        elif s.startswith("|"):
            flush()
            cells = [c.strip() for c in s.strip("|").split("|")]
            html.append("<div class=\"trow\">" + "".join(f"<span>{inline(c)}</span>" for c in cells) + "</div>")
        else:
            if in_list:
                html.append(f"</{in_list}>")
                in_list = None
            para.append(s.strip())
    flush()
    return "\n".join(html)
