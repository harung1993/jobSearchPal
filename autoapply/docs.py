"""Tailored resume and cover letter from a short JSON spec, so the agent writes content, not python-docx code.

`outline(base)` reads the base resume into the shape the spec uses. `build(base, spec, out_dir)` copies the base,
swaps in the spec's headline, summary, skills and bullets (styles kept, role/date/education lines untouched),
writes a cover letter on the same header, converts both to PDF and reports the page counts.

Spec (data/applications/<slug>/docs.json); every part but "company" is optional and defaults to the base:
  {"company": "Acme",
   "resume": {"headline": "...", "summary": "...",
              "skills": [{"label": "Languages", "text": "SQL, Python"}, ...],      # the full list, in order
              "experience": {"<heading from the outline>": ["bullet", ...], ...}},  # each list replaces that block's
                                                                                   # bullets; [] drops a group
   "cover_letter": {"to": "Hiring Team", "greeting": "Dear Hiring Team,", "paragraphs": ["...", ...],
                    "sign_off": "Sincerely,"}}
"""
from __future__ import annotations

import copy
import datetime as dt
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import docx
from docx.shared import Pt
from docx.text.paragraph import Paragraph

MAX_PAGES = {"resume": 2, "cover_letter": 1}


class SpecError(ValueError):
    """The spec asks for something the base resume doesn't have."""


# ---------- reading the base ----------

def _is_bullet(p) -> bool:
    return p.style.name.lower().startswith("list")


def _is_heading(p) -> bool:
    t = p.text.strip()
    return (bool(t) and "\t" not in t and t == t.upper() and any(c.isalpha() for c in t)
            and not _is_bullet(p) and bool(p.runs) and bool(p.runs[0].bold))


class _Resume:
    """Where each part of the base resume is, by paragraph."""

    def __init__(self, d):
        self.d = d
        ps = list(d.paragraphs)
        heads = [i for i, p in enumerate(ps) if _is_heading(p)]
        if len(heads) < 2:
            raise SpecError("The base resume needs an upper-case headline and upper-case section headings.")
        # The first upper-case bold line is the headline; the next non-empty line is the summary.
        self.headline = ps[heads[0]]
        self.summary = next((p for p in ps[heads[0] + 1:heads[1]] if p.text.strip()), None)
        self.sections = {}
        bounds = heads[1:] + [len(ps)]
        for a, b in zip(heads[1:], bounds[1:]):
            self.sections[ps[a].text.strip()] = (ps[a], ps[a + 1:b])
        self.skill_head, self.skills = None, []
        self.blocks = []  # [{"heading", "kind", "para", "bullets": [para]}]
        for name, (head, body) in self.sections.items():
            if "SKILL" in name:
                self.skill_head = head
                self.skills = [p for p in body if len(p.runs) >= 2 and p.runs[0].bold]
            elif "EXPERIENCE" in name:
                for p in body:
                    if not p.text.strip():
                        continue
                    if _is_bullet(p):
                        if self.blocks:
                            self.blocks[-1]["bullets"].append(p)
                    else:
                        self.blocks.append({"heading": p.text.split("\t")[0].strip(),
                                            "kind": "role" if "\t" in p.text else "group",
                                            "para": p, "bullets": []})

    def block(self, key: str) -> dict:
        k = key.strip().lower()
        hits = [b for b in self.blocks if b["heading"].lower() == k]
        if not hits:
            hits = [b for b in self.blocks if b["heading"].lower().startswith(k)]
        if len(hits) == 1:
            return hits[0]
        names = "; ".join(b["heading"] for b in self.blocks)
        if hits:
            raise SpecError(f'"{key}" matches more than one experience heading. Use one of: {names}')
        raise SpecError(f'No experience heading "{key}". Use one of: {names}')


def _skill(p) -> dict:
    return {"label": p.runs[0].text.strip().rstrip(":").strip(), "text": "".join(r.text for r in p.runs[1:]).strip()}


def outline(base: Path) -> dict:
    r = _Resume(docx.Document(base))
    return {"headline": r.headline.text.strip(),
            "summary": r.summary.text.strip() if r.summary else "",
            "skills": [_skill(p) for p in r.skills],
            "experience": [{"heading": b["heading"], "kind": b["kind"], "bullets": [p.text.strip() for p in b["bullets"]]}
                           for b in r.blocks]}


# ---------- editing ----------

def _set_text(p, text: str):
    if not p.runs:
        p.add_run(text)
        return
    p.runs[0].text = text
    for r in p.runs[1:]:
        r.text = ""


def _clone_after(anchor, template, text: str | None = None):
    new = copy.deepcopy(template._p)
    anchor._p.addnext(new)
    p = Paragraph(new, anchor._parent)
    if text is not None:
        _set_text(p, text)
    return p


def _remove(p):
    p._p.getparent().remove(p._p)


