"""Cheap pre-ranking in Python, so the daily agent reads a short list instead of every new role.

The score is a rough fit signal, not a decision: title keyword hits, overlap between the posting and the
words on your base resume and notes, and freshness. The agent still reads the top few and picks.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

STOP = set("""a an and are as at be by for from has have in is it its of on or our that the their this to we will with
you your they them who what which while all any can more most other some such than then there these those into about
over under also not only very just been being were was do does did per via etc using use used work working team teams
experience years year role roles company job jobs including include strong ability skills skill across within new""".split())


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z0-9+#.]{1,30}", (text or "").lower()) if w not in STOP and not w.endswith(".")]


def profile_terms(root: Path, limit: int = 120) -> set[str]:
    """The most frequent meaningful words in your base resume and notes."""
    from .setup import base_resume
    texts = []
    r = base_resume(root)
    if r:
        try:
            from docx import Document
            d = Document(str(r))
            texts.append("\n".join(p.text for p in d.paragraphs))
            texts += [c.text for t in d.tables for row in t.rows for c in row.cells]
        except Exception:
            pass
    for p in [root / "CLAUDE.local.md", *sorted((root / "materials" / "context").glob("*.md"))]:
        try:
            texts.append(p.read_text()[:20000])
        except OSError:
            pass
    c = Counter(_words("\n".join(texts)))
    return {w for w, _ in c.most_common(limit)}


def score(job: dict, search: dict, terms: set[str], now: datetime | None = None) -> float:
    title = (job.get("title") or "").lower()
    s = 3.0 * sum(1 for k in search.get("title_keywords") or [] if k in title)
    if re.search(r"\b(senior|sr\.?|staff|lead|principal|head|manager|director)\b", title):
        s += 1.0
    desc = set(_words(job.get("description") or ""))
    if terms and desc:
        s += 6.0 * len(desc & terms) / max(20, min(len(terms), 80))
    try:
        age = ((now or datetime.now()) - datetime.fromisoformat(str(job.get("found_at"))[:19])).days
        s += max(0.0, 2.0 - 0.25 * age)
    except ValueError:
        pass
    if job.get("ats") == "feed":
        s -= 0.5  # needs a hop to the real posting
    return round(s, 2)


def candidates(db, settings, limit: int = 12, desc_chars: int = 1200) -> list[dict]:
    """New roles, best first, with trimmed descriptions. Saves each score on the job."""
    search = settings.raw.get("search") or {}
    terms = profile_terms(settings.root)
    rows = []
    for j in db.by_status("new"):
        sc = score(j, search, terms)
        rows.append((sc, j))
        if j.get("score") != sc:
            db.update(j["id"], score=sc)
    rows.sort(key=lambda r: r[0], reverse=True)
    out = []
    for sc, j in rows[:limit]:
        d = re.sub(r"\s+", " ", j.get("description") or "").strip()
        out.append({"id": j["id"], "score": sc, "company": j["company"], "title": j["title"], "location": j["location"],
                    "ats": j["ats"], "url": j["url"], "found_at": j["found_at"],
                    "description": d[:desc_chars] + ("…" if len(d) > desc_chars else "")})
    return out


QUICK_PROMPT = """You pick which new job postings are worth applying to today, for the person described below.
Pick at most {n}. Fit to the person comes first, then freshness, then pay. Skip roles they already applied to.
It's fine to pick fewer, or none, if nothing fits well.

About the person:
{about}

