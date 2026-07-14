from __future__ import annotations
from abc import ABC

class ApplicationAdapter(ABC):
    """Conservative adapter seam. MVP never submits applications automatically."""
    def get_questions(self):
        raise NotImplementedError
    def populate_application(self):
        raise NotImplementedError
    def validate_application(self):
        raise NotImplementedError
    def submit_application(self):
        raise RuntimeError("Automatic submission is intentionally disabled in the MVP")
