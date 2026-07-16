from __future__ import annotations

import html
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from job_agent.models import ATS, JobPosting, JobRequirement, ParsingQuality, RemoteStatus
from job_agent.sources.base import JobSourceAdapter

GREENHOUSE_SOURCE = "greenhouse"


class GreenhouseSourceError(RuntimeError):
    pass


class _StructuredHTMLExtractor(HTMLParser):
    BLOCK_TAGS = {"p", "div", "section", "br", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._buf: list[str] = []
        self._li_depth = 0
        self._heading_depth = 0
        self._strong_depth = 0
        self._line_has_only_bold = False

    def _flush(self) -> None:
        text = _normalize_text("".join(self._buf))
        self._buf = []
        if not text:
            return
        if self._line_has_only_bold and not text.endswith(":"):
            text += ":"
        self.lines.append(text)
        self._line_has_only_bold = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.BLOCK_TAGS:
            self._flush()
        if tag == "li":
            self._flush(); self._li_depth += 1; self._buf.append("• ")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_depth += 1; self._line_has_only_bold = True
        elif tag in {"strong", "b"}:
            if not "".join(self._buf).strip():
                self._line_has_only_bold = True
            self._strong_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "li":
            self._flush(); self._li_depth = max(0, self._li_depth - 1)
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._flush(); self._heading_depth = max(0, self._heading_depth - 1)
        elif tag in {"strong", "b"}:
            self._strong_depth = max(0, self._strong_depth - 1)
        elif tag in self.BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if data:
            self._buf.append(data)

    def text(self) -> str:
        self._flush()
        return "\n".join(line for line in self.lines if line).strip()


def _normalize_text(value: str) -> str:
    value = html.unescape(value).replace("\xa0", " ").replace("—", "-").replace("–", "-")
    value = value.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", value).strip(" \t")


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    if "<" not in value or ">" not in value:
        return "\n".join(_normalize_text(line) for line in value.splitlines())
    parser = _StructuredHTMLExtractor()
    parser.feed(value)
    return parser.text()


def _clean(value: str) -> str:
    return _normalize_text(value).strip(" -•\t")


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
    "responsibilities": "responsibilities", "your responsibilities": "responsibilities", "the opportunity": "responsibilities",
    "what you'll do": "responsibilities", "what you will do": "responsibilities", "what you'll be doing": "responsibilities",
    "requirements": "requirements", "qualifications": "requirements", "minimum qualifications": "requirements", "who you are": "requirements",
    "what you bring": "requirements", "what you'll bring": "requirements", "you have": "requirements", "what we're looking for": "requirements", "about you": "requirements",
    "preferred qualifications": "preferred", "preferred experience": "preferred", "nice to have": "preferred", "nice-to-have": "preferred", "bonus": "preferred", "bonus points": "preferred", "it would be great if": "preferred",
    "benefits": "ignore", "perks and benefits": "ignore", "compensation": "ignore", "equal opportunity": "ignore", "privacy": "ignore", "about us": "ignore", "about datadog": "ignore",
}
BOILERPLATE_RE = re.compile(r"\b(equal opportunity|privacy notice|we encourage you to apply|reasonable accommodation|benefits|medical dental|401\(k\)|compensation range)\b", re.I)
TECHS = ["Figma", "Sketch", "React", "Angular", "Vue", "TypeScript", "JavaScript", "Python", "Django", "Rails", "Ruby", "SQL", "AWS", "GCP", "Azure", "Kubernetes", "Docker", "Java", "Go", "Swift", "Kotlin"]
HARD_PATTERNS = re.compile(r"\b(required|must have|minimum qualification|minimum qualifications|legally required|active .{0,30}clearance required|clearance required)\b", re.I)
YEAR_PATTERNS = [re.compile(r"(\d+)\+\s*years?", re.I), re.compile(r"at least\s+(\d+)\s+years?", re.I), re.compile(r"(\d+)\s+or more years?", re.I), re.compile(r"(\d+)\s*-\s*\d+\s+years?", re.I), re.compile(r"(\d+)\s+years?", re.I)]
SALARY_RE = re.compile(r"\$\s*([\d,]+)\s*(?:-|to)\s*\$?\s*([\d,]+)\s*(USD|CAD|EUR|GBP)?", re.I)
COUNTRIES = {"israel": "Israel", "united states": "United States", "usa": "United States", "us": "United States", "canada": "Canada", "united kingdom": "United Kingdom", "uk": "United Kingdom", "germany": "Germany", "france": "France", "india": "India"}

def _heading_key(line: str) -> str:
    return _clean(line).lower().rstrip(":")

def _extract_years(text: str) -> int | None:
    for pat in YEAR_PATTERNS:
        m = pat.search(text)
        if m:
            return int(m.group(1))
    return None

def _salary(text: str) -> tuple[int|None,int|None,str|None]:
    m = SALARY_RE.search(text)
    if not m: return None, None, None
    return int(m.group(1).replace(',', '')), int(m.group(2).replace(',', '')), (m.group(3) or 'USD').upper()

def infer_country(location: str | None) -> str | None:
    if not location: return None
    parts = [p.strip().lower() for p in re.split(r"[,/|-]", location) if p.strip()]
    for p in reversed(parts):
        if p in COUNTRIES: return COUNTRIES[p]
    return None


