"""The staged daily run: pre-ranking, stage list, picks, prompts, quick pick and the per-stage cost table."""
import json
from datetime import datetime

import pytest
import yaml

from autoapply import cli, rank
from autoapply.config import Settings
from autoapply.db import DB
from autoapply.prefs import Prefs


def upd(p, **kw):
    p.data.update(kw)
    p.save()


def make(tmp_path):
    (tmp_path / "materials" / "context").mkdir(parents=True)
    (tmp_path / "materials" / "context" / "me.md").write_text("Analytics engineer. dbt, Snowflake, Airflow, Python.")
    s = Settings(root=tmp_path, raw={"data_dir": str(tmp_path / "data"),
                                     "search": {"title_keywords": ["analytics engineer"]}}, profile={})
    db = DB(s.data_dir / "autoapply.db")
    jobs = [("gh:a:1", "Acme", "Senior Analytics Engineer", "dbt Snowflake Airflow models " * 50),
            ("gh:b:2", "Globex", "Line Cook", "kitchen prep grill"),
            ("wd:c:3", "Initech", "Analytics Engineer", "dbt Python")]
    for jid, co, title, desc in jobs:
        db.upsert_job({"id": jid, "ats": "workday" if jid.startswith("wd") else "greenhouse", "company": co,
                       "title": title, "location": "Remote", "url": "u", "apply_url": "u", "description": desc})
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump(s.raw))
    return s, db, cfg


def test_candidates_rank_and_trim(tmp_path):
    s, db, _ = make(tmp_path)
    c = rank.candidates(db, s, limit=2, desc_chars=100)
    assert [x["id"] for x in c] == ["gh:a:1", "wd:c:3"]
    assert len(c[0]["description"]) <= 101 and c[0]["description"].endswith("…")
    assert db.get("gh:b:2")["score"] < db.get("gh:a:1")["score"]


def test_stages_picks_and_prompt(tmp_path, capsys, monkeypatch):
    s, db, cfg = make(tmp_path)
    cli.main(["--config", str(cfg), "daily-stages"])
    assert capsys.readouterr().out.split()[-1] == "rank"
    (s.data_dir / "daily").mkdir(exist_ok=True)
    (s.data_dir / "daily" / "picks.json").write_text(json.dumps(
        [{"id": "gh:a:1"}, {"id": "nope"}, {"id": "wd:c:3"}, {"id": "gh:b:2"}, {"id": "x"}, {"id": "y"}]))
    cli.main(["--config", str(cfg), "daily-picks"])
    out = capsys.readouterr().out.split()
    assert out[:2] == ["gh:a:1", "wd:c:3"] and "nope" not in out
    cli.main(["--config", str(cfg), "daily-prompt", "apply", "gh:a:1"])
    text = capsys.readouterr().out
    assert "gh:a:1" in text and "{{JOB_ID}}" not in text
    with pytest.raises(SystemExit):
        cli.main(["--config", str(cfg), "daily-prompt", "../daily"])
    with pytest.raises(SystemExit):
        cli.main(["--config", str(cfg), "daily-prompt", "apply", "missing:job"])
    upd(Prefs(s.data_dir), discover_weekday=datetime.now().isoweekday())
    cli.main(["--config", str(cfg), "daily-prompt", "rank"])
    assert "Weekly" in capsys.readouterr().out
    upd(Prefs(s.data_dir), discover_weekday=0)
    cli.main(["--config", str(cfg), "daily-prompt", "rank"])
    out = capsys.readouterr().out
    assert "Weekly" not in out and "{{DISCOVER}}" not in out


def test_light_model_for_light_stages(tmp_path):
    p = Prefs(tmp_path)
    upd(p, daily_model="opus", daily_light_model="sonnet")
    assert p.daily(0, "apply")["model"] == "opus" and p.daily(0, "rank")["model"] == "sonnet"
    upd(p, daily_light_model="")
    assert p.daily(0, "drafts")["model"] == "opus"


def test_usage_by_stage(tmp_path):
    db = DB(tmp_path / "u.db")
    db.log_usage("daily", "claude:sonnet:rank", 0, 0, 0, 0, None, 0.5, 1000)
    db.log_usage("daily", "claude:opus:apply", 0, 0, 0, 0, None, 2.0, 5000)
    db.log_usage("daily", "claude:opus:apply", 0, 0, 0, 0, None, 2.0, 5000)
    db.log_usage("daily", "openrouter:deepseek/deepseek-chat:free:rank (openrouter)", 0, 0, 0.001, 0, None, 0.001, 300)
    by = {b["stage"]: b for b in db.usage_by_stage()}
    assert by["apply"]["n"] == 2 and by["apply"]["tokens"] == 10000
    assert by["rank"]["n"] == 2 and "deepseek/deepseek-chat:free" in by["rank"]["models"]
    assert db.usage_summary()["daily"]["calls"] == 1 and db.usage_summary()["daily"]["sessions"] == 4


def test_quick_pick(tmp_path, capsys, monkeypatch):
    s, db, cfg = make(tmp_path)
    with pytest.raises(SystemExit) as e:  # off by default
        cli.main(["--config", str(cfg), "quick-pick"])
    assert e.value.code == 2
    p = Prefs(s.data_dir)
    upd(p, quick_pick_model="google/gemini-2.5-flash", discover_weekday=0, monthly_api_budget=5)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key-123456")  # restored after the test
    sent = {}

    def fake_post(url, body, key, timeout=120):
        sent.update(body)
        reply = {"picks": [{"id": "wd:c:3", "why": "dbt"}, {"id": "made:up", "why": "x"}, {"id": "gh:a:1", "why": "fit"}]}
        return {"choices": [{"message": {"content": json.dumps(reply)}}], "usage": {"total_tokens": 4321, "cost": 0.0007}}
    monkeypatch.setattr("autoapply.classify._post", fake_post)
    cli.main(["--config", str(cfg), "quick-pick"])
    assert capsys.readouterr().out.strip() == "Picked: Initech (Analytics Engineer), Acme (Senior Analytics Engineer)"
    picks = json.loads((s.data_dir / "daily" / "picks.json").read_text())
    assert [x["id"] for x in picks] == ["wd:c:3", "gh:a:1"] and picks[0]["by_hand"] is True
    assert sent["model"] == "google/gemini-2.5-flash" and "Analytics engineer. dbt" in sent["messages"][0]["content"]
    by = {b["stage"]: b for b in db.usage_by_stage()}
    assert by["rank"]["tokens"] == 4321 and round(by["rank"]["cost"], 4) == 0.0007

    monkeypatch.setattr("autoapply.classify._post", lambda *a, **k: {"choices": [{"message": {"content": "sorry"}}]})
    with pytest.raises(SystemExit) as e:  # a bad reply falls back to the agent
        cli.main(["--config", str(cfg), "quick-pick"])
    assert e.value.code == 1
    upd(p, discover_weekday=datetime.now().isoweekday())
    with pytest.raises(SystemExit) as e:  # discovery day: the agent picks
        cli.main(["--config", str(cfg), "quick-pick"])
    assert e.value.code == 2
