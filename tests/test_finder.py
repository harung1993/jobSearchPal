"""Finder parsing and filtering, with the ATS APIs stubbed out."""
from autoapply import finder
from autoapply.config import Settings
from autoapply.db import DB


class Resp:
    def __init__(self, data): self.data = data
    def raise_for_status(self): pass
    def json(self): return self.data


GH = {"jobs": [
    {"id": 1, "title": "Senior Analytics Engineer", "location": {"name": "Remote - US"},
     "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "content": "&lt;p&gt;dbt &amp; Snowflake&lt;/p&gt;"},
    {"id": 2, "title": "Analytics Engineer Intern", "location": {"name": "Remote"}, "absolute_url": "u", "content": ""},
    {"id": 3, "title": "Staff Data Engineer", "location": {"name": "London, UK"}, "absolute_url": "u", "content": ""},
]}


def test_greenhouse_parse_filter_and_dedupe(monkeypatch, tmp_path):
    monkeypatch.setattr(finder.httpx, "get", lambda *a, **k: Resp(GH))
    s = Settings(root=tmp_path, raw={"data_dir": str(tmp_path), "boards": [{"ats": "greenhouse", "slug": "acme"}],
                                     "search": {"title_keywords": ["analytics engineer", "data engineer"],
                                                "exclude_keywords": ["intern"], "locations_ok": ["remote", "boston"]}},
                 profile={})
    db = DB(tmp_path / "t.db")
    new = finder.run(s, db)
    assert [j["id"] for j in new] == ["greenhouse:acme:1"]
    assert new[0]["description"] == "dbt & Snowflake"
    assert new[0]["apply_url"].endswith("for=acme&token=1")
    assert finder.run(s, db) == []  # already seen


def test_feeds_and_boards_file(monkeypatch, tmp_path):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if "remoteok" in url:
            return Resp([{"legal": "notice"}, {"id": "9", "position": "Senior Analytics Engineer", "company": "Acme",
                                               "location": "USA", "url": "https://remoteok.com/9", "apply_url": "https://x/apply"}])
        if "workable" in url:
            return Resp({"jobs": [{"shortcode": "AB1", "title": "Data Engineer", "telecommuting": True, "country": "United States",
                                   "url": "https://apply.workable.com/w/j/AB1", "application_url": "https://apply.workable.com/w/j/AB1/apply"}]})
        return Resp({"jobs": []})

    monkeypatch.setattr(finder.httpx, "get", fake_get)
    s = Settings(root=tmp_path, raw={"data_dir": str(tmp_path), "feeds": ["remoteok"],
                                     "search": {"title_keywords": ["analytics engineer", "data engineer"], "locations_ok": ["remote"]}},
                 profile={})
    assert finder.add_board(s, "workable", "w") == 1
    finder.add_board(s, "workable", "W")  # same board, different case: not added twice
    assert finder.boards(s) == [{"ats": "workable", "slug": "w"}]
    ids = sorted(j["id"] for j in finder.run(s, DB(tmp_path / "t.db")))
    assert ids == ["remoteok:9", "workable:w:AB1"]


def test_location_filter_is_us_only():
    ok = finder.location_ok
    for loc in ["Remote - US", "Remote, USA", "Boston, MA", "Remote", "Remote (US/Canada)", "United States", "", None, "New York, NY"]:
        assert ok(loc, {}), loc
    for loc in ["Remote Poland", "Remote - Spain", "Remote São Paulo", "London, UK", "Remote, EMEA", "Toronto, Canada", "Bengaluru"]:
        assert not ok(loc, {}), loc


def test_null_location_does_not_crash(monkeypatch, tmp_path):
    data = {"jobs": [{"id": 5, "title": "Analytics Engineer", "location": None, "absolute_url": "u", "content": None}]}
    monkeypatch.setattr(finder.httpx, "get", lambda *a, **k: Resp(data))
    s = Settings(root=tmp_path, raw={"data_dir": str(tmp_path), "boards": [{"ats": "greenhouse", "slug": "a"}],
                                     "search": {"title_keywords": ["analytics engineer"]}}, profile={})
    assert [j["id"] for j in finder.run(s, DB(tmp_path / "t.db"))] == ["greenhouse:a:5"]
