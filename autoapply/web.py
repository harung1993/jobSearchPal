"""Review page. The only place a plan gets approved_by_user and a Submit gets clicked."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import time
import zipfile
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader
from starlette.concurrency import run_in_threadpool

from . import stats
from .db import DB
from .fillers import fill, submit

env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)
env.globals["ws_label"] = "Work search"


def page(name: str, **ctx) -> HTMLResponse:
    return HTMLResponse(env.get_template(name).render(**ctx))


KIND_LABELS = [("confirmed", "Confirmation"), ("interview", "Interview"), ("rejected", "Rejection"), ("offer", "Offer"),
               ("recruiter", "Recruiter"), ("ignored", "Not job-related")]


def work_search(settings) -> tuple[str, int]:
    """Label and weekly minimum for the work-search log, e.g. {label: DUA, weekly_minimum: 3}."""
    ws = settings.raw.get("work_search") or {}
    return ws.get("label", "Work search"), int(ws.get("weekly_minimum", 3))


def make_app(settings, db: DB) -> FastAPI:
    app = FastAPI()
    shots = settings.data_dir / "screenshots"
    headless = settings.raw.get("browser", {}).get("headless", True)

    @app.middleware("http")
    async def auth(request: Request, call_next):
        # Optional shared secret: open /?t=TOKEN once, then a cookie carries it. Read on every request,
        # because the Setup page can create it.
        token = os.environ.get("AUTOAPPLY_TOKEN")
        if token:
            given = request.query_params.get("t") or request.cookies.get("aa")
            if given != token:
                return HTMLResponse("forbidden", status_code=403)
        resp = await call_next(request)
        if token and request.query_params.get("t"):
            resp.set_cookie("aa", token, httponly=True, samesite="strict")
        return resp

    def load(job_id):
        job = db.get(job_id)
        if not job:
            raise HTTPException(404)
        return job, json.loads(job["plan_json"] or "{}"), json.loads(job["report_json"] or "{}")

    ws_label, floor = work_search(settings)
    env.globals["ws_label"] = ws_label

    def page(name: str, **ctx) -> HTMLResponse:  # noqa: F811  every page shows the waiting-email badge
        from . import setup
        return HTMLResponse(env.get_template(name).render(pending_count=len(db.pending_events()),
                                                          setup_left=len(setup.status(settings, os.environ)["left"]), **ctx))

    @app.get("/")
    def index():
        if settings.raw.get("_from_example"):
            return RedirectResponse("/setup", status_code=303)  # fresh install
        from . import prep
        from . import nudges
        todo = nudges.compute(db, settings.data_dir)
        for n in todo:
            n["compose"] = nudges.gmail_compose_url(n)
        return page("index.html", s=stats.compute(db, floor=floor), interviews=prep.interviews(db, settings.data_dir)[:6],
                    todo=todo, nav="dashboard")

    @app.post("/nudge/{key}")
    def nudge_action(key: str, action: str = Form(...), days: int = Form(3)):
        from datetime import timedelta
        if action == "done":
            db.set_nudge(key, "done")
        elif action == "snooze":
            db.set_nudge(key, "snoozed", (datetime.now() + timedelta(days=max(1, min(days, 30)))).strftime("%Y-%m-%d %H:%M"))
        elif action == "reopen":
            db.set_nudge(key, None)
        else:
            raise HTTPException(400)
        return RedirectResponse("/#todo", status_code=303)

    @app.get("/stats")
    def stats_page():
        return page("stats.html", s=stats.detail(db, floor=floor), nav="stats")

    @app.get("/job/{job_id:path}")
    def job_page(job_id: str):
        job, plan, report = load(job_id)
        shown = {k: v for k, v in plan.items() if k not in ("approved", "approved_by_user")}
        files = [(slot, Path(p).name) for slot, p in plan.get("files", {}).items() if _servable(p)]
        return page("job.html", nav="", job=job, report=report, approved=plan.get("approved", {}), files=files,
                    notes=_notes(plan), plan_text=json.dumps(shown, indent=1), handoff=_handoff(job, report))

    def _handoff(job: dict, report: dict) -> str:
        # The bot never works around captchas: when one blocks Submit, you finish on the site.
        if job["status"] == "submitted" or report.get("submitted"):
            return ""
        errs = " ".join(str(e.get("error", "")) for e in report.get("errors", []))
        if "captcha" in errs.lower():
            return "The site showed a captcha, which only you can answer."
        if report.get("submit_clicked"):
            return "Submit was pressed but no confirmation came back."
        return ""

    @app.get("/shot/{job_id:path}")
    def shot(job_id: str):
        job, _, _ = load(job_id)
        p = Path(job["screenshot"] or "")
        if not p.exists() or shots.resolve() not in p.resolve().parents:
            raise HTTPException(404)
        return FileResponse(p)

    def _servable(p: str) -> bool:
        # Only files inside data/ or materials/: the answers box is editable, so never serve arbitrary paths.
        try:
            rp = Path(p).resolve()
        except (OSError, TypeError):
            return False
        roots = [settings.data_dir.resolve(), (settings.root / "materials").resolve()]
        return rp.is_file() and any(r in rp.parents for r in roots)

    def _notes(plan: dict) -> str:
        # notes.md sits next to the tailored resume (data/applications/<slug>/notes.md).
        for p in plan.get("files", {}).values():
            n = Path(p).parent / "notes.md"
            if _servable(str(n)):
                return n.read_text()[:20000]
        return ""

    @app.get("/bundle/{job_id:path}")
    def bundle(job_id: str):
        """Everything `autoapply finish` needs to fill the form in a visible browser on your laptop."""
        job, plan, _ = load(job_id)
        plan = {**plan, "approved_by_user": False}
        slots = {slot: Path(p).name for slot, p in plan.get("files", {}).items() if _servable(p)}
        return {"job": {k: job[k] for k in ("id", "company", "title", "url", "apply_url")},
                "plan": plan, "files": slots, "profile": settings.profile}

    @app.get("/file/{slot}/{job_id:path}")
    def doc(slot: str, job_id: str):
        _, plan, _ = load(job_id)
        p = plan.get("files", {}).get(slot)
        if not p or not _servable(p):
            raise HTTPException(404)
        return FileResponse(p)

    proof_root = settings.data_dir / "proof"

    def _record_applied(job: dict, shot: str | None):
        """An 'applied' event for the DUA record, with the after-submit screenshot as proof."""
        when = datetime.now()
        proof = None
        if shot and Path(shot).exists():
            from .emailsync import week_dir
            d = week_dir(proof_root, when)
            d.mkdir(parents=True, exist_ok=True)
            proof = str(d / f"{when:%Y-%m-%d_%H%M}_{job['company']}_applied.png")
            shutil.copy(shot, proof)
        db.add_event({"id": f"applied:{job['id']}", "job_id": job["id"], "kind": "applied", "at": f"{when:%Y-%m-%d %H:%M}",
                      "sender": job["company"], "subject": f"Applied: {job['title']}", "proof": proof})

    @app.post("/sync")
    async def sync_now():
        from . import emailsync
        try:
            counts = await run_in_threadpool(emailsync.sync, db, 7, settings.raw.get("recruiter_senders"), proof_root)
            db.log_run("sync-email", True, ", ".join(f"{v} {k}" for k, v in counts.items()) or "nothing new")
            from .cli import _review
            await run_in_threadpool(_review, settings, db)
        except Exception as e:  # shown on the strip; no traceback page
            db.log_run("sync-email", False, str(e))
        return RedirectResponse("/emails" if db.pending_events() else "/", status_code=303)

    @app.get("/emails")
    def emails(show: str = "waiting"):
        from . import classify
        every = [e for e in db.events(all=True) if e["kind"] != "applied"]
        groups = {"waiting": [e for e in every if not e["reviewed"]],
                  "replies": [e for e in every if e["reviewed"] and e["kind"] in ("confirmed", "interview", "rejected", "offer")],
                  "recruiter": [e for e in every if e["reviewed"] and e["kind"] == "recruiter"],
                  "ignored": [e for e in every if e["reviewed"] and e["kind"] == "ignored"],
                  "all": every}
        show = show if show in groups else "waiting"
        tabs = [("waiting", "Not sorted", len(groups["waiting"])), ("replies", "Replies", len(groups["replies"])),
                ("recruiter", "Recruiters", len(groups["recruiter"])), ("ignored", "Not job-related", len(groups["ignored"])),
                ("all", "All", len(every))]
        from .prefs import PROVIDERS, Prefs
        chain = Prefs(settings.data_dir, settings.raw).chain()
        name = " then ".join(PROVIDERS[p]["name"] for p in chain[:2]) if chain else "Nobody (no model source is on)"
        jobs = sorted(db.by_status("submitted", "filled"), key=lambda j: (j["company"] or "").lower())
        return page("emails.html", rows=groups[show][:300], tabs=tabs, show=show, kinds=KIND_LABELS, jobs=jobs,
                    classifier=name, nav="emails")

    @app.post("/emails/{event_id:path}")
    def set_email(event_id: str, kind: str = Form(""), job: str = Form(""), show: str = Form("waiting")):
        from . import emailsync
        ev = db.get_event(event_id)
        if not ev:
            raise HTTPException(404)
        job_id = None if job == "none" else (job or "")
        if not kind:  # only the application changed on an unsorted email
            if ev["reviewed"]:
                kind = ev["kind"]
            else:
                db.set_event(event_id, job_id=job_id or None)
                return RedirectResponse(f"/emails?show={show}", status_code=303)
        try:
            emailsync.review_event(db, event_id, kind, job_id=job_id, manual=True)
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e))
        return RedirectResponse(f"/emails?show={show}", status_code=303)

    @app.get("/prep/{job_id:path}")
    def prep_page(job_id: str):
        from . import prep
        job = db.get(job_id)
        if not job:
            raise HTTPException(404)
        row = next((r for r in prep.interviews(db, settings.data_dir) if r["job"]["id"] == job_id), None)
        p = prep.prep_path(settings.data_dir, job)
        body = prep.render(p.read_text()) if p.exists() else ""
        return page("prep.html", job=job, row=row, body=body, nav="dashboard")

    @app.get("/settings")
    def settings_page(msg: str = ""):
        from . import classify
        from .prefs import DAILY_AGENTS, DAILY_MODELS, PROVIDERS, Prefs
        pr = Prefs(settings.data_dir, settings.raw)
        st = classify.status(pr)
        orm = classify.openrouter_models(settings.data_dir / "openrouter-models.json") if (
            "openrouter" in pr.data["enabled"] or pr.key("OPENROUTER_API_KEY")) else []
        u30 = db.usage_summary(30)
        daily = u30.get("daily") or {}
        sorting = [v for k, v in u30.items() if k != "daily"]
        costs = {"daily_runs": daily.get("calls") or 0, "daily_tokens": daily.get("tokens") or 0, "daily_est": daily.get("est") or 0,
                 "sort_est": sum(v.get("est") or 0 for v in sorting), "sort_cost": sum(v.get("cost") or 0 for v in sorting),
                 "month_spend": db.spend_this_month(), "by_day": db.usage_by_day(14)}
        return page("settings.html", p=pr.data, prefs=pr, providers=PROVIDERS, status=st, usage=u30, costs=costs,
                    or_models=orm, daily_models=DAILY_MODELS, daily_agents=DAILY_AGENTS, key_hint=pr.key_hint("OPENROUTER_API_KEY"),
                    pending=len(db.pending_events()), msg=msg, nav="settings")

    @app.post("/settings")
    async def save_settings(request: Request):
        from . import classify
        from .prefs import DAILY_AGENTS, DAILY_MODELS, PROVIDERS, Prefs
        f = await request.form()
        pr = Prefs(settings.data_dir, settings.raw)
        d = pr.data
        d["enabled"] = [p for p in d["order"] if f.get(f"on::{p}")]
        for p in PROVIDERS:
            if f"model::{p}" in f:
                d["models"][p] = str(f.get(f"model::{p}") or "").strip()[:120]
        try:
            d["min_confidence"] = max(0.0, min(1.0, float(f.get("min_confidence") or d["min_confidence"])))
        except ValueError:
            pass
        if f.get("daily_model", "") in DAILY_MODELS:
            d["daily_model"] = f.get("daily_model", "")
        if f.get("daily_agent") in DAILY_AGENTS:
            d["daily_agent"] = f.get("daily_agent")
        if "daily_codex_model" in f:
            cm = str(f.get("daily_codex_model") or "").strip()
            if re.fullmatch(r"[A-Za-z0-9._:/-]{0,80}", cm):  # it ends up on daily.sh's command line
                d["daily_codex_model"] = cm
        for k, lo, hi in (("picks_per_day", 0, 5), ("prep_per_day", 0, 5), ("drafts_per_day", 0, 20)):
            try:
                d[k] = max(lo, min(hi, int(f.get(k, d[k]))))
            except (TypeError, ValueError):
                pass
        if "limits_form" in f:
            d["prep_enabled"], d["drafts_enabled"] = bool(f.get("prep_enabled")), bool(f.get("drafts_enabled"))
        try:
            d["monthly_api_budget"] = max(0.0, min(1000.0, float(f.get("monthly_api_budget", d["monthly_api_budget"]))))
        except (TypeError, ValueError):
            pass
        url = str(f.get("ollama_url") or "").strip()
        if url.startswith(("http://", "https://")):
            d["ollama_url"] = url.rstrip("/")
        key = str(f.get("openrouter_key") or "").strip()
        if key:
            pr.set_key("OPENROUTER_API_KEY", key)
        if f.get("forget_key"):
            pr.set_key("OPENROUTER_API_KEY", "")
        action, msg = str(f.get("action") or "save"), "Saved."
        verb, _, name = action.partition(":")
        if name in d["order"] and verb in ("up", "down"):
            i = d["order"].index(name)
            j = i - 1 if verb == "up" else i + 1
            if 0 <= j < len(d["order"]):
                d["order"][i], d["order"][j] = d["order"][j], d["order"][i]
            msg = f"Moved {PROVIDERS[name]['name']} {verb}."
        pr.save()
        if verb == "test" and name in PROVIDERS:
            r = await run_in_threadpool(classify.test_source, name, pr)
            d.setdefault("tests", {})[name] = {**r, "at": datetime.now().strftime("%b %-d, %-I:%M %p"), "model": pr.model(name)}
            pr.save()
            msg = ""
        elif verb == "sort":
            from .cli import _review
            r = await run_in_threadpool(_review, settings, db)
            n = sum(v for k, v in r.items() if k not in ("left", "errors", "error"))
            msg = f"Sorted {n} email{'' if n == 1 else 's'}; {r.get('left', 0)} still waiting." + (
                " Failed: " + "; ".join(f"{k}: {v[:80]}" for k, v in r["errors"].items()) if r.get("errors") else "")
        from urllib.parse import quote
        return RedirectResponse(f"/settings?msg={quote(msg)}" + (f"#src-{name}" if name else ""), status_code=303)

    # ---- first-run setup: writes profile.yaml, config.yaml, materials/ and data/secrets.json ----
    def _back(msg: str, anchor: str = "") -> RedirectResponse:
        from urllib.parse import quote
        return RedirectResponse(f"/setup?msg={quote(msg)}" + (f"#{anchor}" if anchor else ""), status_code=303)

    @app.get("/setup")
    def setup_page(msg: str = "", err: str = "", t: str = ""):
        from . import setup
        from .finder import FEEDS
        from .prefs import Prefs
        st = setup.status(settings, os.environ)
        prof = setup.load_profile(settings.root)
        if not st["steps"]["profile"]["done"]:
            # Blank out the example's Jane Doe values, then prefill from the resume.
            example = setup._yaml(settings.root / "profile.example.yaml")
            prof = {k: ("" if example.get(k) == v and k not in ("work_authorized", "requires_sponsorship", "willing_to_relocate",
                                                                "how_did_you_hear") else v) for k, v in prof.items()}
            for k, v in setup.resume_contact(setup.base_resume(settings.root)).items():
                prof[k] = prof.get(k) or v
        cfg = setup.load_config(settings.root) if not st["fresh"] else {}
        search = cfg.get("search") or {}
        boards = "\n".join(f"{b['ats']} {b['slug']}" for b in cfg.get("boards") or [])
        pr = Prefs(settings.data_dir, settings.raw)
        return page("setup.html", nav="setup", st=st, prof=prof, fields=setup.PROFILE_FIELDS, yes_no=setup.YES_NO,
                    titles=", ".join(search.get("title_keywords") or []), exclude=", ".join(search.get("exclude_keywords") or []),
                    us_only=search.get("us_only", True), boards=boards, feeds=setup.FEED_LABELS,
                    feeds_on=cfg.get("feeds", list(FEEDS)) if cfg else list(FEEDS), ws=cfg.get("work_search") or {},
                    about=setup.load_about(settings.data_dir), about_fields=setup.ABOUT_FIELDS,
                    resume=setup.base_resume(settings.root), gmail_user=os.environ.get("GMAIL_USER", ""),
                    gmail_env=pr.from_env("GMAIL_APP_PASSWORD"), token_env=pr.from_env("AUTOAPPLY_TOKEN"),
                    msg=msg, err=err, link=(os.environ.get("REVIEW_URL") or "/").split("?")[0] + "?t=" + t
                    if t and t == os.environ.get("AUTOAPPLY_TOKEN") else "")

    @app.post("/setup/profile")
    async def setup_profile(request: Request):
        from . import setup
        f = dict(await request.form())
        if not (str(f.get("first_name") or "").strip() and str(f.get("last_name") or "").strip()
                and "@" in str(f.get("email") or "")):
            return _back("First name, last name and email are needed.", "profile")
        setup.save_profile(settings, f)
        setup.name_placeholder_resume(settings.root, settings.profile.get("last_name", ""))
        return _back("Saved your contact details to profile.yaml.", "interests")

    @app.post("/setup/interests")
    async def setup_interests(request: Request):
        from . import setup
        f = dict(await request.form())
        if not setup._lines(str(f.get("titles") or "")):
            return _back("Add at least one job title to look for.", "interests")
        r = setup.save_interests(settings, f)
        env.globals["ws_label"] = (settings.raw.get("work_search") or {}).get("label", "Work search")
        msg = "Saved your job interests to config.yaml."
        if r["bad"]:
            msg += " Couldn't read: " + "; ".join(r["bad"][:5]) + ". Use a careers-page URL or 'greenhouse slug'."
        return _back(msg, "about")

    @app.post("/setup/resume")
    async def setup_resume(docx: UploadFile = File(None), pdf: UploadFile = File(None)):
        from . import setup
        limit = 10 * 1024 * 1024
        d = await docx.read(limit + 1) if docx and docx.filename else None
        p = await pdf.read(limit + 1) if pdf and pdf.filename else None
        if not d and not p:
            return _back("Pick your resume's .docx file first.", "resume")
        if (d and len(d) > limit) or (p and len(p) > limit):
            return _back("That file is over 10 MB.", "resume")
        if p and not d and not setup.base_resume(settings.root):
            return _back("The .docx is needed: tailored resumes are built from it. The PDF is optional.", "resume")
        last = settings.profile.get("last_name") or ""
        r = await run_in_threadpool(setup.save_resume, settings.root, last, d, p)
        if r["error"]:
            return _back(r["error"], "resume")
        if not last:  # name it after the resume's own header when contact details aren't saved yet
            guess = setup.resume_contact(setup.base_resume(settings.root)).get("last_name", "")
            if renamed := setup.name_placeholder_resume(settings.root, guess):
                r = {"docx": renamed, "pdf": renamed.replace(".docx", ".pdf") if r["pdf"] else None}
        made = " and ".join(x for x in (r["docx"], r["pdf"]) if x)
        return _back(f"Saved {made} in materials/." + ("" if r["pdf"] or p else
                     " No PDF was made (LibreOffice isn't installed here), so upload one too."), "profile")

    @app.post("/setup/about")
    async def setup_about(request: Request):
        from . import setup
        setup.save_about(settings.root, settings.data_dir, dict(await request.form()))
        return _back("Saved to materials/context/setup.md. The agent reads it before every application.", "gmail")

    @app.post("/setup/gmail")
    async def setup_gmail(request: Request):
        from . import setup
        from .prefs import Prefs
        f = await request.form()
        pr = Prefs(settings.data_dir, settings.raw)
        user = str(f.get("gmail_user") or "").strip()
        pw = re.sub(r"\s", "", str(f.get("gmail_password") or ""))
        if f.get("action") == "forget":
            for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "BRIEF_TO"):
                pr.set_key(k, "")
            return _back("Removed the saved Gmail login.", "gmail")
        pw = pw or (os.environ.get("GMAIL_APP_PASSWORD") or "")
        if "@" not in user or not pw:
            return _back("Enter your Gmail address and an app password.", "gmail")
        problem = await run_in_threadpool(setup.test_gmail, user, pw)
        if problem:
            return _back(f"Not saved: {problem}", "gmail")
        if not pr.from_env("GMAIL_USER"):
            pr.set_key("GMAIL_USER", user)
        if not pr.from_env("GMAIL_APP_PASSWORD"):
            pr.set_key("GMAIL_APP_PASSWORD", pw)
        brief_to = str(f.get("brief_to") or "").strip()
        if "@" in brief_to and not pr.from_env("BRIEF_TO"):
            pr.set_key("BRIEF_TO", brief_to)
        return _back("Gmail works. Job alerts and replies will be read (read-only), and the brief sent from it.", "agent")

    @app.post("/setup/token")
    def setup_token():
        import secrets as _secrets
        from urllib.parse import quote
        from .prefs import Prefs
        pr = Prefs(settings.data_dir, settings.raw)
        if os.environ.get("AUTOAPPLY_TOKEN"):
            raise HTTPException(409, "a dashboard password is already set")
        t = _secrets.token_urlsafe(24)
        pr.set_key("AUTOAPPLY_TOKEN", t)
        msg = "Password set. This browser is signed in; open the link below once on each other device."
        return RedirectResponse(f"/setup?t={quote(t)}&msg={quote(msg)}#token", status_code=303)

    @app.get("/dua")
    def dua():
        return page("dua.html", weeks=stats.dua_weeks(db), floor=floor, nav="work")

    @app.get("/dua/{week}.zip")
    def dua_zip(week: str):
        wk = next((w for w in stats.dua_weeks(db) if w["start"].isoformat() == week), None)
        if not wk:
            raise HTTPException(404)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(["Date", "Activity Type", "Company", "Position", "How", "Contact", "Result", "Proof files"])
            for r in wk["rows"]:
                w.writerow([r["date"].isoformat(), r["type"], r["company"], r["position"], r["how"], r["contact"],
                            r["result"], "; ".join(Path(p).name for p in r["proof"])])
            z.writestr(f"work-search-{week}.csv", out.getvalue())
            d = proof_root / week
            for f in sorted(d.iterdir()) if d.is_dir() else []:
                z.write(f, f"proof/{f.name}")
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"content-disposition": f'attachment; filename="work-search-week-{week}.zip"'})

    @app.get("/proof/{week}/{name}")
    def proof_file(week: str, name: str):
        p = (proof_root / week / name).resolve()
        if proof_root.resolve() not in p.parents or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p)

    @app.post("/job/{job_id:path}")
    async def act(job_id: str, request: Request, action: str = Form(...), plan: str = Form("{}"), approve: str = Form("")):
        job, old_plan, old_report = load(job_id)
        form = await request.form()
        try:
            new_plan = json.loads(plan)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"answers JSON is invalid: {e}")
        new_plan.setdefault("answers", {})
        for k, v in form.items():
            if k.startswith("ans::") and v:
                new_plan["answers"][k[5:]] = v
        # Held answers come only from this form, never from the JSON box.
        new_plan["approved"] = {k[6:]: v for k, v in form.items() if k.startswith("held::") and v}
        new_plan["approved_by_user"] = False

        if action == "skip":
            db.update(job_id, status="skipped", plan_json=new_plan)
        elif action == "manual":
            db.update(job_id, status="submitted", plan_json=new_plan, error=None,
                      submitted_at=time.strftime("%Y-%m-%d %H:%M"))
            _record_applied(job, None)
        elif action == "refill":
            r = await run_in_threadpool(fill, new_plan, settings.profile, shots, headless)  # sync Playwright can't share the event loop
            db.update(job_id, status="filled", plan_json=new_plan, report_json=r.as_dict(), screenshot=r.screenshot, error=None)
        elif action == "submit":
            if old_report.get("submit_clicked") and not old_report.get("submitted") and form.get("resubmit") != "yes":
                db.update(job_id, plan_json=new_plan,
                          error="Submit was already pressed once without a confirmation. Check your email, then tick 'submit again' if it really didn't go through.")
                return RedirectResponse(f"/job/{job_id}", status_code=303)
            if approve != "yes":
                db.update(job_id, plan_json=new_plan, error="Tick the review box to submit.")
                return RedirectResponse(f"/job/{job_id}", status_code=303)
            new_plan["approved_by_user"] = True
            r = await run_in_threadpool(submit, new_plan, settings.profile, shots, headless)
            done = r.submitted
            if r.submit_clicked and not done:
                r.errors.append({"label": "(submit)", "error": "Submit was pressed but not confirmed. Don't retry until you've checked your email."})
            new_plan["approved_by_user"] = False  # one approval, one click
            db.update(job_id, status="submitted" if done else "filled", plan_json=new_plan, report_json=r.as_dict(),
                      screenshot=r.screenshot, error=None if done else "; ".join(e["error"] for e in r.errors),
                      submitted_at=time.strftime("%Y-%m-%d %H:%M") if done else None)
            if done:
                _record_applied(job, r.screenshot)
        return RedirectResponse(f"/job/{job_id}", status_code=303)

    return app
