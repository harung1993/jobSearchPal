"""Leads: every role in job-alert emails is kept, de-duplicated across sources, and can be starred."""
import json
from datetime import datetime

from fastapi.testclient import TestClient

from autoapply import cli, leads
from autoapply.web import make_app
from test_daily import make

LINKEDIN = """Your job alert for analytics engineer in Greater Boston
Senior Analytics Engineer
Hooli
Cambridge, MA

This company is actively hiring
View job: https://www.linkedin.com/comm/jobs/view/111/?trackingId=abc&refId=x

---------------------------------------------------------

Staff Data Engineer, Platform
Initech
Boston, MA

1 connection
View job: https://www.linkedin.com/comm/jobs/view/222/?trackingId=def

---------------------------------------------------------

See all jobs on LinkedIn: https://www.linkedin.com/comm/jobs/search-results/?keywords=x
"""

INDEED = """Hi Jane,

Your background as an analytics engineer could be a strong match for this Senior Analytics Engineer role at Hooli. With your expertise in Snowflake and dbt, you could excel in this position.

Senior Analytics Engineer
Hooli
Remote
Salary: $150,000 - $180,000 a year
Work setting: Remote

View job: https://cts.indeed.com/v3/abc
Apply now: https://cts.indeed.com/v3/def
"""


def test_parse_linkedin_and_indeed():
    li = leads.parse(LINKEDIN)
    assert [(j["company"], j["title"], j["location"]) for j in li] == [
        ("Hooli", "Senior Analytics Engineer", "Cambridge, MA"), ("Initech", "Staff Data Engineer, Platform", "Boston, MA")]
    assert li[0]["url"] == "https://www.linkedin.com/comm/jobs/view/111/"
    ind = leads.parse(INDEED)
    assert len(ind) == 1 and ind[0]["company"] == "Hooli" and ind[0]["salary"] == "$150,000 - $180,000 a year"


def test_save_dedupes_and_tracks(tmp_path, capsys):
    s, db, cfg = make(tmp_path)
    now = datetime(2026, 11, 3, 9)
    assert leads.save(db, leads.parse(LINKEDIN), "Linkedin", now, s.raw["search"], set()) == 2
    assert leads.save(db, leads.parse(INDEED), "Indeed", now, s.raw["search"], set()) == 0  # same Hooli role
    rows = {r["company"]: r for r in leads.rows(db)}
    assert rows["Hooli"]["seen"] == 2 and rows["Hooli"]["source"] == "Indeed, Linkedin" and rows["Hooli"]["salary"]
    assert rows["Hooli"]["score"] > 0
    leads.set_status(db, rows["Initech"]["key"], "starred")
    cli.main(["--config", str(cfg), "leads", "lookup"])
    assert capsys.readouterr().out.strip() == "1"
    picked = json.loads((s.data_dir / "daily" / "alert-roles.json").read_text())
    assert picked[0]["company"] == "Initech" and picked[0]["link"].endswith("/222/")
    db.upsert_job({"id": "gh:initech:9", "ats": "greenhouse", "company": "Initech", "title": "Staff Data Engineer, Platform",
                   "location": "Boston", "url": "u", "apply_url": "u", "description": ""})
    cli.main(["--config", str(cfg), "leads", "lookup"])
    assert capsys.readouterr().out.strip() == "0"  # tracked now: nothing left to look up
    assert {r["company"]: r for r in leads.rows(db)}["Initech"]["job_id"] == "gh:initech:9"


def test_leads_page_star_and_dismiss(tmp_path):
    s, db, cfg = make(tmp_path)
    leads.save(db, leads.parse(LINKEDIN), "Linkedin", datetime(2026, 11, 3, 9), {}, set())
    c = TestClient(make_app(s, db))
    html = c.get("/leads").text
    assert "Hooli" in html and "Initech" in html and "To look at" in html
    k = leads.rows(db)[0]["key"]
    c.post("/leads", data={"key": k, "status": "starred"})
    assert "★" in c.get("/leads?show=starred").text
    c.post("/leads", data={"key": k, "status": "dismissed"})
    assert k not in [r["key"] for r in leads.rows(db) if r["status"] != "dismissed"]
    assert c.post("/leads", data={"key": k, "status": "approved"}).status_code == 400
