"""SQLite store for jobs and their pipeline state."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
create table if not exists jobs (
  id text primary key,            -- '<ats>:<slug>:<job id>'
  ats text, company text, title text, location text, url text, apply_url text,
  description text,
  found_at text default current_timestamp,
  score integer, score_reason text,
  status text default 'new',       -- new, skipped, filled, submitted, failed
  plan_json text,                  -- answers + file paths; editable on the review page
  report_json text,                -- last fill/submit report
  screenshot text, error text,
  submitted_at text,
  pay text,                        -- posted range, free text
  outcome text,                    -- confirmed, interview, rejected, offer (latest email signal)
  outcome_at text
);
create table if not exists events (
  id text primary key,             -- email Message-ID, so a resync never double-counts
  job_id text, kind text,          -- confirmed, interview, rejected, offer
  at text, sender text, subject text,
  proof text                       -- saved .eml / screenshot path, for DUA records
);
"""

RUNS_SCHEMA = """
create table if not exists runs (
  kind text primary key,           -- find, sync-email, fill, daily
  at text, ok integer, summary text
);
"""

# Columns added after v0.1; ALTERed into older databases on open.
MIGRATIONS = {"pay": "text", "outcome": "text", "outcome_at": "text"}

STATUSES = ["new", "skipped", "filled", "submitted", "failed"]


class DB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.executescript(RUNS_SCHEMA)
        have = {r[1] for r in self.conn.execute("pragma table_info(jobs)")}
        for col, typ in MIGRATIONS.items():
            if col not in have:
                self.conn.execute(f"alter table jobs add column {col} {typ}")
        ecols = {r[1] for r in self.conn.execute("pragma table_info(events)")}
        if "proof" not in ecols:
            self.conn.execute("alter table events add column proof text")
        # guess: what the keyword rules said; reviewed: a model or you settled the kind; manual: you did.
        for col, typ in {"body": "text", "guess": "text", "reviewed": "integer default 0", "manual": "integer default 0"}.items():
            if col not in ecols:
                self.conn.execute(f"alter table events add column {col} {typ}")
        self.conn.execute("update events set reviewed = 1 where kind = 'applied'")
        self.conn.commit()

    def upsert_job(self, job: dict) -> bool:
        """Insert if unseen. Returns True when the job is new."""
        cur = self.conn.execute(
            "insert or ignore into jobs (id, ats, company, title, location, url, apply_url, description)"
            " values (:id, :ats, :company, :title, :location, :url, :apply_url, :description)",
            job,
        )
        self.conn.commit()
        return cur.rowcount == 1

    def update(self, job_id: str, **fields):
        for k in ("plan_json", "report_json"):
            if k in fields and not isinstance(fields[k], str):
                fields[k] = json.dumps(fields[k], indent=1)
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        self.conn.execute(f"update jobs set {sets} where id = :id", {**fields, "id": job_id})
        self.conn.commit()

    def get(self, job_id: str) -> dict | None:
        row = self.conn.execute("select * from jobs where id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def by_status(self, *statuses: str) -> list[dict]:
        q = f"select * from jobs where status in ({','.join('?' * len(statuses))}) order by score desc, found_at desc"
        return [dict(r) for r in self.conn.execute(q, statuses)]

    def all(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("select * from jobs order by found_at desc")]

    def add_event(self, ev: dict) -> bool:
        ev = {"proof": None, "body": None, "guess": None, "reviewed": 1, "manual": 0, **ev}
        cur = self.conn.execute(
            "insert or ignore into events (id, job_id, kind, at, sender, subject, proof, body, guess, reviewed, manual)"
            " values (:id, :job_id, :kind, :at, :sender, :subject, :proof, :body, :guess, :reviewed, :manual)", ev)
        self.conn.commit()
        return cur.rowcount == 1

    def events(self, all: bool = False) -> list[dict]:
        """Settled events, newest first. Unreviewed and ignored emails only come back with all=True."""
        q = "select * from events" + ("" if all else " where reviewed = 1 and kind != 'ignored'") + " order by at desc"
        return [dict(r) for r in self.conn.execute(q)]

    def pending_events(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("select * from events where reviewed = 0 order by at")]

    def get_event(self, event_id: str) -> dict | None:
        row = self.conn.execute("select * from events where id = ?", (event_id,)).fetchone()
        return dict(row) if row else None

    def set_event(self, event_id: str, **fields):
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        self.conn.execute(f"update events set {sets} where id = :id", {**fields, "id": event_id})
        self.conn.commit()

    def has_event(self, event_id: str) -> bool:
        return self.conn.execute("select 1 from events where id = ?", (event_id,)).fetchone() is not None

    def event_kind(self, event_id: str) -> str | None:
        row = self.conn.execute("select kind from events where id = ?", (event_id,)).fetchone()
        return row[0] if row else None

    def delete_event(self, event_id: str):
        self.conn.execute("delete from events where id = ?", (event_id,))
        self.conn.commit()

    def log_run(self, kind: str, ok: bool, summary: str = ""):
        """Keeps the latest run of each kind, for the dashboard's freshness strip."""
        self.conn.execute("insert or replace into runs (kind, at, ok, summary) values (?, datetime('now', 'localtime'), ?, ?)",
                          (kind, int(ok), summary[:300]))
        self.conn.commit()

    def runs(self) -> dict[str, dict]:
        return {r["kind"]: dict(r) for r in self.conn.execute("select * from runs")}
