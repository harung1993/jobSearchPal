"""Pulls open roles from public ATS job-board APIs (no login needed) and keeps the relevant ones."""
from __future__ import annotations

import html
import re
import sys

import httpx
import yaml

from .config import Settings
from .db import DB


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(s or ""))).strip()


def greenhouse(slug: str) -> list[dict]:
    r = httpx.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}, timeout=30)
    r.raise_for_status()
    return [
        {
            "id": f"greenhouse:{slug}:{j['id']}",
            "ats": "greenhouse",
            "company": slug,
            "title": j["title"],
            "location": (j.get("location") or {}).get("name") or "",
            "url": j["absolute_url"],
            "apply_url": f"https://job-boards.greenhouse.io/embed/job_app?for={slug}&token={j['id']}",
            "description": _strip_html(j.get("content", "")),
        }
        for j in r.json().get("jobs", [])
    ]


def lever(slug: str) -> list[dict]:
    r = httpx.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}, timeout=30)
    r.raise_for_status()
    return [
        {
            "id": f"lever:{slug}:{j['id']}",
            "ats": "lever",
            "company": slug,
            "title": j["text"],
            "location": (j.get("categories") or {}).get("location") or j.get("workplaceType") or "",
            "url": j["hostedUrl"],
            "apply_url": j.get("applyUrl") or j["hostedUrl"] + "/apply",
            "description": j.get("descriptionPlain", "") + " " + _strip_html(
                " ".join(l.get("content", "") for l in j.get("lists", []))
            ),
        }
        for j in r.json()
    ]


def ashby(slug: str) -> list[dict]:
    r = httpx.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=30)
    r.raise_for_status()
    return [
        {
            "id": f"ashby:{slug}:{j['id']}",
            "ats": "ashby",
            "company": slug,
            "title": j["title"],
            "location": ("Remote " if j.get("isRemote") else "") + (j.get("location") or ""),
            "url": j["jobUrl"],
            "apply_url": j.get("applyUrl") or j["jobUrl"] + "/application",
            "description": j.get("descriptionPlain") or _strip_html(j.get("descriptionHtml", "")),
        }
        for j in r.json().get("jobs", [])
    ]


def workable(slug: str) -> list[dict]:
    r = httpx.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", params={"details": "true"}, timeout=30)
    r.raise_for_status()
    return [
        {
            "id": f"workable:{slug}:{j['shortcode']}",
            "ats": "workable",
            "company": slug,
            "title": j["title"],
            "location": ("Remote " if j.get("telecommuting") else "") + ", ".join(
                x for x in (j.get("city"), j.get("state"), j.get("country")) if x),
            "url": j.get("url") or j.get("shortlink", ""),
            "apply_url": j.get("application_url") or j.get("url", ""),
            "description": _strip_html(j.get("description", "")),
        }
        for j in r.json().get("jobs", [])
    ]


def smartrecruiters(slug: str) -> list[dict]:
    r = httpx.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", params={"limit": 100}, timeout=30)
    r.raise_for_status()
    out = []
    for j in r.json().get("content", []):
        loc = j.get("location") or {}
        url = f"https://jobs.smartrecruiters.com/{slug}/{j['id']}"
        out.append({
            "id": f"smartrecruiters:{slug}:{j['id']}",
            "ats": "smartrecruiters",
            "company": (j.get("company") or {}).get("name", slug),
            "title": j["name"],
            "location": ("Remote " if loc.get("remote") else "") + ", ".join(
                x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x),
            "url": url,
            "apply_url": url,
            "description": "",  # needs a per-posting call; `autoapply show` callers can open the url
        })
    return out


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby,
            "workable": workable, "smartrecruiters": smartrecruiters}


# ---------- aggregator feeds: not apply-ready; Claude follows each to the real ATS posting ----------

UA = {"user-agent": "autoapply/0.1 (personal job search)"}


def remoteok() -> list[dict]:
    r = httpx.get("https://remoteok.com/api", headers=UA, timeout=30)
    r.raise_for_status()
    return [
        {
            "id": f"remoteok:{j['id']}",
            "ats": "feed",
            "company": j.get("company", ""),
            "title": j.get("position", ""),
            "location": "Remote " + (j.get("location") or ""),
            "url": j.get("url", ""),
            "apply_url": j.get("apply_url") or j.get("url", ""),
            "description": _strip_html(j.get("description", "")),
        }
        for j in r.json() if isinstance(j, dict) and j.get("id")  # first item is a legal notice
    ]


def remotive() -> list[dict]:
    r = httpx.get("https://remotive.com/api/remote-jobs", params={"category": "data"}, headers=UA, timeout=30)
    r.raise_for_status()
    return [
        {
            "id": f"remotive:{j['id']}",
            "ats": "feed",
            "company": j.get("company_name", ""),
            "title": j.get("title", ""),
            "location": "Remote " + (j.get("candidate_required_location") or ""),
            "url": j.get("url", ""),
            "apply_url": j.get("url", ""),
            "description": _strip_html(j.get("description", "")),
        }
        for j in r.json().get("jobs", [])
    ]


