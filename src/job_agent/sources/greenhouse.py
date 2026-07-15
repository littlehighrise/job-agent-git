from __future__ import annotations

import json
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from job_agent.models import ATS, JobPosting, JobRequirement, RemoteStatus
from job_agent.sources.base import JobSourceAdapter

GREENHOUSE_SOURCE = "greenhouse"


class GreenhouseSourceError(RuntimeError):
    pass


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"p", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "div"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", " ".join(self.parts))).strip()


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    parser = _HTMLTextExtractor()
    parser.feed(value)
    return parser.text()


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" -•\t")


def _parse_date(value: str | None):
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(value.replace("Z", "+0000"), fmt).date()
        except ValueError:
            pass
    return None


SECTION_ALIASES = {
    "responsibilities": "responsibilities",
    "what you'll do": "responsibilities",
    "what you will do": "responsibilities",
    "what you’ll do": "responsibilities",
    "requirements": "requirements",
    "qualifications": "requirements",
    "minimum qualifications": "requirements",
    "what we're looking for": "requirements",
    "what we’re looking for": "requirements",
    "about you": "requirements",
    "preferred qualifications": "preferred",
    "nice to have": "preferred",
    "nice-to-have": "preferred",
    "bonus": "preferred",
    "bonus points": "preferred",
}
PREFERRED_HEADINGS = {"preferred", "nice to have", "nice-to-have", "bonus", "bonus points"}
TECHS = ["Figma", "Sketch", "React", "Angular", "Vue", "TypeScript", "JavaScript", "Python", "Django", "Rails", "Ruby", "SQL", "AWS", "GCP", "Azure", "Kubernetes", "Docker", "Java", "Go", "Swift", "Kotlin"]
HARD_PATTERNS = re.compile(r"\b(required|must have|minimum qualification|minimum qualifications|legally required|active .{0,30}clearance required|clearance required)\b", re.I)
YEAR_RE = re.compile(r"(\d+)\+?\s*(?:\+\s*)?years?", re.I)


def parse_greenhouse_description(description: str) -> dict[str, Any]:
    lines = [_clean(line) for line in description.splitlines()]
    sections: dict[str, list[str]] = {"responsibilities": [], "requirements": [], "preferred": []}
    current: str | None = None
    for raw in lines:
        if not raw:
            continue
        key = raw.lower().rstrip(":")
        if key in SECTION_ALIASES:
            current = SECTION_ALIASES[key]
            continue
        bullet = _clean(re.sub(r"^[•*\-–]+", "", raw))
        if not bullet or current is None:
            continue
        sections[current].append(bullet)

    explicit: list[JobRequirement] = []
    inferred: list[JobRequirement] = []
    required_tech: set[str] = set()
    preferred_tech: set[str] = set()
    years: int | None = None
    education: list[str] = []
    clearance: list[str] = []
    travel: str | None = None

    def add_req(text: str, preferred: bool = False) -> None:
        nonlocal years, travel
        lower = text.lower()
        hard = (not preferred) and bool(HARD_PATTERNS.search(text))
        category = "general"
        if "clearance" in lower:
            category = "clearance"; hard = hard or "required" in lower; clearance.append(text)
        elif "degree" in lower or "bachelor" in lower or "master" in lower:
            category = "education"; education.append(text)
        elif "travel" in lower and any(w in lower for w in ["required", "%", "up to"]):
            category = "general"; travel = text
        m = YEAR_RE.search(text)
        if m:
            years = max(years or 0, int(m.group(1))); category = "experience_years"
        found = [t for t in TECHS if re.search(rf"\b{re.escape(t)}\b", text, re.I)]
        if found:
            category = "preferred_technology" if preferred else "required_technology"
            (preferred_tech if preferred else required_tech).update(found)
        req = JobRequirement(text=text, requirement_type="inferred" if preferred else "explicit", category=category, is_hard_requirement=hard)
        (inferred if preferred else explicit).append(req)

    for item in sections["requirements"]:
        add_req(item, preferred=False)
    for item in sections["preferred"]:
        add_req(item, preferred=True)

    return {
        "responsibilities": sections["responsibilities"],
        "explicit_requirements": explicit,
        "inferred_preferences": inferred,
        "required_technologies": sorted(required_tech),
        "preferred_technologies": sorted(preferred_tech),
        "required_years_experience": years,
        "education_requirements": education,
        "security_clearance_requirements": clearance,
        "travel_requirements": travel,
    }


def infer_remote_status(title: str, location: str | None, description: str) -> RemoteStatus:
    text = " ".join(v for v in [title, location, description[:1000]] if v).lower()
    if re.search(r"\bhybrid\b", text):
        return RemoteStatus.HYBRID
    if re.search(r"\b(remote|work from home|distributed)\b", text):
        return RemoteStatus.REMOTE
    if re.search(r"\b(on-site|onsite|in office|in-office)\b", text):
        return RemoteStatus.ONSITE
    return RemoteStatus.UNKNOWN


class GreenhouseSourceAdapter(JobSourceAdapter):
    def __init__(self, board_token: str, company: str | None = None, timeout_seconds: float = 10.0):
        self.board_token = board_token
        self.company = company or board_token
        self.timeout_seconds = timeout_seconds

    def _fetch_json(self, url: str) -> dict[str, Any]:
        try:
            with urlopen(Request(url, headers={"User-Agent": "job-agent/0.1"}), timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise GreenhouseSourceError(f"Greenhouse board '{self.board_token}' returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GreenhouseSourceError(f"Greenhouse board '{self.board_token}' could not be read: {exc.__class__.__name__}") from exc

    def fetch_jobs(self) -> list[JobPosting]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{self.board_token}/jobs?content=true"
        payload = self._fetch_json(url)
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise GreenhouseSourceError(f"Greenhouse board '{self.board_token}' response did not contain a jobs list")
        postings: list[JobPosting] = []
        for item in jobs:
            if not isinstance(item, dict) or item.get("id") is None or not item.get("title"):
                continue
            description = html_to_text(item.get("content") or item.get("description") or "")
            parsed = parse_greenhouse_description(description)
            location = item.get("location") if isinstance(item.get("location"), dict) else None
            location_name = location.get("name") if location else None
            absolute_url = item.get("absolute_url") or item.get("internal_job_id") or f"https://boards.greenhouse.io/{self.board_token}/jobs/{item.get('id')}"
            postings.append(JobPosting(
                source=GREENHOUSE_SOURCE,
                source_job_id=str(item["id"]),
                employer=self.company,
                job_title=str(item["title"]),
                location=location_name,
                remote_status=infer_remote_status(str(item["title"]), location_name, description),
                description=description,
                date_posted=_parse_date(item.get("updated_at") or item.get("first_published") or item.get("published_at")),
                application_url=str(absolute_url),
                ats_type=ATS.GREENHOUSE,
                **parsed,
            ))
        return postings
