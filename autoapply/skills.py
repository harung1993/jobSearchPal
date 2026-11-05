"""Skills to learn: what the roles you matched keep asking for that your resume doesn't show.

No model and no network. Each matched job's description is checked against a list of skills (with the
usual spellings of each), and so is everything you've written about yourself: the base resume,
CLAUDE.local.md, materials/context/*.md and the skills you mark "I have this". What the jobs ask for
and you don't show, counted across jobs, is the list. The tailoring notes' fit/gap tables
(data/applications/*/notes.md, "| Requirement | Gap |" rows) add the requirements the agent judged
missing on roles you actually applied to.

Your marks (have / learning / hide) live in data/skills.json.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

# (name, pattern, area, where to learn). Patterns are matched case-insensitively on word edges.
# Links are each project's own docs or free course pages; nothing here is paid or affiliate.
SKILLS: list[tuple[str, str, str, str]] = [
    # Languages
    ("Python", r"python", "Languages", "https://docs.python.org/3/tutorial/"),
    ("SQL", r"sql(?! server)", "Languages", ""),
    ("R", r"\bR\b(?=\s*(?:,|/|programming|language))|rstudio", "Languages", "https://www.r-project.org/"),
    ("Java", r"java(?!script)", "Languages", "https://dev.java/learn/"),
    ("Scala", r"scala\b", "Languages", "https://docs.scala-lang.org/"),
    ("Go", r"golang|\bgo\b(?=\s*(?:/|programming|language))", "Languages", "https://go.dev/tour/"),
    ("JavaScript", r"javascript|node\.?js", "Languages", "https://developer.mozilla.org/en-US/docs/Learn"),
    ("TypeScript", r"typescript", "Languages", "https://www.typescriptlang.org/docs/"),
    ("C#", r"c#|\.net\b", "Languages", "https://learn.microsoft.com/dotnet/csharp/"),
    ("C++", r"c\+\+", "Languages", ""),
    ("Rust", r"\brust\b", "Languages", "https://doc.rust-lang.org/book/"),
    ("Bash / shell", r"bash|shell scripting", "Languages", ""),
    # Data engineering
    ("Airflow", r"airflow", "Data engineering", "https://airflow.apache.org/docs/"),
    ("Dagster", r"dagster", "Data engineering", "https://docs.dagster.io/"),
    ("Prefect", r"prefect\b", "Data engineering", "https://docs.prefect.io/"),
    ("dbt", r"\bdbt\b", "Data engineering", "https://courses.getdbt.com/"),
    ("Spark", r"spark|pyspark", "Data engineering", "https://spark.apache.org/docs/latest/"),
    ("Databricks", r"databricks", "Data engineering", "https://www.databricks.com/learn/training"),
    ("Kafka / streaming", r"kafka|kinesis|pub/?sub|stream processing|streaming data|flink", "Data engineering", "https://kafka.apache.org/documentation/"),
    ("CDC", r"change data capture|\bcdc\b|debezium", "Data engineering", "https://debezium.io/documentation/"),
    ("Fivetran / Airbyte", r"fivetran|airbyte|stitch data", "Data engineering", "https://docs.airbyte.com/"),
    ("Iceberg / Delta / lakehouse", r"iceberg|delta lake|lakehouse|hudi", "Data engineering", "https://iceberg.apache.org/docs/latest/"),
    ("Data modeling", r"data model(?:ing|ling)|dimensional model|star schema|kimball|data vault", "Data engineering", ""),
    ("Data quality", r"data quality|great expectations|monte carlo|data observability|soda\b", "Data engineering", ""),
    ("ETL / ELT", r"\betl\b|\belt\b|data pipelines?", "Data engineering", ""),
    # Warehouses and databases
    ("Snowflake", r"snowflake", "Warehouses and databases", "https://learn.snowflake.com/"),
    ("BigQuery", r"bigquery", "Warehouses and databases", "https://cloud.google.com/bigquery/docs"),
    ("Redshift", r"redshift", "Warehouses and databases", "https://docs.aws.amazon.com/redshift/"),
    ("PostgreSQL", r"postgres(?:ql)?", "Warehouses and databases", "https://www.postgresql.org/docs/"),
    ("MySQL", r"mysql", "Warehouses and databases", "https://dev.mysql.com/doc/"),
    ("SQL Server", r"sql server|t-sql|tsql|ssis", "Warehouses and databases", "https://learn.microsoft.com/sql/"),
    ("Oracle", r"oracle", "Warehouses and databases", ""),
    ("MongoDB / NoSQL", r"mongodb|nosql|dynamodb|cassandra", "Warehouses and databases", "https://learn.mongodb.com/"),
    ("Microsoft Fabric / Synapse", r"microsoft fabric|\bfabric\b(?= ?(?:,|/|and|or|lakehouse|warehouse))|synapse", "Warehouses and databases", "https://learn.microsoft.com/fabric/"),
    # BI and analytics
    ("Tableau", r"tableau", "BI and analytics", "https://www.tableau.com/learn/training"),
    ("Power BI", r"power ?bi|\bdax\b", "BI and analytics", "https://learn.microsoft.com/power-bi/"),
    ("Looker / LookML", r"looker|lookml", "BI and analytics", "https://cloud.google.com/looker/docs"),
    ("Semantic layer / metrics", r"semantic layer|metrics layer|cube\.dev|metricflow", "BI and analytics", ""),
    ("Excel", r"excel\b|spreadsheets?", "BI and analytics", ""),
    ("A/B testing / experimentation", r"a/b test|experimentation|experiment design|causal inference", "BI and analytics", ""),
    ("Statistics", r"statistic(?:s|al)|regression|hypothesis test", "BI and analytics", ""),
    ("Product analytics", r"amplitude|mixpanel|product analytics|segment\.com|heap analytics", "BI and analytics", ""),
    # ML and AI
    ("Machine learning", r"machine learning|\bml\b|scikit|sklearn|xgboost", "ML and AI", "https://scikit-learn.org/stable/getting_started.html"),
    ("Deep learning", r"deep learning|pytorch|tensorflow|keras", "ML and AI", "https://pytorch.org/tutorials/"),
    ("LLMs / generative AI", r"\bllms?\b|large language model|generative ai|genai|gpt|prompt engineering", "ML and AI", ""),
    ("RAG / vector search", r"\brag\b|retrieval.augmented|vector (?:db|database|search|store)|embeddings?|pinecone|pgvector", "ML and AI", ""),
    ("AI agents", r"ai agents?|agentic|langchain|langgraph|\bmcp\b|multi.agent", "ML and AI", ""),
    ("MLOps", r"mlops|mlflow|model deployment|feature store|sagemaker|vertex ai", "ML and AI", "https://mlflow.org/docs/latest/"),
    ("NLP", r"\bnlp\b|natural language processing", "ML and AI", ""),
    ("Computer vision", r"computer vision|image recognition|opencv", "ML and AI", ""),
    # Cloud and infrastructure
    ("AWS", r"\baws\b|amazon web services|\bs3\b|lambda\b|glue\b", "Cloud and infrastructure", "https://aws.amazon.com/training/"),
    ("Azure", r"azure", "Cloud and infrastructure", "https://learn.microsoft.com/training/azure/"),
    ("Google Cloud", r"\bgcp\b|google cloud", "Cloud and infrastructure", "https://cloud.google.com/learn/training"),
    ("Docker", r"docker|containers?\b", "Cloud and infrastructure", "https://docs.docker.com/get-started/"),
    ("Kubernetes", r"kubernetes|\bk8s\b|helm\b|\beks\b|\bgke\b|\baks\b", "Cloud and infrastructure", "https://kubernetes.io/docs/tutorials/"),
    ("Terraform / IaC", r"terraform|infrastructure as code|\biac\b|cloudformation|pulumi", "Cloud and infrastructure", "https://developer.hashicorp.com/terraform/tutorials"),
    ("CI/CD", r"ci/cd|ci ?cd|continuous integration|github actions|jenkins|gitlab ci", "Cloud and infrastructure", ""),
    ("Git", r"\bgit\b|github|gitlab|version control", "Cloud and infrastructure", "https://git-scm.com/book/en/v2"),
    ("Linux", r"linux|unix", "Cloud and infrastructure", ""),
    ("Observability", r"observability|datadog|prometheus|grafana|splunk|new relic", "Cloud and infrastructure", ""),
    ("Security / compliance", r"soc ?2|hipaa|gdpr|pci|iam\b|security best practices|data governance|ferpa", "Cloud and infrastructure", ""),
    # Software engineering
    ("REST APIs", r"rest(?:ful)? apis?|\bapis?\b(?= ?(?:,|/|and|or|design|development|integration))|graphql", "Software engineering", ""),
    ("Microservices", r"microservices?|distributed systems", "Software engineering", ""),
    ("React", r"react(?:\.js)?\b", "Software engineering", "https://react.dev/learn"),
    ("Testing", r"unit test|test automation|pytest|integration tests?|tdd\b", "Software engineering", ""),
    ("System design", r"system design|architecture design|scalab(?:le|ility)", "Software engineering", ""),
    # Business tools and domains
    ("Salesforce", r"salesforce", "Business tools and domains", "https://trailhead.salesforce.com/"),
    ("HubSpot", r"hubspot", "Business tools and domains", "https://academy.hubspot.com/"),
    ("Jira / Agile", r"\bjira\b|agile|scrum|kanban|sprint", "Business tools and domains", ""),
    ("SAP / ERP", r"\bsap\b|\berp\b|netsuite|workday financials", "Business tools and domains", ""),
    ("Google Analytics / SEO", r"google analytics|\bga4\b|\bseo\b|\bsem\b", "Business tools and domains", "https://skillshop.withgoogle.com/"),
    ("Figma / design", r"figma|sketch\b|user research|ux design|wireframes?", "Business tools and domains", "https://help.figma.com/"),
    ("Finance / accounting", r"financial (?:reporting|modeling|planning)|fp&a|gaap|revenue recognition|reconciliation|accounting", "Business tools and domains", ""),
    ("Healthcare data", r"healthcare|claims data|\bphi\b|\behr\b|\bemr\b|hl7|fhir|epic\b", "Business tools and domains", ""),
    ("Marketing analytics", r"marketing analytics|attribution|marketing mix|customer acquisition|ltv\b", "Business tools and domains", ""),
    # Ways of working
    ("People management", r"people manag|manag(?:e|ing) (?:a )?team|direct reports|hiring and (?:developing|growing)|mentor(?:ing|ship)?|coach(?:ing)?\b", "Ways of working", ""),
    ("Stakeholder management", r"stakeholder|cross.functional|business partners", "Ways of working", ""),
    ("Project management", r"project manag|program manag|roadmap|pmp\b", "Ways of working", ""),
    ("Communication / presenting", r"communicat(?:e|ion) (?:complex|technical|findings|insights)|presentation skills|data storytelling|executive presentations?", "Ways of working", ""),
    ("Consulting / client-facing", r"client.facing|consulting|pre.sales|customer.facing", "Ways of working", ""),
]
_COMPILED = [(n, re.compile(rf"(?<![a-z0-9]){p}", re.I), area, url) for n, p, area, url in SKILLS]
AREAS = list(dict.fromkeys(a for _, _, a, _ in SKILLS))


def found_in(text: str) -> set[str]:
    """Skill names mentioned in a piece of text."""
    t = text or ""
    return {n for n, rx, _, _ in _COMPILED if rx.search(t)}


def _marks_path(data_dir: Path) -> Path:
    return Path(data_dir) / "skills.json"


def marks(data_dir: Path) -> dict:
    """{"have": [...], "learning": [...], "hidden": [...]} from data/skills.json."""
    try:
        m = json.loads(_marks_path(data_dir).read_text())
    except (OSError, ValueError):
        m = {}
    return {k: [s for s in m.get(k, []) if isinstance(s, str)] for k in ("have", "learning", "hidden")}


def set_mark(data_dir: Path, skill: str, mark: str):
    """mark: have, learning, hidden, or clear."""
    names = {n for n, *_ in SKILLS}
    if skill not in names:
        return
    m = marks(data_dir)
    for k in m:
        m[k] = [s for s in m[k] if s != skill]
    if mark in m:
        m[mark].append(skill)
    p = _marks_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, indent=1))


def about_you(root: Path) -> str:
    """Everything you've written about yourself: the base resume, CLAUDE.local.md and materials/context."""
    from .setup import base_resume
    texts = []
    r = base_resume(root)
    if r:
        try:
            from docx import Document
            d = Document(str(r))
            texts.append("\n".join(p.text for p in d.paragraphs))
            texts += [c.text for t in d.tables for row in t.rows for c in row.cells]
        except Exception:
            pass
    for p in [root / "CLAUDE.local.md", *sorted((root / "materials" / "context").glob("*.md"))]:
        try:
            texts.append(p.read_text())
        except OSError:
            pass
    return "\n".join(texts)


