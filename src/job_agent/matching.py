from __future__ import annotations

from job_agent.models import (
    CandidateProfile, Classification, EvidenceMatch, ExperienceEvidence, JobPosting,
    MatchAnalysis, SearchPreferences,
)
from job_agent.title_matching import title_match_score


def norm(value: str) -> str:
    return value.lower().strip()


def evidence_terms(evidence: list[ExperienceEvidence]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for exp in evidence:
        terms = set(exp.skills + exp.technologies + exp.industries + exp.allowed_claims + exp.responsibilities + exp.accomplishments)
        for ev in exp.all_evidence():
            terms.update(ev.skills + ev.technologies + ev.industries + ev.allowed_claims + [ev.text])
        for term in terms:
            for token in norm(term).replace("/", " ").split():
                if len(token) > 2:
                    index.setdefault(token, []).append(exp.experience_id)
    return index


def evaluate_constraints(job: JobPosting, prefs: SearchPreferences) -> list[str]:
    violations = []
    if job.country and prefs.allowed_countries and job.country not in prefs.allowed_countries:
        violations.append(f"Country {job.country} is outside allowed countries")
    if prefs.minimum_compensation_usd and job.salary_max and job.salary_max < prefs.minimum_compensation_usd:
        violations.append(f"Salary maximum {job.salary_max} is below minimum target")
    if job.industry and norm(job.industry) in {norm(i) for i in prefs.excluded_industries}:
        violations.append(f"Industry {job.industry} is excluded")
    if job.security_clearance_requirements:
        violations.append("Security clearance requirement is present")
    return violations


def match_job(candidate: CandidateProfile, evidence: list[ExperienceEvidence], prefs: SearchPreferences, job: JobPosting, already_applied: bool = False) -> MatchAnalysis:
    del candidate
    constraints = evaluate_constraints(job, prefs)
    if already_applied:
        constraints.append("Candidate has already applied to this position")
    title_score = title_match_score(job.job_title, prefs.target_titles, prefs.title_variations)
    index = evidence_terms(evidence)
    matched: list[EvidenceMatch] = []
    unsupported: list[str] = []
    contradicted: list[str] = []

    prohibited = {norm(p) for exp in evidence for p in exp.prohibited_claims}
    for req in job.explicit_requirements:
        req_tokens = [t for t in norm(req.text).replace("/", " ").split() if len(t) > 2]
        evidence_ids = sorted({eid for token in req_tokens for eid in index.get(token, [])})
        if any(p in norm(req.text) for p in prohibited):
            contradicted.append(req.text)
        elif evidence_ids:
            confidence = min(95, 55 + len(evidence_ids) * 10 + len(set(req_tokens) & set(index.keys())) * 5)
            matched.append(EvidenceMatch(requirement=req.text, evidence_ids=evidence_ids, explanation="Keyword-backed deterministic evidence match; LLM review can refine semantics later.", confidence=confidence))
        elif req.is_hard_requirement:
            unsupported.append(req.text)

    preferred: list[EvidenceMatch] = []
    for pref in job.inferred_preferences:
        pref_tokens = [t for t in norm(pref.text).split() if len(t) > 2]
        ids = sorted({eid for token in pref_tokens for eid in index.get(token, [])})
        if ids:
            preferred.append(EvidenceMatch(requirement=pref.text, evidence_ids=ids, explanation="Candidate has evidence related to inferred preference.", confidence=75))

    requirement_count = max(len(job.explicit_requirements), 1)
    requirement_score = int((len(matched) / requirement_count) * 100)
    role_match = round(title_score * 0.45 + requirement_score * 0.45 + min(len(preferred) * 5, 10))
    evidence_confidence = round(sum(m.confidence for m in matched) / len(matched)) if matched else 0
    risk = min(100, len(unsupported) * 20 + len(contradicted) * 35 + len(constraints) * 30 + max(0, 75 - evidence_confidence))

    if constraints or contradicted or role_match < prefs.review_threshold:
        classification = Classification.REJECT
    elif role_match >= prefs.auto_apply_threshold and evidence_confidence >= prefs.evidence_confidence_threshold and not unsupported and risk < 20:
        classification = Classification.AUTO_APPLY_ELIGIBLE
    else:
        classification = Classification.REVIEW_REQUIRED

    return MatchAnalysis(
        job_id=job.source_job_id,
        role_match_score=role_match,
        evidence_confidence_score=evidence_confidence,
        application_risk_score=risk,
        classification=classification,
        matched_requirements=matched,
        unsupported_requirements=unsupported,
        contradicted_requirements=contradicted,
        preferred_matches=preferred,
        transferable_experience=[m.requirement for m in preferred],
        hard_constraint_violations=constraints,
        rationale=[f"Title score {title_score}", f"Matched {len(matched)} of {len(job.explicit_requirements)} explicit requirements"],
    )
