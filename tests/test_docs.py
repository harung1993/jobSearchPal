"""Resume builder: outline the base resume, then build tailored docs from a short JSON spec."""
import datetime as dt

import docx
import pytest

from autoapply import docs


def _para(d, text, bold=None, style=None):
    p = d.add_paragraph(style=style)
    p.add_run(text).bold = bold
    return p


def _two_run(d, label, text):
    p = d.add_paragraph()
    p.add_run(f"{label}: ").bold = True
    p.add_run(text)


def _role(d, title, dates, bold=True):
    p = d.add_paragraph()
    p.add_run(title).bold = bold
    if dates:
        p.add_run("\t").bold = bold
        p.add_run(dates).bold = bold
    return p


@pytest.fixture
def base(tmp_path):
    """Same shape as a real base resume: name, contact, headline, summary, skills, roles with groups, education."""
    d = docx.Document()
    _para(d, "Pat Example", bold=True)
    _para(d, "Boston, MA | linkedin.com/in/pat")
    _para(d, "DATA ENGINEER · ANALYTICS", bold=True)
    _para(d, "Original summary.")
    _para(d, "CORE SKILLS", bold=True)
    _two_run(d, "Platforms", "Snowflake, dbt")
    _two_run(d, "Languages", "SQL, Python")
    _two_run(d, "BI", "Power BI")
    _para(d, "PROFESSIONAL EXPERIENCE", bold=True)
    _role(d, "Lead Data Scientist | Big University", "2020 – 2026")
    _role(d, "Data Platform", None, bold=False)
    for b in ("Built the platform.", "Set standards.", "Automated releases."):
        _para(d, b, style="List Bullet")
    _role(d, "Data Science", None, bold=False)
    for b in ("Led analytics.", "Built a model."):
        _para(d, b, style="List Bullet")
    _role(d, "Founder | palStack.io", "2024 – Present")
    _para(d, "Founded a company.", style="List Bullet")
    _para(d, "EDUCATION", bold=True)
    _para(d, "M.S., Computer Science (2017)")
    path = tmp_path / "Example Resume - Base.docx"
    d.save(path)
    return path


def texts(path):
    return [p.text for p in docx.Document(path).paragraphs]


def test_outline_reads_headline_summary_skills_and_experience(base):
    o = docs.outline(base)
    assert o["headline"] == "DATA ENGINEER · ANALYTICS"
    assert o["summary"] == "Original summary."
    assert o["skills"] == [{"label": "Platforms", "text": "Snowflake, dbt"},
                           {"label": "Languages", "text": "SQL, Python"},
                           {"label": "BI", "text": "Power BI"}]
    assert [(b["heading"], b["kind"], len(b["bullets"])) for b in o["experience"]] == [
        ("Lead Data Scientist | Big University", "role", 0),
        ("Data Platform", "group", 3),
        ("Data Science", "group", 2),
        ("Founder | palStack.io", "role", 1),
    ]