def hn_whos_hiring() -> list[dict]:
    """Top-level comments of the latest monthly 'Ask HN: Who is hiring?' thread."""
    r = httpx.get("https://hn.algolia.com/api/v1/search_by_date",
                  params={"tags": "story,author_whoishiring", "query": "who is hiring"}, timeout=30)
    r.raise_for_status()
    hits = [h for h in r.json().get("hits", []) if "who is hiring" in h.get("title", "").lower()]
    if not hits:
        return []
    story = hits[0]["objectID"]
    r = httpx.get(f"https://hn.algolia.com/api/v1/items/{story}", timeout=60)
    r.raise_for_status()
    out = []
    for c in r.json().get("children", []):
        text = _strip_html((c.get("text") or "").replace("<p>", "\n"))
        if not text:
            continue
        head = text.split("\n")[0][:200]  # convention: "Company | Role | Location | Remote | ..."
        parts = [p.strip() for p in head.split("|")]
        out.append({
            "id": f"hn:{c['id']}",
            "ats": "feed",
            "company": parts[0] if parts else "",
            "title": head,
            "location": head,
            "url": f"https://news.ycombinator.com/item?id={c['id']}",
            "apply_url": "",
            "description": text,
        })
    return out


FEEDS = {"remoteok": remoteok, "remotive": remotive, "hn_whos_hiring": hn_whos_hiring}


US_MARKERS = re.compile(
    r"\b(united states|usa|u\.s\.|us|america|remote[- ]us|boston|massachusetts|ma|new england|new york|nyc"
    r"|anywhere in the us|north america)\b", re.I)
NON_US = re.compile(
    r"\b(emea|europe|eu|uk|united kingdom|england|london|ireland|dublin|poland|warsaw|spain|madrid|barcelona"
    r"|germany|berlin|munich|france|paris|netherlands|amsterdam|portugal|lisbon|italy|sweden|denmark|norway"
    r"|switzerland|zurich|romania|ukraine|serbia|israel|tel aviv|india|bangalore|bengaluru|hyderabad|pune"
    r"|singapore|apac|japan|tokyo|australia|sydney|new zealand|philippines|latam|brazil|s[aã]o paulo"
    r"|mexico|argentina|colombia|chile|canada|toronto|vancouver|montreal)\b", re.I)


def location_ok(loc: str, search: dict) -> bool:
    """US-only by default: a US marker passes; plain "Remote" passes unless it names another country."""
    loc = loc or ""
    if not search.get("us_only", True):
        oks = search.get("locations_ok", [])
        return not oks or not loc or any(k in loc.lower() for k in oks)
    if not loc.strip() or US_MARKERS.search(loc):
        return True
    return "remote" in loc.lower() and not NON_US.search(loc)


def matches(job: dict, search: dict) -> bool:
    title = (job.get("title") or "").lower()
    if not any(k in title for k in search.get("title_keywords", [])):
        return False
    if any(k in title for k in search.get("exclude_keywords", [])):
        return False
    return location_ok(job.get("location") or "", search)


def boards(settings: Settings) -> list[dict]:
    """Boards from config.yaml plus the growing list in boards.yaml (see `autoapply board add`)."""
    extra = settings.path(settings.raw.get("boards_file", "boards.yaml"))
    more = (yaml.safe_load(extra.read_text()) or []) if extra.exists() else []
    seen, out = set(), []
    for b in settings.raw.get("boards", []) + more:
        key = (b["ats"], b["slug"].lower())
        if key not in seen:
            seen.add(key)
            out.append(b)
    return out


def add_board(settings: Settings, ats: str, slug: str) -> int:
    """Checks the board answers, then appends it to boards.yaml. Returns its open job count."""
    jobs = FETCHERS[ats](slug)  # raises on a wrong slug
    path = settings.path(settings.raw.get("boards_file", "boards.yaml"))
    current = (yaml.safe_load(path.read_text()) or []) if path.exists() else []
    if not any(b["ats"] == ats and b["slug"].lower() == slug.lower() for b in current):
        current.append({"ats": ats, "slug": slug})
        path.write_text(yaml.safe_dump(current, sort_keys=False))
    return len(jobs)


def run(settings: Settings, db: DB) -> list[dict]:
    """Fetch every board and feed; store new matching jobs. Returns the new ones."""
    sources = [(f"{b['ats']}:{b['slug']}", lambda b=b: FETCHERS[b["ats"]](b["slug"])) for b in boards(settings)]
    sources += [(name, FEEDS[name]) for name in settings.raw.get("feeds", [])]
    new = []
    for name, fetch in sources:
        try:
            jobs = fetch()
        except Exception as e:  # one dead source shouldn't stop the run
            print(f"[finder] {name} failed: {e}", file=sys.stderr)
            continue
        for job in jobs:
            job = {k: ("" if v is None else v) for k, v in job.items()}  # boards often send null locations
            if matches(job, settings.raw.get("search", {})) and db.upsert_job(job):
                new.append(job)
    return new
