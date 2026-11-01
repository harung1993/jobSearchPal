"""Playwright form filler for Greenhouse, Lever and Ashby application pages.

Flow: scan() lists the questions, the caller (Claude or the LLM pipeline) writes a plan of
answers, fill() fills everything and stops before Submit, submit() refills and clicks
Submit only when the plan is marked approved by you.

Plan shape:
  {"url": "...", "ats": "greenhouse",
   "files": {"resume": "/path/resume.pdf", "cover_letter": "/path/cover.pdf"},
   "answers": {"<question label>": "<value>" | ["<checkbox option>", ...]},
   "approved": {"<held question label>": "<value>"},   # consents/demographics you decided
   "approved_by_user": false}
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

SCAN_JS = (Path(__file__).parent / "scan.js").read_text()


@dataclass
class ATS:
    name: str
    host: str
    submit: str
    success: str          # regex of text shown after a good submit

    def form_url(self, url: str) -> str:
        if self.name == "lever" and not url.rstrip("/").endswith("/apply"):
            return url.rstrip("/") + "/apply"
        if self.name == "ashby" and not url.rstrip("/").endswith("/application"):
            return url.rstrip("/") + "/application"
        return url


ATS_LIST = [
    ATS("greenhouse", "greenhouse.io", "button[type=submit], #submit_app, input[type=submit]",
        r"thank you for applying|application (has been )?(received|submitted)"),
    ATS("lever", "lever.co", "button#btn-submit, button[type=submit]",
        r"application submitted|thanks for applying|thank you for applying"),
    ATS("ashby", "ashbyhq.com", "button:has-text('Submit Application'), button[type=submit]",
        r"thank you for applying|application (was )?submitted|successfully submitted"),
]
GENERIC = ATS("generic", "", "button[type=submit], input[type=submit]", r"thank you|submitted|received")


def detect(url: str, ats: str | None = None) -> ATS:
    for a in ATS_LIST:
        if a.name == ats or (a.host and a.host in url):
            return a
    return GENERIC


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", (s or "").lower())).strip()


@dataclass
class Report:
    url: str
    filled: list = field(default_factory=list)       # [label, value, source]
    held: list = field(default_factory=list)         # consents/demographics waiting on you
    unanswered: list = field(default_factory=list)   # [label, kind, required, options]
    errors: list = field(default_factory=list)
    screenshot: str = ""
    submitted: bool = False
    submit_clicked: bool = False   # True once Submit was pressed, even if no confirmation was seen
    confirmation: str = ""

    @property
    def blocking(self) -> list:
        return [u for u in self.unanswered if u["required"]] + [h for h in self.held if h["required"]]

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__} | {"blocking": self.blocking}


# ---------- answer resolution ----------

FILE_SLOTS = [("cover_letter", r"cover"), ("resume", r"resume|cv\b|curriculum")]


def _lookup(answers: dict, label: str):
    if not answers:
        return None
    n = norm(label)
    table = {norm(k): v for k, v in answers.items()}
    if n in table:
        return table[n]
    # Labels on the page often carry extra helper text, so accept a key contained in the label,
    # but only a substantial one: a short key like "country" must not claim a long question
    # that merely mentions the word.
    for k, v in sorted(table.items(), key=lambda kv: -len(kv[0])):
        if not k:
            continue
        if n.startswith(k) or k.startswith(n):
            return v
        if k in n and (len(k.split()) >= 4 or len(k) >= 0.5 * len(n)):
            return v
    return None


def resolve(f: dict, plan: dict, profile: dict) -> tuple[object, str]:
    """Returns (value, source). source: plan | profile | approved | file | held | ''."""
    label = f["label"]
    if f["kind"] == "file":
        for slot, pat in FILE_SLOTS:
            if re.search(pat, label, re.I) and plan.get("files", {}).get(slot):
                return plan["files"][slot], "file"
        return None, ""
    held = any(re.search(p, label, re.I) for p in profile.get("hold_for_review", []))
    if held:
        v = _lookup(plan.get("approved", {}), label)
        return (v, "approved") if v is not None else (None, "held")
    v = _lookup(plan.get("answers", {}), label)
    if v is not None:
        return v, "plan"
    for rule in profile.get("rules", []):
        if "kinds" in rule and f["kind"] not in rule["kinds"]:
            continue
        if re.search(rule["match"], label, re.I):
            return (profile.get(rule["key"]) if "key" in rule else rule.get("value")), "profile"
    return None, ""


# ---------- filling primitives ----------

def _best_option(options: list[str], want: str) -> str | None:
    w = norm(want)
    opts = [(o, norm(o)) for o in options]
    for test in (lambda o: o == w, lambda o: o.startswith(w), lambda o: w in o, lambda o: o and o in w):
        for raw, o in opts:
            if test(o):
                return raw
    return None


MENU_JS = """(el, tag) => {
  // Options of the menu this combobox opened: its aria-controls listbox, else a menu inside its
  // own select container. Never the page-wide list (a phone-country picker keeps one open).
  const vis = o => o.getClientRects().length > 0;
  const ids = (el.getAttribute("aria-controls") || el.getAttribute("aria-owns") || "").split(" ").filter(Boolean);
  let opts = ids.flatMap(id => [...(document.getElementById(id)?.querySelectorAll("[role=option], [class*=select__option]") || [])]);
  if (!opts.length) {
    const box = el.closest("[class*=select__container], [class*=select-shell], .select, [class*=combobox]");
    if (box) opts = [...box.querySelectorAll("[role=option], [class*=select__option]")];
  }
  opts = [...new Set(opts)].filter(vis);
  opts.forEach((o, i) => o.setAttribute("data-aa-opt", tag + "-" + i));
  return opts.map(o => o.innerText.trim());
}"""


def _menu_texts(page: Page, el, tag: str, wait_ms: int) -> list[str]:
    """Option texts of the dropdown el opened, waiting up to wait_ms for them to render."""
    deadline = time.time() + wait_ms / 1000
    while True:
        texts = el.evaluate(MENU_JS, tag)
        if texts or time.time() > deadline:
            return texts
        page.wait_for_timeout(200)


def _click_option(page: Page, tag: str, index: int):
    page.locator(f'[data-aa-opt="{tag}-{index}"]').first.click()


def _combobox_options(page: Page, f: dict) -> list[str]:
    """Opens a dropdown just long enough to read its choices (Greenhouse loads them on open)."""
    el = page.locator(f'[data-aa-id="{f["id"]}"]')
    try:
        el.click()
        texts = _menu_texts(page, el, f["id"], wait_ms=1500)
        page.keyboard.press("Escape")
        return texts
    except Exception:
        return []


def _set_one(page: Page, f: dict, value) -> str | None:
    """Fills a single field. Returns an error string, or None on success."""
    el = page.locator(f'[data-aa-id="{f["id"]}"]')
    kind = f["kind"]
    if kind == "file":
        el.set_input_files(str(value))
        return None
    if kind in ("text", "textarea"):
        el.fill(str(value))
        return None
    if kind == "select":
        opt = _best_option(f["options"], str(value))
        if not opt:
            return f"no option like {value!r}"
        el.select_option(label=opt)
        return None
    if kind == "combobox":
        # react-select style: type, wait for the menu (autocompletes load it async), click the best match.
        el.click()
        el.fill(str(value))
        texts = _menu_texts(page, el, f["id"], wait_ms=3000)
        if texts:  # Enter is never pressed here: on a plain input it would submit the form
            opt = _best_option(texts, str(value))
            if opt is None:
                page.keyboard.press("Escape")
                return f"no option like {value!r} in {texts[:8]}"
            _click_option(page, f["id"], texts.index(opt))
        else:
            page.keyboard.press("Tab")
        return None
    if kind in ("radio", "checkbox", "checkbox_single"):
        wants = value if isinstance(value, list) else [value]
        if kind == "checkbox_single":
            on = str(wants[0]).lower() in ("yes", "true", "1", "checked", "on") or norm(str(wants[0])) == norm(f["options"][0]["text"])
            box = page.locator(f'[data-aa-id="{f["options"][0]["id"]}"]')
            box.set_checked(on, force=True)
            return None
        texts = [o["text"] for o in f["options"]]
        for w in wants:
            opt = _best_option(texts, str(w))
            if opt is None:
                return f"no option like {w!r} in {texts}"
            oid = f["options"][texts.index(opt)]["id"]
            page.locator(f'[data-aa-id="{oid}"]').check(force=True)
        return None
    return f"unsupported kind {kind}"


def _open(p, url: str, headless: bool):
    # AUTOAPPLY_CHROMIUM points at a system Chromium when Playwright's own download isn't used.
    exe = os.environ.get("AUTOAPPLY_CHROMIUM") or None
    browser = p.chromium.launch(headless=headless, executable_path=exe)
    ctx = browser.new_context(viewport={"width": 1280, "height": 1600})
    page = ctx.new_page()
    page.set_default_timeout(8000)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)  # let React forms mount
    return browser, page


def _fill_page(page: Page, plan: dict, profile: dict, report: Report):
    fields = page.evaluate(SCAN_JS)
    # Upload the resume first: Lever and Ashby parse it and prefill fields we'd overwrite anyway.
    fields.sort(key=lambda f: 0 if f["kind"] == "file" else 1)
    for f in fields:
        value, source = resolve(f, plan, profile)
        info = {"label": f["label"], "kind": f["kind"], "required": f.get("required", False),
                "options": [o if isinstance(o, str) else o["text"] for o in f.get("options", [])][:30]}
        if source == "held":
            report.held.append(info)
            continue
        if value in (None, ""):
            report.unanswered.append(info)
            continue
        try:
            err = _set_one(page, f, value)
        except Exception as e:
            err = str(e).splitlines()[0]
        if err:
            report.errors.append({"label": f["label"], "error": err})
            report.unanswered.append(info)
        else:
            report.filled.append({"label": f["label"], "value": str(value) if source != "file" else Path(str(value)).name, "source": source})


# ---------- public API ----------

def scan(url: str, ats: str | None = None, headless: bool = True) -> list[dict]:
    """Lists the questions on an application form, for whoever writes the plan."""
    a = detect(url, ats)
    with sync_playwright() as p:
        browser, page = _open(p, a.form_url(url), headless)
        fields = page.evaluate(SCAN_JS)
        for f in fields:
            if f["kind"] == "combobox" and not f.get("options"):
                # Empty here means a type-to-search autocomplete (e.g. city): answer with free text.
                f["options"] = _combobox_options(page, f) or []
                f["autocomplete"] = not f["options"]
        browser.close()
    return [{"label": f["label"], "kind": f["kind"], "required": f.get("required", False),
             "options": [o if isinstance(o, str) else o["text"] for o in f.get("options", [])][:50],
             **({"autocomplete": True} if f.get("autocomplete") else {})}
            for f in fields]


def fill(plan: dict, profile: dict, shot_dir: Path, headless: bool = True, _submit: bool = False) -> Report:
    a = detect(plan["url"], plan.get("ats"))
    url = a.form_url(plan["url"])
    report = Report(url=url)
    shot_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with sync_playwright() as p:
        browser, page = _open(p, url, headless)
        _fill_page(page, plan, profile, report)
        report.screenshot = str(shot_dir / f"{a.name}-{stamp}-filled.png")
        page.screenshot(path=report.screenshot, full_page=True)
        if _submit:
            _do_submit(page, a, report, shot_dir, stamp)
        browser.close()
    return report


def handoff(plan: dict, profile: dict, wait: bool = True) -> Report:
    """Fills the form in a visible browser and leaves it open for you to finish and submit.

    Never clicks Submit. Uses the installed Google Chrome when there is one.
    """
    a = detect(plan["url"], plan.get("ats"))
    url = a.form_url(plan["url"])
    report = Report(url=url)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=False, channel="chrome")
        except Exception:
            browser = p.chromium.launch(headless=False, executable_path=os.environ.get("AUTOAPPLY_CHROMIUM") or None)
        page = browser.new_context(viewport=None).new_page()
        page.set_default_timeout(8000)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        _fill_page(page, plan, profile, report)
        if wait:
            page.wait_for_event("close", timeout=0)  # you close the tab when done
        browser.close()
    return report


def submit(plan: dict, profile: dict, shot_dir: Path, headless: bool = True) -> Report:
    """Refills the form and clicks Submit. Refuses unless you approved this plan."""
    if plan.get("approved_by_user") is not True:
        raise PermissionError("plan is not approved_by_user; submit from the review page")
    return fill(plan, profile, shot_dir, headless, _submit=True)


DIAGNOSE_JS = """() => {
  const vis = e => e.getClientRects().length > 0;
  const errors = [...document.querySelectorAll('[aria-invalid="true"], .error, .field-error, [class*="error-message"], [role="alert"]')]
    .filter(vis).map(e => (e.innerText || e.getAttribute("aria-label") || e.name || "").trim()).filter(Boolean);
  const captcha = [...document.querySelectorAll('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[title*="challenge" i]')]
    .some(f => { const r = f.getBoundingClientRect(); return r.width > 100 && r.height > 100; });
  return {errors: [...new Set(errors)].slice(0, 10), captcha, url: location.href};
}"""


def _do_submit(page: Page, a: ATS, report: Report, shot_dir: Path, stamp: str):
    if report.blocking:
        report.errors.append({"label": "(submit)", "error": f"{len(report.blocking)} required question(s) still open"})
        return
    before = page.url
    page.locator(a.submit).first.click()
    report.submit_clicked = True
    try:
        # Success shows as thank-you text, or as a move to a /confirmation-style URL (newer Greenhouse boards).
        page.wait_for_function(
            "([re, before]) => new RegExp(re, 'i').test(document.body.innerText)"
            " || (location.href !== before && /confirm|thank|success|submitted/i.test(location.href))",
            arg=[a.success, before], timeout=30000,
        )
        report.submitted = True
        m = re.search(a.success, page.inner_text("body"), re.I)
        report.confirmation = m.group(0) if m else page.url
    except Exception:
        d = page.evaluate(DIAGNOSE_JS)
        if d["captcha"]:
            why = "a captcha challenge appeared; finish this one from the posting link"
        elif d["errors"]:
            why = "the form showed errors: " + "; ".join(d["errors"])
        else:
            why = "no confirmation seen; it may still have gone through, so check your email before retrying"
        report.errors.append({"label": "(submit)", "error": why})
    report.screenshot = str(shot_dir / f"{a.name}-{stamp}-after-submit.png")
    page.screenshot(path=report.screenshot, full_page=True)
