"""autoapply: the hands for the daily agent run. The agent decides; this opens, fills and uploads.

  autoapply find                      pull new matching roles from boards and feeds
  autoapply board add ATS SLUG        watch another company's job board (checked first)
  autoapply board list                boards being watched
  autoapply alerts [--days N]         recent job-alert emails as text, to pull roles from
  autoapply sync-email [--days N] [--rebuild] [--no-review]  gather ATS/recruiter emails, then have the
                                      model sort them (--rebuild refiles; your corrections are kept)
  autoapply review-emails             sort the emails still waiting (sources and order: Settings page)
  autoapply daily-agent               the agent (claude or codex) and model picked for the daily run
  autoapply emails [--pending]        filed emails with their kind, as JSON
  autoapply emails set ID KIND [--job JOB_ID|none]  settle one by hand (confirmed, interview, rejected,
                                      offer, recruiter, ignored)
  autoapply import-history FILE.json  load past applications (tracker rows) into the dashboard
  autoapply log-run KIND ok|fail [MSG]  record a run (daily.sh uses it) for the dashboard
  autoapply add URL [--company C --title T]   track a role found elsewhere
  autoapply list [--status S]         show tracked roles
  autoapply list --prep               interviews that need a prep pack, with where to write it
  autoapply show JOB_ID               full posting text, for tailoring
  autoapply skip JOB_ID [--reason R]  mark a role not worth applying to
  autoapply scan URL [--ats A]        list the form's questions as JSON
  autoapply fill PLAN.json [--job ID] fill the form, upload PDFs, screenshot, stop before Submit
  autoapply brief [--send]            morning brief: today's picks, new replies, application status
  autoapply serve                     review page: check answers, decide consents, press Submit
  autoapply finish JOB_ID             (on your Mac) open the filled form in Chrome; you submit it
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from . import config, finder
from .db import DB
from .fillers import fill, scan
from .fillers.base import detect


def _out(obj):
    print(json.dumps(obj, indent=1, default=str))


def _review(s, db) -> dict:
    """Model review of waiting emails; a failure leaves them waiting and is logged, not raised."""
    from . import classify
    from .prefs import Prefs
    try:
        r = classify.review(db, Prefs(s.data_dir, s.raw))
        summary = ", ".join(f"{v} {k}" for k, v in r.items() if k != "errors")
        if r.get("errors"):
            summary += " · failed: " + ", ".join(r["errors"])
        db.log_run("classify", not r.get("errors") or r.get("left", 0) == 0, summary)
        return r
    except Exception as e:
        db.log_run("classify", False, str(e)[:300])
        return {"error": str(e)[:300], "left": len(db.pending_events())}


def _summary(out: dict) -> str:
    filed = sum(out["filed"].values())
    r = out.get("reviewed") or {}
    bits = [f"{filed} new email{'' if filed == 1 else 's'}"]
    sorted_n = sum(v for k, v in r.items() if k not in ("left", "error", "errors"))
    if sorted_n:
        bits.append(f"{sorted_n} sorted")
    if r.get("left"):
        bits.append(f"{r['left']} waiting for review")
    if r.get("error"):
        bits.append("sorting failed: " + r["error"][:80])
    for src, err in (r.get("errors") or {}).items():
        bits.append(f"{src} failed")
    return ", ".join(bits)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="autoapply", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("find")
    a = sub.add_parser("board"); a.add_argument("action", choices=["add", "list"]); a.add_argument("ats", nargs="?", choices=sorted(finder.FETCHERS)); a.add_argument("slug", nargs="?")
    a = sub.add_parser("alerts"); a.add_argument("--days", type=int, default=1)
    a = sub.add_parser("sync-email"); a.add_argument("--days", type=int, default=60); a.add_argument("--rebuild", action="store_true"); a.add_argument("--no-review", action="store_true")
    sub.add_parser("review-emails")
    sub.add_parser("daily-model"); sub.add_parser("daily-agent")
    a = sub.add_parser("emails"); a.add_argument("action", nargs="?", choices=["set"]); a.add_argument("event_id", nargs="?")
    a.add_argument("kind", nargs="?"); a.add_argument("--job", default=""); a.add_argument("--pending", action="store_true")
    a = sub.add_parser("import-history"); a.add_argument("file")
    a = sub.add_parser("log-run"); a.add_argument("kind"); a.add_argument("result", choices=["ok", "fail"]); a.add_argument("msg", nargs="?", default="")
    a = sub.add_parser("add"); a.add_argument("url"); a.add_argument("--company", default=""); a.add_argument("--title", default="")
    a = sub.add_parser("list"); a.add_argument("--status"); a.add_argument("--prep", action="store_true")
    a = sub.add_parser("show"); a.add_argument("job_id")
    a = sub.add_parser("skip"); a.add_argument("job_id"); a.add_argument("--reason", default="")
    a = sub.add_parser("scan"); a.add_argument("url"); a.add_argument("--ats")
    a = sub.add_parser("fill"); a.add_argument("plan"); a.add_argument("--job")
    a = sub.add_parser("brief"); a.add_argument("--send", action="store_true")
    sub.add_parser("serve")
    a = sub.add_parser("finish"); a.add_argument("job_id"); a.add_argument("--server", default=os.environ.get("AUTOAPPLY_SERVER", ""))
    args = ap.parse_args(argv)
    if args.cmd == "finish":
        return finish(args.job_id, args.server)

    s = config.load(args.config)
    db = DB(s.data_dir / "autoapply.db")
    headless = s.raw.get("browser", {}).get("headless", True)

    if args.cmd == "find":
        new = finder.run(s, db)
        db.log_run("find", True, f"{len(new)} new roles")
        _out([{k: j[k] for k in ("id", "company", "title", "location", "url")} for j in new])
    elif args.cmd == "log-run":
        db.log_run(args.kind, args.result == "ok", args.msg)
    elif args.cmd == "board":
        if args.action == "list":
            _out(finder.boards(s))
        else:
            if not (args.ats and args.slug):
                sys.exit("usage: autoapply board add ATS SLUG")
            try:
                n = finder.add_board(s, args.ats, args.slug)
            except Exception as e:
                sys.exit(f"{args.ats}:{args.slug} doesn't answer ({e}); check the slug in the careers URL")
            print(f"watching {args.ats}:{args.slug} ({n} open roles)")
    elif args.cmd == "alerts":
        from . import alerts
        _out(alerts.fetch(args.days, s.raw.get("alert_senders")))
    elif args.cmd == "sync-email":
        from . import emailsync
        try:
            if args.rebuild:
                emailsync.rebuild(db, args.days)
            counts = emailsync.sync(db, args.days, s.raw.get("recruiter_senders"), s.data_dir / "proof")
        except Exception as e:
            db.log_run("sync-email", False, str(e))
            raise
        out = {"filed": counts}
        if not args.no_review:
            out["reviewed"] = _review(s, db)
        db.log_run("sync-email", True, _summary(out))
        _out(out)
    elif args.cmd in ("daily-model", "daily-agent"):
        from .prefs import DAILY_AGENTS, Prefs
        d = Prefs(s.data_dir, s.raw).data
        agent = d.get("daily_agent") if d.get("daily_agent") in DAILY_AGENTS else "claude"
        model = d.get("daily_codex_model" if agent == "codex" else "daily_model") or ""
        print(model if args.cmd == "daily-model" else f"{agent} {model}".strip())
    elif args.cmd == "review-emails":
        _out(_review(s, db))
    elif args.cmd == "emails":
        from . import emailsync
        if args.action == "set":
            if not (args.event_id and args.kind):
                sys.exit("usage: autoapply emails set EVENT_ID KIND [--job JOB_ID|none]")
            job = None if args.job == "none" else args.job
            try:
                ev = emailsync.review_event(db, args.event_id, args.kind, job_id=job, manual=True)
            except (KeyError, ValueError) as e:
                sys.exit(str(e))
            _out({k: ev[k] for k in ("id", "kind", "job_id", "subject")})
        else:
            rows = db.pending_events() if args.pending else db.events(all=True)
            _out([{k: e[k] for k in ("id", "at", "kind", "guess", "reviewed", "manual", "job_id", "sender", "subject")}
                  for e in rows if e["kind"] != "applied"])
    elif args.cmd == "import-history":
        from . import history
        print(f"imported {history.import_file(db, Path(args.file))} past applications")
    elif args.cmd == "add":
        jid = "manual:" + hashlib.sha1(args.url.encode()).hexdigest()[:10]
        db.upsert_job({"id": jid, "ats": detect(args.url).name, "company": args.company, "title": args.title,
                       "location": "", "url": args.url, "apply_url": args.url, "description": ""})
        print(jid)
    elif args.cmd == "list" and args.prep:
        from . import prep
        _out(prep.needed(db, s.data_dir))
    elif args.cmd == "list":
        rows = db.by_status(args.status) if args.status else db.all()
        _out([{k: r[k] for k in ("id", "status", "company", "title", "location", "found_at")} for r in rows])
    elif args.cmd == "show":
        _out(db.get(args.job_id) or sys.exit(f"no job {args.job_id}"))
    elif args.cmd == "skip":
        db.update(args.job_id, status="skipped", error=args.reason)
    elif args.cmd == "scan":
        _out(scan(args.url, args.ats, headless=headless))
    elif args.cmd == "fill":
        plan = json.loads(Path(args.plan).read_text())
        # Consent answers and the go-ahead only ever come from you on the review page.
        plan.pop("approved_by_user", None)
        plan.pop("approved", None)
        if args.job and not plan.get("url"):
            plan["url"] = db.get(args.job)["apply_url"]
        report = fill(plan, s.profile, s.data_dir / "screenshots", headless)
        db.log_run("fill", not report.errors, f"{plan.get('url', '')[:80]}: {len(report.filled)} filled, {len(report.errors)} errors")
        if args.job:
            db.update(args.job, status="filled", plan_json=plan, report_json=report.as_dict(),
                      screenshot=report.screenshot, error=None)
        _out(report.as_dict())
    elif args.cmd == "brief":
        from . import brief
        from .web import work_search
        label, floor = work_search(s)
        subject, text, html = brief.compose(db, os.environ.get("REVIEW_URL", ""), label=label, floor=floor,
                                          data_dir=s.data_dir)
        if args.send:
            to = brief.send(subject, text, html)
            db.log_run("brief", True, f"sent to {to}")
            print(f"sent: {subject}")
        else:
            print(subject + "\n\n" + text)
    elif args.cmd == "serve":
        import uvicorn
        from .web import make_app
        r = s.raw.get("review", {})
        # In Docker the dashboard listens on all interfaces inside the container; compose decides what's exposed.
        host = os.environ.get("AUTOAPPLY_HOST") or r.get("host", "127.0.0.1")
        uvicorn.run(make_app(s, db), host=host, port=int(os.environ.get("AUTOAPPLY_PORT") or r.get("port", 8787)))


def finish(job_id: str, server: str):
    """Runs on your laptop: pulls the plan and PDFs from the review page and fills a visible Chrome."""
    import httpx
    from .fillers import handoff
    if not server:
        sys.exit("set AUTOAPPLY_SERVER (e.g. http://<server-ip>:8787) or pass --server")
    params = {"t": os.environ["AUTOAPPLY_TOKEN"]} if os.environ.get("AUTOAPPLY_TOKEN") else {}
    with httpx.Client(base_url=server.rstrip("/"), params=params, timeout=60) as c:
        r = c.get(f"/bundle/{job_id}")
        if r.status_code != 200:
            sys.exit(f"review page said {r.status_code} for {job_id}")
        b = r.json()
        folder = Path.home() / "autoapply-finish" / job_id.replace(":", "_").replace("/", "_")
        folder.mkdir(parents=True, exist_ok=True)
        plan = b["plan"]
        plan["files"] = {}
        for slot, name in b["files"].items():
            dest = folder / Path(name).name
            dest.write_bytes(c.get(f"/file/{slot}/{job_id}").raise_for_status().content)
            plan["files"][slot] = str(dest)
    job = b["job"]
    print(f"Opening {job['company']} · {job['title']} in Chrome. Check it, answer any captcha, and press Submit yourself.")
    print("Close the tab when you're done.")
    report = handoff(plan, b["profile"])
    left = [u["label"] for u in report.unanswered]
    if left:
        print("Left for you to answer:", "; ".join(left))
    print(f"Once it's sent, press 'I applied by hand' on {server.rstrip('/')}/job/{job_id} (the confirmation email also marks it).")


if __name__ == "__main__":
    main()
