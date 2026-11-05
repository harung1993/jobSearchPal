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