def parse_greenhouse_description(description: str) -> dict[str, Any]:
    structured = html_to_text(description)
    lines = [_clean(line) for line in structured.splitlines()]
    sections: dict[str, list[str]] = {"responsibilities": [], "requirements": [], "preferred": []}
    current: str | None = None; recognized = 0
    for raw in lines:
        if not raw: continue
        alias = SECTION_ALIASES.get(_heading_key(raw))
        if alias:
            current = None if alias == "ignore" else alias; recognized += 1; continue
        bullet = _clean(re.sub(r"^[•*\-–]+", "", raw))
        if not bullet or current is None or BOILERPLATE_RE.search(bullet): continue
        sections[current].append(bullet)

    explicit: list[JobRequirement] = []; inferred: list[JobRequirement] = []
    required_tech: set[str] = set(); preferred_tech: set[str] = set(); domains: set[str] = set()
    years: int | None = None; education: list[str] = []; clearance: list[str] = []; travel: str | None = None; management: str | None = None

    def add_req(text: str, preferred: bool = False) -> None:
        nonlocal years, travel, management
        lower = text.lower(); hard = (not preferred) and bool(HARD_PATTERNS.search(text)); category = "general"
        if "clearance" in lower: category = "clearance"; hard = hard or "required" in lower; clearance.append(text)
        elif any(x in lower for x in ["degree", "bachelor", "master"]): category = "education"; education.append(text)
        elif "travel" in lower and any(w in lower for w in ["required", "%", "up to"]): travel = text
        if any(x in lower for x in ["manage a team", "direct reports", "people manager"]): management = text; category = "management"
        yr = _extract_years(text)
        if yr is not None: years = max(years or 0, yr); category = "experience_years"
        for domain in ["developer platforms", "observability", "infrastructure", "technical domains", "saas", "enterprise"]:
            if domain in lower: domains.add(domain)
        found = [t for t in TECHS if re.search(rf"\b{re.escape(t)}\b", text, re.I)]
        if found: category = "preferred_technology" if preferred else "required_technology"; (preferred_tech if preferred else required_tech).update(found)
        (inferred if preferred else explicit).append(JobRequirement(text=text, requirement_type="inferred" if preferred else "explicit", category=category, is_hard_requirement=hard))

    for item in sections["requirements"]: add_req(item, False)
    for item in sections["preferred"]: add_req(item, True)
    sal_min, sal_max, currency = _salary(structured)
    quality = ParsingQuality.INSUFFICIENT
    if explicit and sections["responsibilities"] and recognized >= 2: quality = ParsingQuality.HIGH
    elif explicit and recognized: quality = ParsingQuality.MEDIUM
    elif explicit or sections["responsibilities"] or recognized: quality = ParsingQuality.LOW
    seniority = "staff" if re.search(r"\bstaff\b", structured, re.I) else None
    return {"responsibilities": sections["responsibilities"], "explicit_requirements": explicit, "inferred_preferences": inferred,
        "required_technologies": sorted(required_tech), "preferred_technologies": sorted(preferred_tech), "required_years_experience": years,
        "education_requirements": education, "security_clearance_requirements": clearance, "travel_requirements": travel,
        "salary_min": sal_min, "salary_max": sal_max, "currency": currency or "USD", "seniority": seniority,
        "management_expectations": management, "domain_experience_expectations": sorted(domains), "parsing_quality": quality}


def infer_remote_status(title: str, location: str | None, description: str) -> RemoteStatus:
    text = " ".join(v for v in [title, location, description[:1000]] if v).lower()
    if re.search(r"\bhybrid\b", text): return RemoteStatus.HYBRID
    if re.search(r"\b(remote|work from home|distributed)\b", text): return RemoteStatus.REMOTE
    if re.search(r"\b(on-site|onsite|in office|in-office)\b", text): return RemoteStatus.ONSITE
    return RemoteStatus.UNKNOWN


class GreenhouseSourceAdapter(JobSourceAdapter):
    def __init__(self, board_token: str, company: str | None = None, timeout_seconds: float = 10.0):
        self.board_token = board_token; self.company = company or board_token; self.timeout_seconds = timeout_seconds
    def _fetch_json(self, url: str) -> dict[str, Any]:
        try:
            with urlopen(Request(url, headers={"User-Agent": "job-agent/0.1"}), timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc: raise GreenhouseSourceError(f"Greenhouse board '{self.board_token}' returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc: raise GreenhouseSourceError(f"Greenhouse board '{self.board_token}' could not be read: {exc.__class__.__name__}") from exc
    def fetch_jobs(self) -> list[JobPosting]:
        payload = self._fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{self.board_token}/jobs?content=true")
        jobs = payload.get("jobs")
        if not isinstance(jobs, list): raise GreenhouseSourceError(f"Greenhouse board '{self.board_token}' response did not contain a jobs list")
        postings: list[JobPosting] = []
        for item in jobs:
            if not isinstance(item, dict) or item.get("id") is None or not item.get("title"): continue
            raw_description = item.get("content") or item.get("description") or ""
            normalized = html_to_text(raw_description); parsed = parse_greenhouse_description(raw_description)
            location = item.get("location") if isinstance(item.get("location"), dict) else None
            location_name = location.get("name") if location else None
            absolute_url = item.get("absolute_url") or item.get("internal_job_id") or f"https://boards.greenhouse.io/{self.board_token}/jobs/{item.get('id')}"
            postings.append(JobPosting(source=GREENHOUSE_SOURCE, source_job_id=str(item["id"]), employer=self.company, job_title=str(item["title"]),
                location=location_name, country=infer_country(location_name), remote_status=infer_remote_status(str(item["title"]), location_name, normalized),
                description=str(raw_description), date_posted=_parse_date(item.get("updated_at") or item.get("first_published") or item.get("published_at")),
                application_url=str(absolute_url), ats_type=ATS.GREENHOUSE, **parsed))
        return postings
