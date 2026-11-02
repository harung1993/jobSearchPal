"""Morning brief email: today's picks, new replies and where every application stands.

Sent to yourself over Gmail SMTP with the same app password the email sync uses, so it lands in
the inbox your morning brief already reads. Subject starts "Job search brief" for easy filtering.
"""
from __future__ import annotations

import os
import smtplib
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from html import escape

from . import stats
from .db import DB


def _recent_events(db: DB, hours: int = 30) -> list[dict]:
    cut = datetime.now() - timedelta(hours=hours)
    out = []
    for e in db.events():
        try:
            at = datetime.strptime(e["at"][:16], "%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            continue
        if at >= cut and e["kind"] != "applied":
            out.append(e)
    return out


def _co(name: str) -> str:
    # Board slugs come through lower-case ("acme"); tracker rows keep their own spelling.
    return name.title() if name and name.islower() else (name or "")


def compose(db: DB, review_url: str = "", today: date | None = None, label: str = "Work search",
            floor: int = 3, data_dir=None) -> tuple[str, str, str]:
    """Returns (subject, text, html)."""
    s = stats.compute(db, today, floor=floor)
    from . import nudges
    ready, news = s["ready"], _recent_events(db)
    todo = nudges.compute(db, data_dir) if data_dir else []
    left = max(0, s["dua_floor"] - s["this_week"])
    dua = f"{s['this_week']} of {s['dua_floor']} this week" + (" (done)" if not left else f", {left} to go")
    subject = f"Job search brief: {len(ready)} ready, {len(news)} new repl{'y' if len(news) == 1 else 'ies'}, {label} {s['this_week']}/{s['dua_floor']}" + (
        f", {len(todo)} to do" if todo else "")
    base = review_url.rstrip("/")

    t = [f"{label}: {dua}", ""]
    if todo:
        t.append(f"TO DO ({len(todo)})")
        t += [f"- {n['title']} · {n['about']} · {n['why']}" for n in todo[:8]]
        t += [f"  {base}/#todo" if base else "", ""]
    t.append(f"READY FOR YOU ({len(ready)})")
    for j in ready:
        t.append(f"- {_co(j['company'])} · {j['title']}" + (f" · {j['pay']}" if j["pay"] else ""))
        t.append(f"  review: {base}/job/{j['id']}   finish on Mac: autoapply-finish {j['id']}")
    if not ready:
        t.append("- nothing new today")
    from . import prep
    recent = ((today or date.today()) - timedelta(days=30)).isoformat()
    ivs = [r for r in prep.interviews(db, data_dir) if r["latest"] >= recent][:5] if data_dir else []
    if ivs:
        t += ["", f"INTERVIEWS ({len(ivs)})"]
        for r in ivs:
            st = {"ready": f"prep pack: {base}/prep/{r['job']['id']}", "update": "prep pack updating next run",
                  "due": "prep pack next run"}[r["state"]]
            t.append(f"- {_co(r['job']['company'])} · {r['job']['title']} · {st}")
    t += ["", f"NEW REPLIES ({len(news)})"]
    t += [f"- {e['kind']}: {e['sender'].split(' <')[0]} · {e['subject']}" for e in news] or ["- none"]
    t += ["", f"APPLICATIONS: {s['applied']} sent · {s['interviews']} interviews · {s['rejected']} rejected · "
              f"{s['waiting']} waiting · {s['no_reply']} no reply · {s['response_rate']}% response rate"]
    for j in s["applications"][:15]:
        t.append(f"- {j['applied_label']}: {_co(j['company'])} · {j['title']} · {j['state']}")
    stale = [r for r in s["runs"] if r["state"] in ("fail", "stale")]
    if stale:
        t += ["", "CHECK: " + "; ".join(f"{r['label']} {r['state']} ({r['when']})" for r in stale)]
    if base:
        t += ["", f"Dashboard: {base}/"]
    text = "\n".join(t)

    def li(items):
        return "".join(f"<li>{i}</li>" for i in items)
    h = [f"<p style='font-size:15px'><b>{escape(label)}:</b> {escape(dua)}</p>",
         f"<h3>Ready for you ({len(ready)})</h3><ul>"]
    h.append(li([f"<b>{escape(_co(j['company']))}</b> · {escape(j['title'])}{' · ' + escape(j['pay']) if j['pay'] else ''}<br>"
                 f"<a href='{escape(base)}/job/{escape(j['id'])}'>Review</a> · finish on Mac: <code>autoapply-finish {escape(j['id'])}</code>"
                 for j in ready]) or "<li>Nothing new today</li>")
    if todo:
        h.append(f"</ul><h3>To do ({len(todo)})</h3><ul>")
        h.append(li([f"<b>{escape(n['title'])}</b> · {escape(n['about'])}<br><span style='color:#666'>{escape(n['why'])}</span>"
                     for n in todo[:8]]))
    if ivs:
        h.append(f"</ul><h3>Interviews ({len(ivs)})</h3><ul>")
        h.append(li([f"<b>{escape(_co(r['job']['company']))}</b> · {escape(r['job']['title'])} · " + (
            f"<a href='{escape(base)}/prep/{escape(r['job']['id'])}'>Prep pack</a>" if r["state"] == "ready" else
            ("prep pack updating next run" if r["state"] == "update" else "prep pack next run")) for r in ivs]))
    h.append(f"</ul><h3>New replies ({len(news)})</h3><ul>")
    h.append(li([f"<b>{escape(e['kind'])}</b>: {escape(e['sender'].split(' <')[0])} · {escape(e['subject'])}" for e in news]) or "<li>None</li>")
    h.append(f"</ul><h3>Applications</h3><p>{s['applied']} sent · {s['interviews']} interviews · {s['rejected']} rejected · "
             f"{s['waiting']} waiting · {s['no_reply']} no reply · {s['response_rate']}% response rate</p>")
    h.append("<table cellpadding='4' style='border-collapse:collapse;font-size:13px'>" + "".join(
        f"<tr><td style='color:#666'>{escape(j['applied_label'])}</td><td>{escape(_co(j['company']))}</td>"
        f"<td>{escape(j['title'])}</td><td><b>{escape(j['state'])}</b></td></tr>" for j in s["applications"][:15]) + "</table>")
    if stale:
        h.append("<p style='color:#b42318'><b>Check:</b> " + escape("; ".join(f"{r['label']} {r['state']} ({r['when']})" for r in stale)) + "</p>")
    if base:
        h.append(f"<p><a href='{escape(base)}/'>Open the dashboard</a></p>")
    html = "<div style='font:14px/1.5 -apple-system,Segoe UI,Arial,sans-serif;max-width:640px'>" + "".join(h) + "</div>"
    return subject, text, html


def send(subject: str, text: str, html: str) -> str:
    user, pw = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if not (user and pw):
        raise RuntimeError("set GMAIL_USER and GMAIL_APP_PASSWORD in .env")
    to = os.environ.get("BRIEF_TO") or user
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(user, pw)
        smtp.send_message(msg)
    return to