GAP_ROW = re.compile(r"^\|\s*([^|]{3,160}?)\s*\|\s*\**(gap|partial|missing|no)\**\b", re.I | re.M)


def notes_gaps(data_dir: Path) -> list[tuple[str, str]]:
    """(application folder, requirement) for each Gap/Partial row in the tailoring notes' fit/gap tables."""
    out = []
    for f in sorted((Path(data_dir) / "applications").glob("*/notes.md")):
        try:
            text = f.read_text()
        except OSError:
            continue
        out += [(f.parent.name, m.group(1).strip("* ")) for m in GAP_ROW.finditer(text)]
    return out


def gaps(db, settings, days: int = 90, now: datetime | None = None) -> dict:
    """What the matched roles ask for, split into what you show and what you don't."""
    now = now or datetime.now()
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.conn.execute(
        "select id, company, title, description, status from jobs where found_at >= ? and coalesce(description, '') != ''"
        " and not (status = 'skipped' and coalesce(error, '') like 'Declined%')", (since,)).fetchall()
    jobs = [dict(zip(("id", "company", "title", "description", "status"), r)) for r in rows]
    m = marks(settings.data_dir)
    have_text = found_in(about_you(settings.root)) | set(m["have"])
    tally: dict[str, dict] = {}
    for j in jobs:
        for name in found_in(j["description"]):
            t = tally.setdefault(name, {"count": 0, "examples": [], "applied": 0})
            t["count"] += 1
            if j["status"] in ("filled", "submitted"):
                t["applied"] += 1
            if len(t["examples"]) < 4:
                t["examples"].append({"id": j["id"], "label": f"{j['company']} · {j['title']}"})
    # Tailoring notes: requirements the agent judged missing on roles you applied to.
    from_notes: dict[str, set] = {}
    other_notes: dict[str, set] = {}
    for app, req in notes_gaps(settings.data_dir):
        named = found_in(req)
        for n in named:
            from_notes.setdefault(n, set()).add(app)
        if not named:
            other_notes.setdefault(re.sub(r"\s+", " ", req.lower())[:90], set()).add(app)
    meta = {n: (area, url) for n, _, area, url in SKILLS}
    total = len(jobs) or 1
    items = []
    for name in set(tally) | set(from_notes):
        t = tally.get(name, {"count": 0, "examples": [], "applied": 0})
        area, url = meta[name]
        state = ("learning" if name in m["learning"] else "hidden" if name in m["hidden"]
                 else "have" if name in have_text else "gap")
        items.append({"name": name, "area": area, "url": url, "count": t["count"], "pct": round(100 * t["count"] / total),
                      "applied": t["applied"], "examples": t["examples"], "notes": len(from_notes.get(name, ())),
                      "state": state, "you_mark_have": name in m["have"]})
    items.sort(key=lambda i: (-(i["count"] + 2 * i["notes"]), -i["count"], i["name"]))  # a gap on a role you applied to counts double
    return {
        "jobs": len(jobs), "days": days,
        "gaps": [i for i in items if i["state"] == "gap"],
        "learning": [i for i in items if i["state"] == "learning"],
        "have": [i for i in items if i["state"] == "have"],
        "hidden": [i for i in items if i["state"] == "hidden"],
        "other_notes": sorted(((k, len(v)) for k, v in other_notes.items()), key=lambda kv: -kv[1])[:12],
    }
