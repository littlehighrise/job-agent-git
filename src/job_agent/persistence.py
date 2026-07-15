from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from job_agent.models import Classification, JobPosting, MatchAnalysis

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  source_job_id TEXT NOT NULL,
  employer TEXT NOT NULL,
  job_title TEXT,
  canonical_job_title TEXT,
  location TEXT,
  application_url TEXT NOT NULL,
  date_discovered TEXT NOT NULL,
  last_seen TEXT,
  match_score INTEGER,
  evidence_confidence INTEGER,
  risk_score INTEGER,
  classification TEXT,
  analysis_json TEXT,
  job_json TEXT,
  artifact_dir TEXT,
  resume_version TEXT,
  answers_generated TEXT,
  user_decision TEXT,
  decision_timestamp TEXT,
  date_applied TEXT,
  submission_status TEXT,
  rejection_status TEXT,
  notes TEXT,
  UNIQUE(source, source_job_id)
);
"""

COLUMNS: dict[str, str] = {
    "job_title": "TEXT", "location": "TEXT", "last_seen": "TEXT", "analysis_json": "TEXT",
    "job_json": "TEXT", "artifact_dir": "TEXT", "decision_timestamp": "TEXT",
}


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


class ApplicationStore:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        existing = {row[1] for row in self.conn.execute("PRAGMA table_info(applications)")}
        for column, definition in COLUMNS.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE applications ADD COLUMN {column} {definition}")

    def find_existing(self, job: JobPosting) -> sqlite3.Row | None:
        row = self.conn.execute("SELECT * FROM applications WHERE source=? AND source_job_id=?", (job.source, job.source_job_id)).fetchone()
        if row:
            return row
        row = self.conn.execute("SELECT * FROM applications WHERE application_url=?", (job.application_url,)).fetchone()
        if row:
            return row
        return self.conn.execute(
            "SELECT * FROM applications WHERE lower(employer)=? AND lower(coalesce(job_title, canonical_job_title))=? AND lower(coalesce(location,''))=?",
            (_norm(job.employer), _norm(job.job_title), _norm(job.location)),
        ).fetchone()

    def has_seen(self, job: JobPosting) -> bool:
        return self.find_existing(job) is not None

    def upsert(self, job: JobPosting, analysis: MatchAnalysis, artifact_dir: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO applications(source, source_job_id, employer, job_title, canonical_job_title, location, application_url, date_discovered, last_seen, match_score, evidence_confidence, risk_score, classification, analysis_json, job_json, artifact_dir, submission_status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source, source_job_id) DO UPDATE SET
              last_seen=excluded.last_seen, match_score=excluded.match_score, evidence_confidence=excluded.evidence_confidence,
              risk_score=excluded.risk_score, classification=excluded.classification, analysis_json=excluded.analysis_json,
              job_json=excluded.job_json, artifact_dir=coalesce(excluded.artifact_dir, applications.artifact_dir), job_title=excluded.job_title,
              canonical_job_title=excluded.canonical_job_title, location=excluded.location, application_url=excluded.application_url""",
            (job.source, job.source_job_id, job.employer, job.job_title, job.canonical_job_title or job.job_title, job.location, job.application_url, job.date_discovered.isoformat(), now, analysis.role_match_score, analysis.evidence_confidence_score, analysis.application_risk_score, analysis.classification.value, analysis.model_dump_json(), job.model_dump_json(), artifact_dir, "not_submitted"),
        )
        self.conn.commit()

    def queue(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM applications WHERE classification IN (?,?) AND coalesce(user_decision,'')='' ORDER BY date_discovered DESC", (Classification.REVIEW_REQUIRED.value, Classification.AUTO_APPLY_ELIGIBLE.value)).fetchall()
        return [dict(r) for r in rows]

    def get(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM applications WHERE source_job_id=? OR id=?", (job_id, job_id if job_id.isdigit() else -1)).fetchone()
        return dict(row) if row else None

    def record_decision(self, job_id: str, decision: str, notes: str | None = None) -> bool:
        ts = datetime.now(timezone.utc).isoformat()
        cur = self.conn.execute("UPDATE applications SET user_decision=?, decision_timestamp=?, notes=coalesce(?, notes) WHERE source_job_id=? OR id=?", (decision, ts, notes, job_id, job_id if job_id.isdigit() else -1))
        self.conn.commit()
        return cur.rowcount > 0
