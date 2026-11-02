"""More places to find roles: any RSS feed (HigherEdJobs, We Work Remotely, jobs.ac.uk...), job sites with
open APIs (The Muse, Himalayas, Jobicy) and ones with a free key (Adzuna, USAJOBS).

Like the other feeds these aren't apply-ready: the agent follows each to the real posting. Titles are
filtered the same way as boards (config.yaml `search`), so the keyword searches below only cut the download.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import httpx

from .finder import UA, _strip_html

KEYS = {"adzuna": ("ADZUNA_APP_ID", "ADZUNA_APP_KEY"), "usajobs": ("USAJOBS_API_KEY", "USAJOBS_EMAIL")}
MAX_KEYWORDS = 8  # searches per keyed source per run; keeps Adzuna well inside its free 2,500 calls a month


def _keywords(search: dict) -> list[str]:
    return [k for k in (search.get("title_keywords") or []) if len(k) >= 3][:MAX_KEYWORDS]


def _slug(url: str) -> str:
    u = urlparse(url)
    return re.sub(r"[^a-z0-9]+", "-", (u.netloc + u.path + "-" + u.query).lower()).strip("-")[:60]


# ---- RSS / Atom ---------------------------------------------------------------------------------------

A = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"


def _text(el, *names) -> str:
    for n in names:
        f = el.find(n)
        if f is not None and (f.text or "").strip():
            return f.text.strip()
    return ""


def parse_feed(xml: bytes | str, source: str) -> list[dict]:
    """RSS 2.0 or Atom items -> jobs. Many job feeds put the employer and place in the title or the
    description; both go to the agent, and the location filter reads them."""
    root = ET.fromstring(xml)
    items = root.findall(".//item") or root.findall(f".//{A}entry")
    channel = _text(root, "channel/title", f"{A}title")
    out = []
    for it in items:
        title = _strip_html(_text(it, "title", f"{A}title"))
        link = _text(it, "link")
        if not link:
            al = it.find(f"{A}link")
            link = al.get("href", "") if al is not None else ""
        if not title or not link:
            continue
        desc = _strip_html(_text(it, CONTENT, "description", f"{A}content", f"{A}summary"))
        company = _text(it, f"{DC}creator", "author", f"{A}author/{A}name")
        loc = _text(it, "location", "{http://www.jobs.ac.uk/rss}location")
        guid = _text(it, "guid", f"{A}id") or link
        out.append({"id": f"rss:{source}:{_slug(guid)}", "ats": "feed", "company": company or channel,
                    "title": title, "location": loc, "url": link, "apply_url": link,
                    "description": desc})
    return out


def rss(url: str, name: str = "") -> list[dict]:
    r = httpx.get(url, headers={**UA, "accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"},
                  timeout=30, follow_redirects=True)
    r.raise_for_status()
    head = r.content[:600].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")) or b"<html" in head:
        # Usually a bot check. We don't try to get around those; use the site's job-alert emails instead.
        raise RuntimeError("the site sent a web page, not a feed (it likely blocks automated access); "
                           "use its job-alert emails instead")
    return parse_feed(r.content, name or urlparse(url).netloc.removeprefix("www."))


# ---- open APIs ----------------------------------------------------------------------------------------

def themuse(search: dict | None = None, categories=("Data and Analytics", "Data Science", "Software Engineering"),
            pages: int = 3) -> list[dict]:
    out = []
    for cat in categories:
        for page in range(pages):
            r = httpx.get("https://www.themuse.com/api/public/jobs", params={"category": cat, "page": page},
                          headers=UA, timeout=30)
            r.raise_for_status()
            res = r.json().get("results") or []
            for j in res:
                url = (j.get("refs") or {}).get("landing_page", "")
                out.append({"id": f"themuse:{j['id']}", "ats": "feed", "company": (j.get("company") or {}).get("name", ""),
                            "title": j.get("name", ""), "url": url, "apply_url": url,
                            "location": "; ".join(l.get("name", "") for l in j.get("locations") or []),
                            "description": _strip_html(j.get("contents", ""))})
            if len(res) < 20:
                break
    return out


def himalayas(search: dict | None = None, pages: int = 5) -> list[dict]:
    """Remote roles. The API pages 20 at a time, newest first."""
    out = []
    for page in range(pages):
        r = httpx.get("https://himalayas.app/jobs/api", params={"limit": 20, "offset": page * 20}, headers=UA, timeout=30)
        r.raise_for_status()
        jobs = r.json().get("jobs") or []
        for j in jobs:
            where = ", ".join(j.get("locationRestrictions") or []) or "Anywhere"
            url = j.get("applicationLink") or j.get("guid") or ""
            out.append({"id": f"himalayas:{_slug(j.get('guid') or url)}", "ats": "feed", "company": j.get("companyName", ""),
                        "title": j.get("title", ""), "location": f"Remote {where}", "url": j.get("guid") or url,
                        "apply_url": url, "description": _strip_html(j.get("description") or j.get("excerpt", ""))})
        if len(jobs) < 20:
            break
    return out


def jobicy(search: dict | None = None) -> list[dict]:
    """Remote roles open to the US (or anywhere). Jobicy asks for at most one request an hour; we make one a day."""
    search = search or {}
    params = {"count": 100, **({"geo": "usa"} if search.get("us_only", True) else {})}
    r = httpx.get("https://jobicy.com/api/v2/remote-jobs", params=params, headers=UA, timeout=30)
    r.raise_for_status()
    return [{"id": f"jobicy:{j['id']}", "ats": "feed", "company": j.get("companyName", ""), "title": j.get("jobTitle", ""),
             "location": f"Remote {j.get('jobGeo') or ''}", "url": j.get("url", ""), "apply_url": j.get("url", ""),
             "description": _strip_html(j.get("jobDescription") or j.get("jobExcerpt", ""))}
            for j in r.json().get("jobs") or []]


# ---- free keys ----------------------------------------------------------------------------------------

def adzuna(search: dict | None = None) -> list[dict]:
    """A large US aggregator (it indexes many company sites and boards). Free key: developer.adzuna.com."""
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        raise RuntimeError("needs ADZUNA_APP_ID and ADZUNA_APP_KEY (free at developer.adzuna.com)")
    out = []
    for kw in _keywords(search or {}):
        r = httpx.get("https://api.adzuna.com/v1/api/jobs/us/search/1", headers=UA, timeout=30, params={
            "app_id": app_id, "app_key": app_key, "what_phrase": kw, "results_per_page": 50, "max_days_old": 7,
            "content-type": "application/json"})
        r.raise_for_status()
        for j in r.json().get("results") or []:
            out.append({"id": f"adzuna:{j['id']}", "ats": "feed", "company": (j.get("company") or {}).get("display_name", ""),
                        "title": _strip_html(j.get("title", "")), "location": (j.get("location") or {}).get("display_name", ""),
                        "url": j.get("redirect_url", ""), "apply_url": j.get("redirect_url", ""),
                        "description": _strip_html(j.get("description", ""))})
    return out


def usajobs(search: dict | None = None) -> list[dict]:
    """US federal jobs. Free key: developer.usajobs.gov (the email you signed up with goes in USAJOBS_EMAIL)."""
    key, email = os.environ.get("USAJOBS_API_KEY"), os.environ.get("USAJOBS_EMAIL")
    if not (key and email):
        raise RuntimeError("needs USAJOBS_API_KEY and USAJOBS_EMAIL (free at developer.usajobs.gov)")
    out = []
    for kw in _keywords(search or {}):
        r = httpx.get("https://data.usajobs.gov/api/search", timeout=30,
                      headers={"Host": "data.usajobs.gov", "User-Agent": email, "Authorization-Key": key},
                      params={"Keyword": kw, "ResultsPerPage": 100, "DatePosted": 7})
        r.raise_for_status()
        for it in (r.json().get("SearchResult") or {}).get("SearchResultItems") or []:
            d = it.get("MatchedObjectDescriptor") or {}
            apply = (d.get("ApplyURI") or [d.get("PositionURI", "")])[0]
            summary = ((d.get("UserArea") or {}).get("Details") or {}).get("JobSummary", "")
            out.append({"id": f"usajobs:{d.get('PositionID') or it.get('MatchedObjectId')}", "ats": "feed",
                        "company": d.get("OrganizationName", ""), "title": d.get("PositionTitle", ""),
                        "location": d.get("PositionLocationDisplay", "") + " United States",
                        "url": d.get("PositionURI", ""), "apply_url": apply, "description": _strip_html(summary)})
    return out


# name -> (function, label, needs keys)
EXTRA = {
    "themuse": (themuse, "The Muse", None),
    "himalayas": (himalayas, "Himalayas (remote)", None),
    "jobicy": (jobicy, "Jobicy (remote)", None),
    "adzuna": (adzuna, "Adzuna (large US aggregator, free key)", KEYS["adzuna"]),
    "usajobs": (usajobs, "USAJOBS (federal, free key)", KEYS["usajobs"]),
}

# Feeds people often want, offered as one-click additions on the Setup page.
# (HigherEdJobs answers automated requests with a bot check, so it comes in through its Job Agent emails.)
RSS_PRESETS = [
    ("We Work Remotely: Programming", "https://weworkremotely.com/categories/remote-programming-jobs.rss"),
]
