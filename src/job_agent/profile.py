from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

from job_agent.io import load_model
from job_agent.models import AuditFinding, CandidateProfile

_PLACEHOLDER_PATTERNS = ("example.com", "placeholder", "your-", "test@", "@test.", "bob@example.com")


def load_candidate_profile(path: Path) -> CandidateProfile:
    profile = load_model(path, CandidateProfile)
    email = os.getenv("JOB_AGENT_EMAIL")
    phone = os.getenv("JOB_AGENT_PHONE")
    if email:
        profile.email = email
    if phone:
        profile.phone = phone
    return profile


def _is_placeholder(value: str | None) -> bool:
    return bool(value) and any(p in value.lower() for p in _PLACEHOLDER_PATTERNS)


def _valid_url(value: str | None) -> bool:
    if not value or _is_placeholder(value):
        return False
    parsed = urlparse(value if "://" in value else f"https://{value}")
    return parsed.scheme in {"http", "https"} and "." in parsed.netloc and " " not in parsed.netloc


def validate_candidate_profile(profile: CandidateProfile, *, submission_required: bool = True) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    if not profile.email:
        findings.append(AuditFinding(severity="blocker" if submission_required else "warning", category="missing_contact", message="Missing private email; resume is draft-only."))
    elif _is_placeholder(profile.email) or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", profile.email):
        findings.append(AuditFinding(severity="blocker", category="placeholder_contact", message="Email is placeholder, test, or invalid.", referenced_text=profile.email))
    if not profile.phone:
        findings.append(AuditFinding(severity="blocker" if submission_required else "warning", category="missing_contact", message="Missing private phone; resume is draft-only."))
    if profile.portfolio_url and not _valid_url(profile.portfolio_url):
        findings.append(AuditFinding(severity="blocker", category="invalid_profile_url", message="Portfolio URL is placeholder or invalid.", referenced_text=profile.portfolio_url))
    if profile.linkedin_url and not _valid_url(profile.linkedin_url):
        findings.append(AuditFinding(severity="blocker", category="invalid_profile_url", message="LinkedIn URL is placeholder or invalid.", referenced_text=profile.linkedin_url))
    return findings


def contact_complete(profile: CandidateProfile) -> bool:
    return not any(f.severity == "blocker" for f in validate_candidate_profile(profile, submission_required=True))
