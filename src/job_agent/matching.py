from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from job_agent.models import (
    CandidateProfile,
    Classification,
    EvidenceMatch,
    ExperienceEvidence,
    JobPosting,
    JobRequirement,
    MatchAnalysis,
    PreferenceMode,
    RemoteStatus,
    RequirementEvaluation,
    RequirementMatchStatus,
    ScoreBreakdown,
    SearchPreferences,
)
from job_agent.title_matching import title_match_score

CATEGORY_WEIGHTS: dict[str, float] = {
    "clearance": 2.0,
    "required_technology": 1.6,
    "technology": 1.5,
    "experience_years": 1.5,
    "experience": 1.4,
    "core_skill": 1.4,
    "skill": 1.3,
    "education": 1.0,
    "domain": 1.0,
    "management": 1.0,
    "preferred_technology": 0.6,
    "preference": 0.5,
    "general": 1.0,
}
HARD_REQUIREMENT_MULTIPLIER = 1.25

CONCEPT_GROUPS: dict[str, tuple[str, ...]] = {
    "DESIGN_SYSTEMS": ("design system", "design systems", "component library", "component libraries", "ui library", "design library", "reusable components", "design tokens", "design system operations"),
    "FIGMA": ("figma", "figma library", "figma libraries", "shared figma resources"),
    "ENGINEERING_COLLABORATION": ("developer collaboration", "engineering collaboration", "partner with engineers", "partnering with engineers", "design and engineering collaboration", "cross-functional collaboration with developers", "cross-functional collaboration"),
    "COMPONENT_STANDARDIZATION": ("component standardization", "reusable interface patterns", "standardized components", "standardized component", "component consistency", "reusable ui patterns", "standardize reusable components"),
    "PRODUCT_DESIGN": ("product design", "ux design", "ui/ux design", "interaction design"),
    "ACCESSIBILITY": ("accessibility", "accessible design", "wcag", "inclusive design"),
    "RESPONSIVE_DESIGN": ("responsive design", "responsive web design", "mobile and responsive interfaces"),
    "REACT_ENGINEERING": ("react development", "react developer", "react engineering", "production interfaces in react", "build production interfaces in react", "professional react engineering"),
    "ML_ENGINEERING": ("machine learning engineering", "ml engineering", "machine learning development", "building ml models", "build ml models"),
    "SECURITY_CLEARANCE": ("security clearance", "active clearance", "clearance required"),
}
RELATED_CONCEPTS: dict[str, set[str]] = {
    "DESIGN_SYSTEMS": {"COMPONENT_STANDARDIZATION", "FIGMA", "ENGINEERING_COLLABORATION"},
    "COMPONENT_STANDARDIZATION": {"DESIGN_SYSTEMS", "ENGINEERING_COLLABORATION"},
    "ENGINEERING_COLLABORATION": {"COMPONENT_STANDARDIZATION", "DESIGN_SYSTEMS"},
    "PRODUCT_DESIGN": {"DESIGN_SYSTEMS", "ACCESSIBILITY", "RESPONSIVE_DESIGN"},
    "ACCESSIBILITY": {"PRODUCT_DESIGN", "DESIGN_SYSTEMS"},
    "RESPONSIVE_DESIGN": {"PRODUCT_DESIGN"},
}
GENERIC_TOKENS = {"experience", "work", "working", "hands", "hand", "required", "preferred", "familiarity", "years", "year", "team", "role", "using", "with", "and", "the", "for", "on"}


@dataclass(frozen=True)
class EvidenceRecord:
    experience_id: str
    statement_id: str | None
    text: str
    skills: tuple[str, ...] = ()
    technologies: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    allowed_claims: tuple[str, ...] = ()
    prohibited_claims: tuple[str, ...] = ()
    evidence_type: str = "statement"
    source_note: str | None = None
    concepts: frozenset[str] = field(default_factory=frozenset)
    tokens: frozenset[str] = field(default_factory=frozenset)


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower().replace("ui/ux", "ui ux").replace("/", " ")).strip()


def text_tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", norm(value)) if len(token) > 2 and token not in GENERIC_TOKENS}


def concepts_for_text(value: str) -> set[str]:
    normalized = norm(value)
    concepts: set[str] = set()
    for concept, phrases in CONCEPT_GROUPS.items():
        if any(norm(phrase) in normalized for phrase in phrases):
            concepts.add(concept)
    return concepts


