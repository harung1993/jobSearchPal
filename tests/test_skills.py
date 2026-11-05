"""Skills to learn: what matched roles ask for that the resume and notes don't show."""
from datetime import datetime

from fastapi.testclient import TestClient

from autoapply import skills
from autoapply.web import make_app
from test_daily import make


def test_found_in_avoids_lookalikes():
    got = skills.found_in("Excellent communicator. Airflow, PySpark, Kubernetes; Go and Python a plus. Scalable systems.")
    assert {"Airflow", "Spark", "Kubernetes", "Python"} <= got
    assert "Excel" not in got and "Go" not in got and "Scala" not in got


def test_gaps_page_and_marks(tmp_path):
    s, db, cfg = make(tmp_path)
    (tmp_path / "materials" / "context").mkdir(parents=True, exist_ok=True)
    (tmp_path / "materials" / "context" / "me.md").write_text("I work in SQL, Python and dbt.")
    db.conn.execute("delete from jobs")  # just these four
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for i, d in enumerate(["dbt, Snowflake and Airflow.", "Airflow, Databricks, Python.", "Airflow and Kubernetes; SQL.", ""]):
        db.upsert_job({"id": f"gh:x:{i}", "ats": "greenhouse", "company": f"Co{i}", "title": "Data Engineer", "location": "",
                       "url": "", "apply_url": "", "description": d})
        db.update(f"gh:x:{i}", found_at=now)
    app = tmp_path / "data" / "applications" / "co1"
    app.mkdir(parents=True)
    (app / "notes.md").write_text("| Requirement | Fit | Evidence |\n|---|---|---|\n| Databricks / lakehouse | Gap | none |\n"
                                  "| Healthcare claims domain | Partial | adjacent |\n| Payments domain knowledge | Gap | none |\n| SQL | Good | yes |\n")
    g = skills.gaps(db, s)
    assert g["jobs"] == 3  # the one without a description doesn't count
    names = [i["name"] for i in g["gaps"]]
    assert names[0] == "Airflow" and g["gaps"][0]["count"] == 3 and g["gaps"][0]["pct"] == 100
    assert "Databricks" in names and next(i for i in g["gaps"] if i["name"] == "Databricks")["notes"] == 1
    assert "Healthcare data" in names  # from the notes alone
    assert {"SQL", "Python", "dbt"} <= {i["name"] for i in g["have"]}
    assert ("payments domain knowledge", 1) in g["other_notes"]
    c = TestClient(make_app(s, db))
    assert "Skills to learn" in c.get("/skills").text
    c.post("/skills/mark", data={"skill": "Airflow", "mark": "learning"})
    c.post("/skills/mark", data={"skill": "Kubernetes", "mark": "have"})
    c.post("/skills/mark", data={"skill": "Not a skill", "mark": "have"})
    g = skills.gaps(db, s)
    assert [i["name"] for i in g["learning"]] == ["Airflow"] and "Kubernetes" in {i["name"] for i in g["have"]}
    assert skills.marks(s.data_dir)["have"] == ["Kubernetes"]
    c.post("/skills/mark", data={"skill": "Airflow", "mark": "clear"})
    assert skills.gaps(db, s)["gaps"][0]["name"] == "Airflow"


def test_optional_ai_reading(tmp_path, monkeypatch):
    from autoapply.prefs import Prefs
    s, db, cfg = make(tmp_path)
    db.conn.execute("delete from jobs")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for i, d in enumerate(["Must have Airflow. Snowplow a plus.", "PySpark required; Six Sigma preferred."]):
        db.upsert_job({"id": f"gh:y:{i}", "ats": "greenhouse", "company": f"Co{i}", "title": "Data Engineer", "location": "",
                       "url": "", "apply_url": "", "description": d})
        db.update(f"gh:y:{i}", found_at=now)
    prefs = Prefs(s.data_dir, s.raw)
    c = TestClient(make_app(s, db))
    page = c.get("/skills").text
    assert "Read skills with AI" in page and "off, optional" in page and "tokens" in page
    est = skills.estimate(db, prefs)
    assert est["backlog"] == 2 and est["per_job"] > 100 and est["tokens_month"] == est["per_job"] * est["per_day"] * 30
    # Off: the CLI path does nothing.
    assert not skills.ai_on(prefs)
    c.post("/skills/ai", data={"action": "on", "per_day": "10"})
    prefs = Prefs(s.data_dir, s.raw)
    assert skills.ai_on(prefs) and skills.ai_per_day(prefs) == 10
    reply = ('{"jobs": [{"id": "j0", "skills": [{"name": "Apache Airflow", "required": true}, {"name": "Snowplow", "required": false}]},'
             ' {"id": "j1", "skills": [{"name": "PySpark", "required": true}, {"name": "Six Sigma", "required": false}]}, {"id": "j9"}]}')
    calls = []
    monkeypatch.setattr(skills, "_ask", lambda name, p, prompt: calls.append(prompt) or (reply, 0.0002, 0.0, 1800))
    monkeypatch.setattr(skills, "source", lambda db, p: "openrouter")
    r = skills.tag_jobs(db, prefs)
    assert r == {"read": 2, "left": 0, "error": None, "source": "openrouter"} and len(calls) == 1
    assert "Must have Airflow" in calls[0] and "about you" not in calls[0].lower()
    read = skills.ai_skills(db)
    assert read["gh:y:0"] == [{"name": "Airflow", "required": True}, {"name": "Snowplow", "required": False}]
    assert read["gh:y:1"][0] == {"name": "Spark", "required": True}
    g = skills.gaps(db, s)
    by = {i["name"]: i for i in g["gaps"] + g["have"]}
    assert g["read_by_ai"] == 2 and by["Airflow"]["required"] == 1 and by["Snowplow"]["area"] == "Other"
    assert skills.estimate(db, prefs)["used"] == {"jobs": 2, "tokens": 1800, "cost": 0.0002, "est": 0.0}
    c.post("/skills/ai", data={"action": "off"})
    assert not skills.ai_on(Prefs(s.data_dir, s.raw))


def test_parse_ai_ignores_junk():
    batch = [{"id": "a"}]
    assert skills.parse_ai("not json", batch) == {}
    assert skills.parse_ai('{"jobs": [{"id": "j5", "skills": ["x"]}]}', batch) == {}
    assert skills.parse_ai('Sure! {"jobs": [{"id": "j0", "skills": ["dbt", "DBT", "Kubernetes (k8s)"]}]}', batch) == {
        "a": [{"name": "dbt", "required": True}, {"name": "Kubernetes", "required": True}]}