def _apply_resume(r: _Resume, spec: dict):
    if "headline" in spec:
        _set_text(r.headline, spec["headline"])
    if "summary" in spec and r.summary is not None:
        _set_text(r.summary, spec["summary"])
    if "skills" in spec:
        if not r.skills:
            raise SpecError("The base resume has no skills lines to replace.")
        tpl = r.skills[0]
        sep = tpl.runs[0].text[len(tpl.runs[0].text.rstrip().rstrip(":").rstrip()):] or ": "
        anchor = r.skill_head
        made = []
        for item in spec["skills"]:
            p = _clone_after(anchor, tpl)
            p.runs[0].text = f'{item["label"].strip()}{sep}'
            p.runs[1].text = item["text"].strip()
            for extra in p.runs[2:]:
                extra.text = ""
            anchor = p
            made.append(p)
        for p in r.skills:
            _remove(p)
        r.skills = made
    any_bullet = next((p for b in r.blocks for p in b["bullets"]), None)
    for key, bullets in (spec.get("experience") or {}).items():
        b = r.block(key)
        tpl = b["bullets"][0] if b["bullets"] else any_bullet
        if tpl is None and bullets:
            raise SpecError("The base resume has no bullet points to copy the style from.")
        anchor = b["para"]
        made = []
        for text in bullets:
            anchor = _clone_after(anchor, tpl, text.strip())
            made.append(anchor)
        for p in b["bullets"]:
            _remove(p)
        b["bullets"] = made
        if not bullets and b["kind"] == "group":
            _remove(b["para"])
            r.blocks.remove(b)


def _cover(base: Path, spec: dict, company: str, today: dt.date) -> docx.Document:
    c = docx.Document(base)
    r = _Resume(c)
    ps = list(c.paragraphs)
    tpl = r.summary or r.headline
    keep = []
    for p in ps:
        if p._p is r.headline._p:
            break
        keep.append(p._p)  # name and contact lines
    for p in ps:
        if p._p not in keep and p._p is not tpl._p:
            _remove(p)
    name = ps[0].text.strip() if keep else ""
    to =(spec.get("to") or "Hiring Team").strip()
    if company.lower() not in to.lower():
        to = f"{to}\n{company}"
    lines = [f"{today:%B} {today.day}, {today.year}", to, spec.get("greeting") or "Dear Hiring Team,",
             *spec.get("paragraphs", []), f'{spec.get("sign_off") or "Sincerely,"}\n{name}'.strip()]
    _set_text(tpl, lines[0])
    tpl.paragraph_format.space_before = Pt(14)
    tpl.paragraph_format.space_after = Pt(10)
    anchor = tpl
    for text in lines[1:]:
        anchor = _clone_after(anchor, tpl, text)
        anchor.paragraph_format.space_before = Pt(0)
    return c


# ---------- PDF ----------

def to_pdf(path: Path) -> Path | None:
    """LibreOffice conversion next to the .docx. None when LibreOffice isn't installed or fails."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    # A user id with no home (Docker) can't make LibreOffice's profile: give it its own.
    lo_profile = Path(tempfile.gettempdir()) / f"lo-profile-{os.getuid()}"
    try:
        subprocess.run([soffice, f"-env:UserInstallation={lo_profile.as_uri()}", "--headless", "--convert-to", "pdf",
                        "--outdir", str(path.parent), str(path)], capture_output=True, timeout=180, check=True)
    except (subprocess.SubprocessError, OSError):
        return None
    out = path.with_suffix(".pdf")
    return out if out.exists() else None


def pdf_pages(path: Path) -> int:
    from .preflight import pdf_text
    got = pdf_text(path)
    return got[1] if got else 0


# ---------- build ----------

def _safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', " ", s).strip()


def build(base: Path, spec: dict, out_dir: Path, last_name: str = "", pdf: bool = True,
          today: dt.date | None = None) -> dict:
    company = _safe(str(spec.get("company") or ""))
    if not company:
        raise SpecError('The spec needs "company".')
    base = Path(base)
    last = _safe(last_name) or base.stem.split(" Resume")[0].strip() or "My"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    today = today or dt.date.today()

    d = docx.Document(base)
    r = _Resume(d)
    _apply_resume(r, spec.get("resume") or {})
    out = {"resume": str(out_dir / f"{last} Resume - {company}.docx")}
    d.save(out["resume"])
    if spec.get("cover_letter"):
        out["cover_letter"] = str(out_dir / f"{last} Cover Letter - {company}.docx")
        _cover(base, spec["cover_letter"], company, today).save(out["cover_letter"])

    problems = []
    if pdf:
        out["pdf"], out["pages"] = {}, {}
        for kind in ("resume", "cover_letter"):
            if kind not in out:
                continue
            made = to_pdf(Path(out[kind]))
            if made is None:
                problems.append(f"PDF not made for {Path(out[kind]).name}: LibreOffice (soffice) is missing or failed.")
                continue
            out["pdf"][kind] = str(made)
            n = out["pages"][kind] = pdf_pages(made)
            if n > MAX_PAGES[kind]:
                what = "Resume" if kind == "resume" else "Cover letter"
                msg = f"{what} is {n} pages; the limit is {MAX_PAGES[kind]}."
                if kind == "resume":
                    longest = sorted(r.blocks, key=lambda b: -sum(len(p.text) for p in b["bullets"]))[:3]
                    msg += " Shorten or drop bullets, longest first: " + ", ".join(
                        f'{b["heading"]} ({len(b["bullets"])} bullets)' for b in longest if b["bullets"])
                else:
                    msg += " Cut a paragraph or shorten them."
                problems.append(msg)
    out["problems"] = problems
    return out
