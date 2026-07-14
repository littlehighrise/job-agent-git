from __future__ import annotations

from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


class RemoteStatus(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    UNKNOWN = "unknown"


class ATS(StrEnum):
    GREENHOUSE = "greenhouse"
    ASHBY = "ashby"
    LEVER = "lever"
    GENERIC = "generic"
    LOCAL_JSON = "local_json"


class Classification(StrEnum):
    REJECT = "REJECT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    AUTO_APPLY_ELIGIBLE = "AUTO_APPLY_ELIGIBLE"


class QuestionType(StrEnum):
    STATIC_FACT = "STATIC_FACT"
    VERIFIED_PROFILE_FACT = "VERIFIED_PROFILE_FACT"
    GENERATIVE_RESPONSE = "GENERATIVE_RESPONSE"
    USER_DECISION_REQUIRED = "USER_DECISION_REQUIRED"
    LEGAL_OR_ATTESTATION = "LEGAL_OR_ATTESTATION"


class Eligibility(BaseModel):
    resume: bool = True
    cover_letter: bool = True
    application_answers: bool = True


class EvidenceStatement(BaseModel):
    statement_id: str
    text: str
    skills: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    verified_metrics: list[str] = Field(default_factory=list)
    allowed_claims: list[str] = Field(default_factory=list)
    prohibited_claims: list[str] = Field(default_factory=list)
    source_note: str | None = None
    eligibility: Eligibility = Field(default_factory=Eligibility)
    seniority_evidence: bool = False
    management_evidence: bool = False
    individual_contributor_evidence: bool = True


class ProjectEvidence(BaseModel):
    project_id: str
    name: str
    responsibilities: list[str] = Field(default_factory=list)
    accomplishments: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    evidence: list[EvidenceStatement] = Field(default_factory=list)


class ExperienceEvidence(BaseModel):
    experience_id: str
    employer: str
    role: str
    start_date: date | None = None
    end_date: date | Literal["present"] | None = None
    projects: list[ProjectEvidence] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    accomplishments: list[str] = Field(default_factory=list)
    evidence: list[EvidenceStatement] = Field(default_factory=list)
    allowed_claims: list[str] = Field(default_factory=list)
    prohibited_claims: list[str] = Field(default_factory=list)
    source_note: str | None = None
    eligibility: Eligibility = Field(default_factory=Eligibility)
    seniority_evidence: bool = False
    management_evidence: bool = False
    individual_contributor_evidence: bool = True

    def all_evidence(self) -> list[EvidenceStatement]:
        items = list(self.evidence)
        for project in self.projects:
            items.extend(project.evidence)
        return items


class CandidateProfile(BaseModel):
    candidate_id: str
    full_name: str
    email: str
    phone: str | None = None
    location: str | None = None
    portfolio_url: str | None = None
    linkedin_url: str | None = None
    static_facts: dict[str, Any] = Field(default_factory=dict)


class SearchPreferences(BaseModel):
    target_titles: list[str]
    title_variations: dict[str, list[str]] = Field(default_factory=dict)
    preferred_remote_statuses: list[RemoteStatus] = Field(default_factory=lambda: [RemoteStatus.REMOTE])
    allowed_countries: list[str] = Field(default_factory=lambda: ["United States"])
    minimum_compensation_usd: int | None = None
    excluded_industries: list[str] = Field(default_factory=list)
    preferred_industries: list[str] = Field(default_factory=list)
    prefer_individual_contributor: bool = True
    polling_interval_minutes: int = 60
    review_threshold: int = 75
    auto_apply_threshold: int = 92
    evidence_confidence_threshold: int = 85
    sources: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("polling_interval_minutes")
    @classmethod
    def enforce_hourly_max_frequency(cls, value: int) -> int:
        if value < 60:
            raise ValueError("MVP polling interval cannot be more frequent than once per hour")
        return value


class JobRequirement(BaseModel):
    text: str
    requirement_type: Literal["explicit", "inferred"]
    category: str = "general"
    is_hard_requirement: bool = False


class JobPosting(BaseModel):
    source: str
    source_job_id: str
    employer: str
    job_title: str
    canonical_job_title: str | None = None
    job_title_variations: list[str] = Field(default_factory=list)
    seniority: str | None = None
    employment_type: str | None = None
    location: str | None = None
    country: str | None = None
    remote_status: RemoteStatus = RemoteStatus.UNKNOWN
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str | None = "USD"
    industry: str | None = None
    description: str
    responsibilities: list[str] = Field(default_factory=list)
    explicit_requirements: list[JobRequirement] = Field(default_factory=list)
    inferred_preferences: list[JobRequirement] = Field(default_factory=list)
    required_technologies: list[str] = Field(default_factory=list)
    preferred_technologies: list[str] = Field(default_factory=list)
    required_years_experience: int | None = None
    management_expectations: str | None = None
    domain_experience_expectations: list[str] = Field(default_factory=list)
    education_requirements: list[str] = Field(default_factory=list)
    security_clearance_requirements: list[str] = Field(default_factory=list)
    travel_requirements: str | None = None
    date_posted: date | None = None
    date_discovered: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    application_url: str
    ats_type: ATS = ATS.GENERIC


class EvidenceMatch(BaseModel):
    requirement: str
    evidence_ids: list[str]
    explanation: str
    confidence: int


class MatchAnalysis(BaseModel):
    job_id: str
    role_match_score: int
    evidence_confidence_score: int
    application_risk_score: int
    classification: Classification
    matched_requirements: list[EvidenceMatch] = Field(default_factory=list)
    unsupported_requirements: list[str] = Field(default_factory=list)
    contradicted_requirements: list[str] = Field(default_factory=list)
    preferred_matches: list[EvidenceMatch] = Field(default_factory=list)
    transferable_experience: list[str] = Field(default_factory=list)
    hard_constraint_violations: list[str] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)


class ResumePlanItem(BaseModel):
    job_requirement: str
    selected_candidate_evidence: list[str]
    why_relevant: str
    intended_resume_section: str
    proposed_framing: str
    confidence: int


class ResumeContentPlan(BaseModel):
    job_id: str
    items: list[ResumePlanItem]


class ResumeExperience(BaseModel):
    employer: str
    title: str
    bullets: list[str]


class StructuredResume(BaseModel):
    candidate_name: str
    headline: str
    contact: dict[str, str | None]
    summary: str
    competencies: list[str]
    experience: list[ResumeExperience]
    project_highlights: list[str] = Field(default_factory=list)


class AuditFinding(BaseModel):
    severity: Literal["info", "warning", "blocker"]
    category: str
    message: str
    referenced_text: str | None = None


class FactualConsistencyAudit(BaseModel):
    job_id: str
    passed: bool
    findings: list[AuditFinding] = Field(default_factory=list)