def test_build_rewrites_content_and_keeps_roles_dates_and_education(base, tmp_path):
    spec = {"company": "Acme", "resume": {
        "headline": "ANALYTICS ENGINEER · SEMANTIC LAYER",
        "summary": "New summary.",
        "skills": [{"label": "Languages", "text": "SQL (advanced), Python"},
                   {"label": "Semantic Layer", "text": "Snowflake semantic views"}],
        "experience": {"Data Platform": ["Set standards first.", "Built the platform.", "Added a grain bullet."],
                       "Data Science": [],
                       "founder": ["Founded a company; manage a team of 4."]}}}
    r = docs.build(base, spec, tmp_path / "out", last_name="Example", pdf=False)
    t = texts(r["resume"])
    assert r["resume"].endswith("Example Resume - Acme.docx")
    assert t[2] == "ANALYTICS ENGINEER · SEMANTIC LAYER" and t[3] == "New summary."
    i = t.index("CORE SKILLS")
    assert t[i + 1:i + 3] == ["Languages: SQL (advanced), Python", "Semantic Layer: Snowflake semantic views"]
    assert "Platforms: Snowflake, dbt" not in t
    j = t.index("Data Platform")
    assert t[j + 1:j + 4] == ["Set standards first.", "Built the platform.", "Added a grain bullet."]
    assert "Automated releases." not in t
    # An empty list drops a group and its bullets; role and date lines stay.
    assert "Data Science" not in t and "Led analytics." not in t
    assert "Lead Data Scientist | Big University\t2020 – 2026" in t
    assert t[t.index("Founder | palStack.io\t2024 – Present") + 1] == "Founded a company; manage a team of 4."
    assert t[-2:] == ["EDUCATION", "M.S., Computer Science (2017)"]
    d = docx.Document(r["resume"])
    new = next(p for p in d.paragraphs if p.text == "Added a grain bullet.")
    assert new.style.name == "List Bullet" and not new.runs[0].bold
    skill = next(p for p in d.paragraphs if p.text.startswith("Semantic Layer"))
    assert skill.runs[0].bold and not skill.runs[1].bold


def test_left_out_parts_stay_as_in_the_base(base, tmp_path):
    r = docs.build(base, {"company": "Acme", "resume": {"summary": "Only this."}}, tmp_path, last_name="Example", pdf=False)
    t = texts(r["resume"])
    assert t[2] == "DATA ENGINEER · ANALYTICS" and t[3] == "Only this."
    assert "Automated releases." in t and "BI: Power BI" in t


def test_unknown_or_ambiguous_section_is_an_error_naming_the_choices(base, tmp_path):
    with pytest.raises(docs.SpecError, match="Data Platform"):
        docs.build(base, {"company": "A", "resume": {"experience": {"Nope": ["x"]}}}, tmp_path, last_name="E", pdf=False)
    with pytest.raises(docs.SpecError, match="more than one"):
        docs.build(base, {"company": "A", "resume": {"experience": {"Data": ["x"]}}}, tmp_path, last_name="E", pdf=False)
    with pytest.raises(docs.SpecError, match="company"):
        docs.build(base, {"resume": {}}, tmp_path, last_name="E", pdf=False)


def test_cover_letter_keeps_header_and_writes_letter(base, tmp_path):
    spec = {"company": "Acme", "cover_letter": {"to": "Hiring Team, Data", "greeting": "Dear Hiring Team,",
                                                "paragraphs": ["First.", "Second."]}}
    r = docs.build(base, spec, tmp_path, last_name="Example", pdf=False, today=dt.date(2026, 11, 6))
    assert r["cover_letter"].endswith("Example Cover Letter - Acme.docx")
    assert texts(r["cover_letter"]) == ["Pat Example", "Boston, MA | linkedin.com/in/pat", "November 6, 2026",
                                        "Hiring Team, Data\nAcme", "Dear Hiring Team,", "First.", "Second.",
                                        "Sincerely,\nPat Example"]
    assert "resume" in r  # the resume is always built, as the base when the spec leaves it out


def test_resume_over_two_pages_is_reported(base, tmp_path, monkeypatch):
    monkeypatch.setattr(docs, "to_pdf", lambda path: path.with_suffix(".pdf"))
    monkeypatch.setattr(docs, "pdf_pages", lambda path: 3 if "Resume" in path.name else 1)
    r = docs.build(base, {"company": "Acme", "cover_letter": {"paragraphs": ["Hi."]}}, tmp_path, last_name="E")
    assert r["pages"] == {"resume": 3, "cover_letter": 1}
    assert any("Resume is 3 pages" in p and "Data Platform" in p for p in r["problems"])


def test_missing_libreoffice_is_reported_not_raised(base, tmp_path, monkeypatch):
    monkeypatch.setattr(docs, "to_pdf", lambda path: None)
    r = docs.build(base, {"company": "Acme"}, tmp_path, last_name="E")
    assert any("PDF" in p for p in r["problems"])
