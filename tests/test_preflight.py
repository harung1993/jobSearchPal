"""Pre-submit checks: documents and form must pass before Submit unlocks."""
import json
import os
import time

import docx

from autoapply import preflight


def setup(tmp_path, monkeypatch, resume_text, cover_text, pages=(2, 1)):
    m = tmp_path / "materials"
    m.mkdir()
    d = docx.Document()
    d.add_paragraph("Lead Data Scientist, Northeastern University  Jul 2020 - Oct 2026")
    d.add_paragraph("Data Scientist, Acme  Aug 2019 – Jun 2020")
    d.save(m / "Doe Resume - Base.docx")
    app = tmp_path / "data" / "applications" / "warp"
    app.mkdir(parents=True)
    for n in ("Doe Resume - Warp", "Doe Cover Letter - Warp"):
        (app / f"{n}.docx").write_bytes(b"x")
        time.sleep(0.01)
        (app / f"{n}.pdf").write_bytes(b"%PDF")
    texts = {"Doe Resume - Warp.pdf": (resume_text, pages[0]), "Doe Cover Letter - Warp.pdf": (cover_text, pages[1])}
    monkeypatch.setattr(preflight, "pdf_text", lambda p: texts[p.name])
    monkeypatch.setattr(preflight, "page_texts", lambda p: [])
    plan = {"files": {"resume": str(app / "Doe Resume - Warp.pdf"), "cover_letter": str(app / "Doe Cover Letter - Warp.pdf")}}
    return app, plan


PROFILE = {"first_name": "Jane", "last_name": "Doe", "email": "jane@example.com", "phone": "(555) 010-0000"}
GOOD = "Jane Doe jane@example.com\nLead Data Scientist Jul 2020 - Oct 2026\nData Scientist Aug 2019 - Jun 2020"


def test_good_application_passes(tmp_path, monkeypatch):
    _, plan = setup(tmp_path, monkeypatch, GOOD, "Dear Warp team, Jane Doe jane@example.com")
    report = {"filled": [{"label": "Email", "value": "jane@example.com"}], "unanswered": [{"label": "Pronouns", "required": False}]}
    checks = preflight.check({"company": "Warp"}, plan, report, PROFILE, tmp_path, ["GitLab"])
    assert not [c for c in checks if not c["ok"]], [c for c in checks if not c["ok"]]


def test_problems_lock_submit(tmp_path, monkeypatch):
    app, plan = setup(tmp_path, monkeypatch, "Jane Doe jane@example.com\nLead Data Scientist Jul 2020 - Oct 2026 [Company] TODO",
                      "Dear GitLab team, Jane Doe", pages=(3, 1))
    now = time.time()
    os.utime(app / "Doe Cover Letter - Warp.docx", (now + 30, now + 30))  # docx edited after the PDF
    report = {"unanswered": [{"label": "Why Warp?", "required": True}],
              "held": [{"label": "Gender", "required": True, "value": ""}],
              "filled": [{"label": "Email", "value": "old@example.com"}]}
    checks = preflight.check({"company": "Warp"}, plan, report, PROFILE, tmp_path, ["GitLab"])
    failed = {c["name"] for c in preflight.blocking(checks)}
    assert {"At most 2 pages", "No placeholder text", "Every job date from your base resume",
            "PDF is up to date with the Word file", "Names Warp", "Every required question answered",
            "Required consents chosen by you", "Email matches your profile"} <= failed
    assert any(c["name"] == "No other company's name" and not c["ok"] and not c["hard"] for c in checks)
    missing = next(c for c in checks if c["name"] == "Every job date from your base resume")["detail"]
    assert "aug 2019 - jun 2020" in missing
