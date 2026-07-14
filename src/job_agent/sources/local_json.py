from __future__ import annotations
from pathlib import Path
from job_agent.io import load_model_list
from job_agent.models import JobPosting
from job_agent.sources.base import JobSourceAdapter

class LocalJsonSourceAdapter(JobSourceAdapter):
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def fetch_jobs(self) -> list[JobPosting]:
        return load_model_list(self.path, JobPosting)
