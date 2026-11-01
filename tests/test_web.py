"""Review page: nothing submits without the tick box, and consent answers come only from the form."""
import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from autoapply.config import Settings
from autoapply.db import DB
from autoapply.fillers import fill
from autoapply.web import make_app

from test_fillers import PROFILE, base_url, pdfs  # noqa: F401  (fixtures)

CONSENT = "I acknowledge the Privacy Notice and consent to processing of my personal data"


def setup(tmp_path, base_url, pdfs):
    s = Settings(root=tmp_path, raw={"data_dir": str(tmp_path / "data")}, profile=PROFILE)
    db = DB(s.data_dir / "t.db")
    url = f"{base_url}/gh/"
    db.upsert_job({"id": "manual:gh", "ats": "greenhouse", "company": "Acme", "title": "AE", "location": "",
                   "url": url, "apply_url": url, "description": ""})
    plan = {"url": url, "ats": "greenhouse", "files": pdfs, "answers": {"Why do you want to work here?": "x"}}
    r = fill(plan, PROFILE, s.data_dir / "screenshots")
    db.update("manual:gh", status="filled", plan_json=plan, report_json=r.as_dict(), screenshot=r.screenshot)
    return db, TestClient(make_app(s, db)), plan


def test_page_shows_held_consent(tmp_path, base_url, pdfs):
    _, c, _ = setup(tmp_path, base_url, pdfs)
    html = c.get("/job/manual:gh").text
    assert "Your call" in html and "Privacy Notice" in html
    assert c.get("/shot/manual:gh").status_code == 200


def test_submit_needs_tick_box(tmp_path, base_url, pdfs):
    db, c, plan = setup(tmp_path, base_url, pdfs)
    c.post("/job/manual:gh", data={"action": "submit", "plan": json.dumps(plan), f"held::{CONSENT}": "yes"})
    assert db.get("manual:gh")["status"] == "filled"


def test_json_box_cannot_smuggle_approval(tmp_path, base_url, pdfs):
    db, c, plan = setup(tmp_path, base_url, pdfs)
    sneaky = {**plan, "approved": {CONSENT: "yes"}}
    c.post("/job/manual:gh", data={"action": "submit", "approve": "yes", "plan": json.dumps(sneaky)})
    job = db.get("manual:gh")
    assert job["status"] == "filled" and "required" in job["error"]


def test_submit_with_consent_and_tick(tmp_path, base_url, pdfs):
    db, c, plan = setup(tmp_path, base_url, pdfs)
    c.post("/job/manual:gh", data={"action": "submit", "approve": "yes", "plan": json.dumps(plan),
                                    f"held::{CONSENT}": "yes", "held::Gender": "Decline to self-identify"})
    job = db.get("manual:gh")
    assert job["status"] == "submitted", job["error"]
    assert json.loads(job["plan_json"])["approved_by_user"] is False


def test_documents_served_only_from_data_dir(tmp_path, base_url, pdfs):
    db, c, plan = setup(tmp_path, base_url, pdfs)  # pdfs live in tmp_path, outside data/
    assert c.get("/file/resume/manual:gh").status_code == 404
    inside = tmp_path / "data" / "applications" / "acme"
    inside.mkdir(parents=True)
    (inside / "r.pdf").write_bytes(b"%PDF-1.4")
    db.update("manual:gh", plan_json={**plan, "files": {"resume": str(inside / "r.pdf"), "cover_letter": "/etc/passwd"}})
    assert c.get("/file/resume/manual:gh").content == b"%PDF-1.4"
    assert c.get("/file/cover_letter/manual:gh").status_code == 404
    assert "r.pdf" in c.get("/job/manual:gh").text


