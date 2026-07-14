from __future__ import annotations
import sqlite3
from pathlib import Path
from job_agent.models import JobPosting, MatchAnalysis

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  source_job_id TEXT NOT NULL,
  employer TEXT NOT NULL,
  canonical_job_title TEXT,
  application_url TEXT NOT NULL,
  date_discovered TEXT NOT NULL,
  match_score INTEGER,
  evidence_confidence INTEGER,
  risk_score INTEGER,
  classification TEXT,
  resume_version TEXT,
  answers_generated TEXT,
  user_decision TEXT,
  date_applied TEXT,
  submission_status TEXT,
  rejection_status TEXT,
  notes TEXT,
  UNIQUE(source, source_job_id)
);
"""

class ApplicationStore:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.execute(SCHEMA)
        self.conn.commit()

    def has_seen(self, job: JobPosting) -> bool:
        row = self.conn.execute("SELECT 1 FROM applications WHERE source=? AND source_job_id=?", (job.source, job.source_job_id)).fetchone()
        return row is not None

    def upsert(self, job: JobPosting, analysis: MatchAnalysis) -> None:
        self.conn.execute(
            """INSERT INTO applications(source, source_job_id, employer, canonical_job_title, application_url, date_discovered, match_score, evidence_confidence, risk_score, classification, submission_status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source, source_job_id) DO UPDATE SET match_score=excluded.match_score,evidence_confidence=excluded.evidence_confidence,risk_score=excluded.risk_score,classification=excluded.classification""",
            (job.source, job.source_job_id, job.employer, job.canonical_job_title or job.job_title, job.application_url, job.date_discovered.isoformat(), analysis.role_match_score, analysis.evidence_confidence_score, analysis.application_risk_score, analysis.classification.value, "not_submitted"),
        )
        self.conn.commit()