def build_evidence_index(evidence: list[ExperienceEvidence]) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []
    for exp in evidence:
        exp_text = " ".join(exp.responsibilities + exp.accomplishments + exp.skills + exp.technologies + exp.industries + exp.allowed_claims)
        records.append(_record_from_parts(exp.experience_id, None, exp_text, exp.skills, exp.technologies, exp.industries, exp.allowed_claims, exp.prohibited_claims, "experience", exp.source_note))
        for ev in exp.all_evidence():
            records.append(_record_from_parts(exp.experience_id, ev.statement_id, ev.text, ev.skills, ev.technologies, ev.industries, ev.allowed_claims, ev.prohibited_claims, "statement", ev.source_note))
    return records


def _record_from_parts(experience_id: str, statement_id: str | None, text: str, skills: list[str], technologies: list[str], industries: list[str], allowed_claims: list[str], prohibited_claims: list[str], evidence_type: str, source_note: str | None) -> EvidenceRecord:
    blob = " ".join([text, *skills, *technologies, *industries, *allowed_claims])
    return EvidenceRecord(
        experience_id=experience_id,
        statement_id=statement_id,
        text=text,
        skills=tuple(skills),
        technologies=tuple(technologies),
        industries=tuple(industries),
        allowed_claims=tuple(allowed_claims),
        prohibited_claims=tuple(prohibited_claims),
        evidence_type=evidence_type,
        source_note=source_note,
        concepts=frozenset(concepts_for_text(blob)),
        tokens=frozenset(text_tokens(blob)),
    )


def category_weight(category: str, is_hard: bool) -> float:
    weight = CATEGORY_WEIGHTS.get(category, CATEGORY_WEIGHTS["general"])
    return round(weight * (HARD_REQUIREMENT_MULTIPLIER if is_hard else 1.0), 2)