def test_dua_page_and_zip_after_submit(tmp_path, base_url, pdfs):
    import io, zipfile
    db, c, plan = setup(tmp_path, base_url, pdfs)
    c.post("/job/manual:gh", data={"action": "submit", "approve": "yes", "plan": json.dumps(plan),
                                    f"held::{CONSENT}": "yes", "held::Gender": "Decline to self-identify"})
    ev = [e for e in db.events() if e["kind"] == "applied"]
    assert ev and ev[0]["proof"].endswith("_applied.png")
    html = c.get("/dua").text
    assert "Job application" in html and "screenshot" in html
    week = ev[0]["proof"].split("/")[-2]
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/dua/{week}.zip").content))
    names = z.namelist()
    assert f"work-search-{week}.csv" in names and any(n.startswith("proof/") and n.endswith(".png") for n in names)
    assert c.get("/proof/../../etc/passwd").status_code == 404


def test_second_submit_needs_explicit_resubmit(tmp_path, base_url, pdfs):
    db, c, plan = setup(tmp_path, base_url, pdfs)
    rep = json.loads(db.get("manual:gh")["report_json"])
    db.update("manual:gh", report_json={**rep, "submit_clicked": True, "submitted": False})
    c.post("/job/manual:gh", data={"action": "submit", "approve": "yes", "plan": json.dumps(plan),
                                    f"held::{CONSENT}": "yes", "held::Gender": "Decline to self-identify"})
    job = db.get("manual:gh")
    assert job["status"] == "filled" and "already pressed" in job["error"]
    assert "submit again" in c.get("/job/manual:gh").text


def test_captcha_shows_finish_yourself_card(tmp_path, base_url, pdfs):
    db, c, _ = setup(tmp_path, base_url, pdfs)
    assert "Finish this one yourself" not in c.get("/job/manual:gh").text
    rep = json.loads(db.get("manual:gh")["report_json"])
    errs = rep["errors"] + [{"label": "(submit)", "error": "a captcha challenge appeared; finish this one from the posting link"}]
    db.update("manual:gh", report_json={**rep, "errors": errs, "submit_clicked": True})
    html = c.get("/job/manual:gh").text
    assert "Finish this one yourself" in html and "captcha" in html and "Copy" in html


def test_bundle_for_finish_on_mac(tmp_path, base_url, pdfs):
    _, c, _ = setup(tmp_path, base_url, pdfs)
    b = c.get("/bundle/manual:gh").json()
    assert b["job"]["company"] == "Acme" and b["plan"]["approved_by_user"] is False
    assert b["profile"] and "resume" not in b["files"]  # test PDFs sit outside data/, so they aren't served


def test_finish_fills_without_submitting(tmp_path, base_url, pdfs):
    import os
    import pytest
    from autoapply.fillers import handoff
    if not os.environ.get("DISPLAY"):
        pytest.skip("needs a display (run under xvfb-run)")
    plan = {"url": f"{base_url}/gh/", "ats": "greenhouse", "files": pdfs, "answers": {"Why do you want to work here?": "x"}}
    r = handoff(plan, PROFILE, wait=False)
    assert r.filled and not r.submit_clicked and not r.submitted


def test_emails_page_sets_category_and_job(tmp_path, base_url, pdfs):
    from datetime import datetime
    from autoapply import emailsync
    db, c, plan = setup(tmp_path, base_url, pdfs)
    db.update("manual:gh", status="submitted", submitted_at="2026-10-28 10:00")
    emailsync.apply_event(db, db.by_status("submitted"), "<m1>", "Acme", "no-reply@greenhouse.io", "Your application",
                          datetime(2026, 10, 29, 9), "Thanks <b>for applying</b>.")
    html = c.get("/emails").text
    assert "Your application" in html and "Not sorted" in html and "&lt;b&gt;" in html  # escaped body
    r = c.post("/emails/<m1>", data={"kind": "interview", "job": "manual:gh", "show": "waiting"}, follow_redirects=False)
    assert r.status_code == 303
    ev = db.get_event("<m1>")
    assert ev["kind"] == "interview" and ev["manual"] == 1 and db.get("manual:gh")["outcome"] == "interview"
    assert c.post("/emails/<m1>", data={"kind": "bogus"}).status_code == 400
    assert c.post("/emails/<nope>", data={"kind": "ignored"}).status_code == 404
