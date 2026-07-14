from __future__ import annotations
from abc import ABC, abstractmethod
from job_agent.models import JobPosting

class JobSourceAdapter(ABC):
    @abstractmethod
    def fetch_jobs(self) -> list[JobPosting]:
        """Return normalized job postings from this source."""