def professional_design_years(evidence: list[ExperienceEvidence], today: date | None = None) -> float:
    today = today or date.today()
    intervals: list[tuple[date, date]] = []
    qualifying = {"PRODUCT_DESIGN", "DESIGN_SYSTEMS", "FIGMA", "COMPONENT_STANDARDIZATION"}
    for exp in evidence:
        if not exp.start_date:
            continue
        end = today if exp.end_date == "present" or exp.end_date is None else exp.end_date
        concepts = concepts_for_text(" ".join([exp.role, *exp.skills, *exp.responsibilities, *exp.allowed_claims]))
        if concepts & qualifying or "designer" in norm(exp.role):
            intervals.append((exp.start_date, end))
    if not intervals:
        return 0.0
    intervals.sort()
    merged: list[list[date]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        elif end > merged[-1][1]:
            merged[-1][1] = end
    return round(sum((end - start).days for start, end in merged) / 365.25, 1)


def _dedupe_requirements(requirements: list[JobRequirement]) -> list[JobRequirement]:
    seen: set[tuple[str, str]] = set()
    deduped: list[JobRequirement] = []
    for req in requirements:
        key = (norm(req.text), req.category)
        if key not in seen:
            seen.add(key)
            deduped.append(req)
    return deduped


def _requirements_from_job(job: JobPosting) -> tuple[list[JobRequirement], list[JobRequirement]]:
    explicit = list(job.explicit_requirements)
    inferred = list(job.inferred_preferences)
    existing_text = "\n".join(r.text for r in explicit + inferred).lower()
    for tech in job.required_technologies:
        if tech.lower() not in existing_text:
            explicit.append(JobRequirement(text=f"Required technology: {tech}", requirement_type="explicit", category="required_technology", is_hard_requirement=True))
    for tech in job.preferred_technologies:
        if tech.lower() not in existing_text:
            inferred.append(JobRequirement(text=f"Preferred technology: {tech}", requirement_type="inferred", category="preferred_technology"))
        else:
            for req in inferred:
                if tech.lower() in req.text.lower() and req.category == "technology":
                    req.category = "preferred_technology"
    if job.required_years_experience and not re.search(r"\d+\+?\s+years", existing_text):
        explicit.append(JobRequirement(text=f"{job.required_years_experience}+ years of product or UX design experience", requirement_type="explicit", category="experience_years", is_hard_requirement=True))
    for domain in job.domain_experience_expectations:
        if domain.lower() not in existing_text:
            inferred.append(JobRequirement(text=f"Domain experience: {domain}", requirement_type="inferred", category="domain"))
    for education in job.education_requirements:
        if education.lower() not in existing_text:
            explicit.append(JobRequirement(text=f"Education requirement: {education}", requirement_type="explicit", category="education", is_hard_requirement=True))
    for clearance in job.security_clearance_requirements:
        if clearance.lower() not in existing_text:
            explicit.append(JobRequirement(text=f"Security clearance requirement: {clearance}", requirement_type="explicit", category="clearance", is_hard_requirement=True))
    if job.management_expectations:
        inferred.append(JobRequirement(text=f"Management expectation: {job.management_expectations}", requirement_type="inferred", category="management"))
    return _dedupe_requirements(explicit), _dedupe_requirements(inferred)


def _prohibited_concepts(evidence: list[ExperienceEvidence]) -> set[str]:
    concepts: set[str] = set()
    for exp in evidence:
        for claim in exp.prohibited_claims:
            concepts.update(concepts_for_text(claim))
    return concepts


def _status_points(status: RequirementMatchStatus) -> float:
    return {
        RequirementMatchStatus.SUPPORTED: 1.0,
        RequirementMatchStatus.PARTIALLY_SUPPORTED: 0.65,
        RequirementMatchStatus.TRANSFERABLE: 0.45,
        RequirementMatchStatus.UNSUPPORTED: 0.0,
        RequirementMatchStatus.CONTRADICTED: 0.0,
    }[status]


def evaluate_requirement(req: JobRequirement, records: list[EvidenceRecord], prohibited_concepts: set[str], design_years: float) -> RequirementEvaluation:
    req_concepts = concepts_for_text(req.text)
    req_tokens = text_tokens(req.text)
    weight = category_weight(req.category, req.is_hard_requirement)

    if req.category == "experience_years" or re.search(r"\d+\+?\s+years", req.text.lower()):
        needed = int(re.search(r"(\d+)", req.text).group(1)) if re.search(r"(\d+)", req.text) else 0
        if design_years >= needed:
            return RequirementEvaluation(requirement=req.text, category="experience_years", is_hard_requirement=req.is_hard_requirement, status=RequirementMatchStatus.SUPPORTED, confidence=min(95, 75 + int((design_years - needed) * 4)), matched_experience_ids=sorted({r.experience_id for r in records if "PRODUCT_DESIGN" in r.concepts or "DESIGN_SYSTEMS" in r.concepts}), explanation=f"Calculated {design_years} non-overlapping years of dated design/product UX experience against {needed}+ required years.", weight=weight)
        return RequirementEvaluation(requirement=req.text, category="experience_years", is_hard_requirement=req.is_hard_requirement, status=RequirementMatchStatus.UNSUPPORTED, confidence=20, explanation=f"Only {design_years} dated qualifying years found for {needed}+ required years.", weight=weight, blocks_auto_apply=req.is_hard_requirement)

    if req_concepts & prohibited_concepts:
        return RequirementEvaluation(requirement=req.text, category=req.category, is_hard_requirement=req.is_hard_requirement, status=RequirementMatchStatus.CONTRADICTED, confidence=95, explanation="Requirement maps to a concept explicitly prohibited by verified candidate evidence.", weight=weight, blocks_auto_apply=True, blocks_application_consideration=True)

    direct: list[EvidenceRecord] = []
    partial: list[EvidenceRecord] = []
    transferable: list[EvidenceRecord] = []
    for record in records:
        concept_overlap = req_concepts & set(record.concepts)
        token_overlap = req_tokens & set(record.tokens)
        tech_overlap = {norm(t) for t in record.technologies} & {norm(t) for t in req_tokens | req_concepts}
        if concept_overlap or tech_overlap or (req.category in {"technology", "required_technology", "preferred_technology"} and any(norm(t) in norm(req.text) for t in record.technologies)):
            direct.append(record)
        elif req_concepts and any(RELATED_CONCEPTS.get(c, set()) & set(record.concepts) for c in req_concepts):
            transferable.append(record)
        elif len(token_overlap) >= 2:
            partial.append(record)

    if direct:
        statement_count = len({r.statement_id for r in direct if r.statement_id})
        experience_count = len({r.experience_id for r in direct})
        status = RequirementMatchStatus.SUPPORTED
        if statement_count:
            confidence = min(98, 76 + statement_count * 7 + experience_count * 3)
            explanation = "Direct normalized concept/technology match to verified evidence statements."
        else:
            confidence = min(74, 62 + experience_count * 6)
            explanation = "Direct normalized concept/technology match only to broad experience-level evidence; statement-level support is not verified."
    elif partial:
        status = RequirementMatchStatus.PARTIALLY_SUPPORTED
        confidence = min(78, 45 + len({r.statement_id for r in partial if r.statement_id}) * 6)
        explanation = "Partial deterministic phrase/token overlap; requires human review for semantic fit."
        direct = partial
    elif transferable:
        status = RequirementMatchStatus.TRANSFERABLE
        confidence = min(70, 42 + len({r.statement_id for r in transferable if r.statement_id}) * 6)
        explanation = "Related concept evidence exists, but direct requirement evidence is not verified."
        direct = transferable
    else:
        status = RequirementMatchStatus.UNSUPPORTED
        confidence = 15
        explanation = "No verified evidence statement or experience record supports this requirement."

    blocks_auto = status in {RequirementMatchStatus.UNSUPPORTED, RequirementMatchStatus.TRANSFERABLE, RequirementMatchStatus.CONTRADICTED} and req.is_hard_requirement
    blocks_all = status == RequirementMatchStatus.CONTRADICTED and req.is_hard_requirement
    return RequirementEvaluation(
        requirement=req.text,
        category=req.category,
        is_hard_requirement=req.is_hard_requirement,
        status=status,
        confidence=confidence,
        matched_evidence_statement_ids=sorted({r.statement_id for r in direct if r.statement_id}),
        matched_experience_ids=sorted({r.experience_id for r in direct}),
        explanation=explanation,
        weight=weight,
        blocks_auto_apply=blocks_auto,
        blocks_application_consideration=blocks_all,
    )


def _search_preference_signals(job: JobPosting, prefs: SearchPreferences) -> tuple[list[str], list[str], int, int, int]:
    absolute_blockers: list[str] = []
    review_concerns: list[str] = []
    industry_domain = 50
    work_arrangement = 50
    ic_management = 50

    if job.country and prefs.allowed_countries and job.country not in prefs.allowed_countries:
        absolute_blockers.append(f"Country {job.country} is outside allowed countries")
    if prefs.minimum_compensation_usd and job.salary_max and job.salary_max < prefs.minimum_compensation_usd:
        review_concerns.append(f"Salary maximum {job.salary_max} is below minimum target {prefs.minimum_compensation_usd}")
    if job.industry and norm(job.industry) in {norm(i) for i in prefs.excluded_industries}:
        message = f"Industry {job.industry} is excluded"
        if prefs.excluded_industries_are_hard:
            absolute_blockers.append(message)
        else:
            review_concerns.append(message)
    if job.security_clearance_requirements:
        absolute_blockers.append("Required security clearance is present and no verified clearance evidence exists")
    if job.industry and norm(job.industry) in {norm(i) for i in prefs.preferred_industries}:
        industry_domain += 25
    if job.domain_experience_expectations:
        industry_domain += 10

    if prefs.preferred_remote_statuses and job.remote_status in prefs.preferred_remote_statuses:
        work_arrangement = 95
    elif prefs.preferred_remote_statuses and job.remote_status != RemoteStatus.UNKNOWN:
        message = f"Work arrangement {job.remote_status.value} is outside preferred statuses"
        if prefs.remote_preference_mode == PreferenceMode.HARD_REQUIREMENT:
            absolute_blockers.append(message)
        else:
            review_concerns.append(message)
            work_arrangement = 35

    management_text = norm(job.management_expectations or "")
    people_mgmt = any(phrase in management_text for phrase in ["people manager", "manage a team", "direct reports", "line management"])
    ic_role = "individual contributor" in management_text or "cross-functional" in management_text or not management_text
    if prefs.prefer_individual_contributor and ic_role:
        ic_management = 90
    elif prefs.prefer_individual_contributor and people_mgmt:
        review_concerns.append("Role appears to include people-management expectations")
        ic_management = 30

    return absolute_blockers, review_concerns, min(industry_domain, 100), work_arrangement, ic_management


def _coverage(evaluations: list[RequirementEvaluation], only_hard: bool | None = None) -> int:
    items = [e for e in evaluations if only_hard is None or e.is_hard_requirement is only_hard]
    possible = sum(e.weight for e in items)
    if possible == 0:
        return 100
    points = sum(e.weight * _status_points(e.status) for e in items)
    return round(points / possible * 100)


def _evidence_confidence(evaluations: list[RequirementEvaluation]) -> int:
    possible = sum(e.weight for e in evaluations)
    if possible == 0:
        return 0
    adjusted = 0.0
    for e in evaluations:
        quality = e.confidence
        if e.status == RequirementMatchStatus.UNSUPPORTED:
            quality = 10
        elif e.status == RequirementMatchStatus.CONTRADICTED:
            quality = 0
        elif e.status == RequirementMatchStatus.TRANSFERABLE:
            quality = min(quality, 58)
        elif e.status == RequirementMatchStatus.PARTIALLY_SUPPORTED:
            quality = min(quality, 72)
        elif e.status == RequirementMatchStatus.SUPPORTED and not e.matched_evidence_statement_ids:
            quality = min(quality, 68)
        adjusted += max(0, quality) * e.weight
    return round(adjusted / possible)


def _application_risk(evaluations: list[RequirementEvaluation], absolute_blockers: list[str], review_concerns: list[str], evidence_confidence: int) -> int:
    if absolute_blockers:
        return min(100, 70 + (len(absolute_blockers) - 1) * 10)
    risk = 0
    if any(e.status == RequirementMatchStatus.CONTRADICTED for e in evaluations):
        risk = max(risk, 85)
    unsupported_hard = [e for e in evaluations if e.is_hard_requirement and e.status == RequirementMatchStatus.UNSUPPORTED]
    transferable_hard = [e for e in evaluations if e.is_hard_requirement and e.status == RequirementMatchStatus.TRANSFERABLE]
    partial_hard = [e for e in evaluations if e.is_hard_requirement and e.status == RequirementMatchStatus.PARTIALLY_SUPPORTED]
    unsupported_soft = [e for e in evaluations if not e.is_hard_requirement and e.status == RequirementMatchStatus.UNSUPPORTED]
    if unsupported_hard:
        risk = max(risk, 38 + min(22, (len(unsupported_hard) - 1) * 8))
    if transferable_hard:
        risk = max(risk, 24 + min(16, (len(transferable_hard) - 1) * 5))
    if partial_hard:
        risk = max(risk, 14 + min(12, (len(partial_hard) - 1) * 4))
    risk += min(12, len(unsupported_soft) * 3)
    non_auto_concerns = [c for c in review_concerns if not c.startswith("Auto-apply blocker")]
    risk += min(16, len(non_auto_concerns) * 6)
    if evidence_confidence < 50:
        risk += 24
    elif evidence_confidence < 65:
        risk += 12
    elif evidence_confidence < 78:
        risk += 5
    return min(100, risk)


def match_job(candidate: CandidateProfile, evidence: list[ExperienceEvidence], prefs: SearchPreferences, job: JobPosting, already_applied: bool = False) -> MatchAnalysis:
    del candidate
    records = build_evidence_index(evidence)
    explicit, inferred = _requirements_from_job(job)
    design_years = professional_design_years(evidence)
    prohibited = _prohibited_concepts(evidence)
    evaluations = [evaluate_requirement(req, records, prohibited, design_years) for req in explicit]
    preference_evals = [evaluate_requirement(req, records, prohibited, design_years) for req in inferred]
    all_evaluations = evaluations + preference_evals

    absolute_blockers, review_concerns, industry_domain, work_arrangement, ic_management = _search_preference_signals(job, prefs)
    if already_applied:
        absolute_blockers.append("Candidate has already applied to this position")
    for e in all_evaluations:
        if e.blocks_application_consideration:
            absolute_blockers.append(f"Contradicted requirement: {e.requirement}")
        elif e.blocks_auto_apply:
            review_concerns.append(f"Auto-apply blocker: {e.requirement} is {e.status.value}")

    title_score = title_match_score(job.job_title, prefs.target_titles, prefs.title_variations)
    requirement_coverage = _coverage(evaluations)
    hard_coverage = _coverage(evaluations, only_hard=True)
    preference_alignment = _coverage(preference_evals, only_hard=False) if preference_evals else 65

    # Inspectable final role score formula. Explicit/hard requirements dominate; preferences can help only modestly.
    # Contradictions and absolute blockers force REJECT later and cannot be offset by preferred qualifications.
    role_score = round(
        title_score * 0.22
        + requirement_coverage * 0.32
        + hard_coverage * 0.18
        + preference_alignment * 0.08
        + industry_domain * 0.08
        + work_arrangement * 0.07
        + ic_management * 0.05
    )
    evidence_confidence = _evidence_confidence(evaluations)
    risk = _application_risk(all_evaluations, absolute_blockers, review_concerns, evidence_confidence)

    auto_apply_blockers = [c for c in review_concerns if c.startswith("Auto-apply blocker")]
    if absolute_blockers:
        classification = Classification.REJECT
        rationale = ["Rejected because one or more absolute blockers are present."]
    elif role_score >= prefs.auto_apply_threshold and evidence_confidence >= prefs.evidence_confidence_threshold and not auto_apply_blockers and risk < 20:
        classification = Classification.AUTO_APPLY_ELIGIBLE
        rationale = ["Meets configured role and evidence thresholds with no automatic-application blockers."]
    elif role_score >= prefs.review_threshold:
        classification = Classification.REVIEW_REQUIRED
        rationale = ["Meets review threshold but requires human review for concerns, gaps, or threshold confidence."]
    else:
        classification = Classification.REJECT
        rationale = ["Rejected because weighted role score is below configured review threshold."]

    matched = [EvidenceMatch(requirement=e.requirement, evidence_ids=e.matched_experience_ids, statement_ids=e.matched_evidence_statement_ids, explanation=e.explanation, confidence=e.confidence) for e in evaluations if e.status == RequirementMatchStatus.SUPPORTED]
    preferred = [EvidenceMatch(requirement=e.requirement, evidence_ids=e.matched_experience_ids, statement_ids=e.matched_evidence_statement_ids, explanation=e.explanation, confidence=e.confidence) for e in preference_evals if e.status in {RequirementMatchStatus.SUPPORTED, RequirementMatchStatus.PARTIALLY_SUPPORTED, RequirementMatchStatus.TRANSFERABLE}]
    weighted_possible = sum(e.weight for e in evaluations)
    weighted_points = sum(e.weight * _status_points(e.status) for e in evaluations)
    breakdown = ScoreBreakdown(
        title_alignment=title_score,
        requirement_coverage=requirement_coverage,
        hard_requirement_coverage=hard_coverage,
        preferred_qualification_alignment=preference_alignment,
        industry_domain_alignment=industry_domain,
        work_arrangement_alignment=work_arrangement,
        ic_management_alignment=ic_management,
        weighted_requirement_points=round(weighted_points, 2),
        weighted_requirement_possible=round(weighted_possible, 2),
        formula="title .22 + req .32 + hard_req .18 + preferences .08 + industry/domain .08 + work arrangement .07 + IC/management .05",
    )
    return MatchAnalysis(
        job_id=job.source_job_id,
        role_match_score=role_score,
        evidence_confidence_score=evidence_confidence,
        application_risk_score=risk,
        classification=classification,
        requirement_evaluations=all_evaluations,
        auto_apply_blockers=auto_apply_blockers,
        review_concerns=review_concerns,
        score_breakdown=breakdown,
        title_score=title_score,
        requirement_coverage_score=requirement_coverage,
        preference_alignment_score=preference_alignment,
        final_classification_rationale=rationale,
        matched_requirements=matched,
        unsupported_requirements=[e.requirement for e in evaluations if e.status == RequirementMatchStatus.UNSUPPORTED],
        contradicted_requirements=[e.requirement for e in evaluations if e.status == RequirementMatchStatus.CONTRADICTED],
        preferred_matches=preferred,
        transferable_experience=[e.requirement for e in all_evaluations if e.status == RequirementMatchStatus.TRANSFERABLE],
        hard_constraint_violations=absolute_blockers,
        rationale=[*rationale, f"Title score {title_score}", f"Weighted explicit requirement coverage {requirement_coverage}"],
    )