Already applied to (skip these companies' same roles):
{applied}

Candidates (pre-ranked by a rough keyword score, best first):
{cands}

Reply with JSON only, no prose: {{"picks": [{{"id": "<candidate id>", "why": "<one line on fit>"}}]}}"""


def _about(root: Path, chars: int = 4000) -> str:
    parts = []
    for p in [root / "CLAUDE.local.md", *sorted((root / "materials" / "context").glob("*.md"))]:
        try:
            parts.append(p.read_text())
        except OSError:
            pass
    return "\n\n".join(parts)[:chars] or "(no notes yet; judge by the titles and the resume keywords)"


def quick_pick(db, settings, n: int, model: str, key: str, limit: int = 15) -> tuple[list[dict], dict]:
    """One cheap chat call instead of an agent session: candidates in, picks out. Returns (picks, usage)."""
    from . import classify
    cands = candidates(db, settings, limit, desc_chars=700)
    if not cands or n <= 0:
        return [], {"tokens": 0, "cost": 0.0}
    applied = sorted({f"{j['company']} ({j['title']})" for j in db.by_status("submitted")})[-60:]
    lines = [f"- id={c['id']} | {c['company']} | {c['title']} | {c['location'] or '?'} | found {str(c['found_at'])[:10]}"
             f"\n  {c['description']}" for c in cands]
    prompt = QUICK_PROMPT.format(n=n, about=_about(settings.root), applied="\n".join(applied) or "(none)",
                                 cands="\n".join(lines))
    data = classify._post("https://openrouter.ai/api/v1/chat/completions",
                          {"model": model, "temperature": 0, "messages": [{"role": "user", "content": prompt}],
                           "response_format": {"type": "json_object"}, "usage": {"include": True}}, key)
    u = data.get("usage") or {}
    usage = {"tokens": int(u.get("total_tokens") or 0), "cost": float(u.get("cost") or 0)}
    text = data["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{.*\}", text, re.S)
    try:
        raw = json.loads(m.group(0))["picks"]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise RuntimeError(f"the model's reply wasn't the JSON asked for: {text[:200]}")
    ok = {c["id"]: c for c in cands}
    picks, seen = [], set()
    for p in raw if isinstance(raw, list) else []:
        jid = p.get("id") if isinstance(p, dict) else None
        if jid in ok and jid not in seen:  # only ids we offered: the model can't add jobs
            seen.add(jid)
            c = ok[jid]
            picks.append({"id": jid, "why": str(p.get("why") or "")[:200],
                          **({"by_hand": True} if c["ats"] == "workday" else {})})
    return picks[:n], usage


ALERT_PROMPT = """These are job-alert emails (LinkedIn, Indeed, HigherEdJobs and others) for the person described below.
List the roles in them that clearly fit the person, at most {n}, best first. Leave out roles they already applied to.
It's fine to list none.

About the person:
{about}

Already applied to:
{applied}

Emails:
{emails}

Reply with JSON only, no prose:
{{"roles": [{{"company": "<company>", "title": "<job title>", "link": "<the link in the email, if any>", "why": "<one line>"}}]}}"""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def alert_roles(db, settings, emails: list[dict], model: str, key: str, limit: int = 8) -> tuple[list[dict], dict]:
    """One cheap call over the alert emails: the roles worth looking up. Drops ones already tracked."""
    from . import classify
    if not emails:
        return [], {"tokens": 0, "cost": 0.0}
    applied = sorted({f"{j['company']} ({j['title']})" for j in db.by_status("submitted")})[-60:]
    body = "\n\n".join(f"--- {e['from']} | {e['subject']}\n{e['text'][:3000]}" for e in emails)[:40000]
    prompt = ALERT_PROMPT.format(n=limit, about=_about(settings.root), applied="\n".join(applied) or "(none)", emails=body)
    data = classify._post("https://openrouter.ai/api/v1/chat/completions",
                          {"model": model, "temperature": 0, "messages": [{"role": "user", "content": prompt}],
                           "response_format": {"type": "json_object"}, "usage": {"include": True}}, key)
    u = data.get("usage") or {}
    usage = {"tokens": int(u.get("total_tokens") or 0), "cost": float(u.get("cost") or 0)}
    text = data["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{.*\}", text, re.S)
    try:
        raw = json.loads(m.group(0))["roles"]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise RuntimeError(f"the model's reply wasn't the JSON asked for: {text[:200]}")
    have = {(_norm(j["company"]), _norm(j["title"])) for j in db.all()}
    out = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict) or not r.get("company") or not r.get("title"):
            continue
        k = (_norm(r["company"]), _norm(r["title"]))
        if k in have:
            continue
        have.add(k)
        out.append({f: str(r.get(f) or "")[:300] for f in ("company", "title", "link", "why")})
    return out[:limit], usage
