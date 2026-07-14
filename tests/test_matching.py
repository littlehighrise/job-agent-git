from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path

from job_agent.io import load_model, load_model_list
from job_agent.matching import concepts_for_text, evaluate_requirement, build_evidence_index, match_job, professional_design_years
from job_agent.models import (
    CandidateProfile,
    Classification,
    ExperienceEvidence,
    JobPosting,
    JobRequirement,
    PreferenceMode,
    RemoteStatus,
    RequirementMatchStatus,
    SearchPreferences,
)


def fixtures():
    profile = load_model(Path("config/candidate_profile.json"), CandidateProfile)
    evidence = load_model_list(Path("config/career_evidence.json"), ExperienceEvidence)
    prefs = load_model(Path("config/search_preferences.json"), SearchPreferences)
    jobs = load_model_list(Path("data/sample_jobs/jobs.json"), JobPosting)
    return profile, evidence, prefs, jobs


def eval_req(text: str, category: str = "skill", hard: bool = True):
    _, evidence, _, _ = fixtures()
    return evaluate_requirement(JobRequirement(text=text, requirement_type="explicit", category=category, is_hard_requirement=hard), build_evidence_index(evidence), set(), professional_design_years(evidence, today=date(2026, 7, 14)))


def test_phrase_concept_normalization_and_design_system_synonyms():
    assert "DESIGN_SYSTEMS" in concepts_for_text("Maintain a component library and reusable components")
    assert "DESIGN_SYSTEMS" in concepts_for_text("Shared Figma design system operations")


def test_specific_evidence_statement_traceability():
    evaluation = eval_req("Hands-on Figma design systems experience")
    assert evaluation.status == RequirementMatchStatus.SUPPORTED
    assert "caci_figma_admin" in evaluation.matched_evidence_statement_ids
    assert evaluation.matched_experience_ids == ["caci_design_system"]


def test_hard_requirement_supported():
    evaluation = eval_req("Experience partnering with engineers on component standardization")
    assert evaluation.status in {RequirementMatchStatus.SUPPORTED, RequirementMatchStatus.PARTIALLY_SUPPORTED}
    assert not evaluation.blocks_application_consideration


def test_hard_requirement_unsupported_blocks_auto_only():
    evaluation = eval_req("Kubernetes administration", category="core_skill")
    assert evaluation.status == RequirementMatchStatus.UNSUPPORTED
    assert evaluation.blocks_auto_apply
    assert not evaluation.blocks_application_consideration


def test_prohibited_claim_contradiction():
    _, evidence, _, _ = fixtures()
    evaluation = evaluate_requirement(
        JobRequirement(text="Build production interfaces in React", requirement_type="explicit", category="technology", is_hard_requirement=True),
        build_evidence_index(evidence),
        {"REACT_ENGINEERING"},
        professional_design_years(evidence, today=date(2026, 7, 14)),
    )
    assert evaluation.status == RequirementMatchStatus.CONTRADICTED
    assert evaluation.blocks_application_consideration


def test_transferable_experience():
    evaluation = eval_req("Responsive design for product interfaces", category="skill", hard=False)
    assert evaluation.status == RequirementMatchStatus.TRANSFERABLE


def test_required_years_of_experience_supported_from_dates():
    evaluation = eval_req("5+ years of product or UX design experience", category="experience_years")
    assert evaluation.status == RequirementMatchStatus.SUPPORTED
    assert "caci_design_system" in evaluation.matched_experience_ids


def test_overlapping_employment_dates_are_not_double_counted():
    _, evidence, _, _ = fixtures()
    overlapping = deepcopy(evidence[0])
    overlapping.experience_id = "overlap_design_role"
    overlapping.start_date = date(2022, 1, 1)
    overlapping.end_date = date(2023, 1, 1)
    years = professional_design_years([evidence[0], overlapping], today=date(2026, 1, 1))
    assert 4.9 <= years <= 5.1


def test_excluded_industry_blocker():
    profile, evidence, prefs, jobs = fixtures()
    analysis = match_job(profile, evidence, prefs, jobs[1])
    assert any("Industry defense is excluded" in blocker for blocker in analysis.hard_constraint_violations)


def test_security_clearance_blocker():
    profile, evidence, prefs, jobs = fixtures()
    analysis = match_job(profile, evidence, prefs, jobs[1])
    assert any("security clearance" in blocker.lower() or "clearance" in blocker.lower() for blocker in analysis.hard_constraint_violations + analysis.contradicted_requirements)


def test_remote_preference_behavior_is_review_concern_not_reject_when_not_hard():
    profile, evidence, prefs, jobs = fixtures()
    prefs.excluded_industries = []
    prefs.minimum_compensation_usd = None
    prefs.remote_preference_mode = PreferenceMode.PREFERENCE
    job = jobs[1].model_copy(update={"industry": "SaaS", "security_clearance_requirements": [], "explicit_requirements": []})
    analysis = match_job(profile, evidence, prefs, job)
    assert any("Work arrangement onsite" in concern for concern in analysis.review_concerns)
    assert not any("Work arrangement onsite" in blocker for blocker in analysis.hard_constraint_violations)


def test_preferred_industry_bonus():
    profile, evidence, prefs, jobs = fixtures()
    preferred = match_job(profile, evidence, prefs, jobs[0])
    neutral_job = jobs[0].model_copy(update={"industry": "utilities"})
    neutral = match_job(profile, evidence, prefs, neutral_job)
    assert preferred.score_breakdown.industry_domain_alignment > neutral.score_breakdown.industry_domain_alignment


def test_individual_contributor_preference():
    profile, evidence, prefs, jobs = fixtures()
    analysis = match_job(profile, evidence, prefs, jobs[0])
    assert analysis.score_breakdown.ic_management_alignment >= 80


def test_required_technology_evaluation():
    profile, evidence, prefs, jobs = fixtures()
    analysis = match_job(profile, evidence, prefs, jobs[0])
    assert any(e.requirement == "Hands-on Figma design systems experience" and e.status == RequirementMatchStatus.SUPPORTED for e in analysis.requirement_evaluations)


def test_preferred_technology_evaluation():
    profile, evidence, prefs, jobs = fixtures()
    analysis = match_job(profile, evidence, prefs, jobs[0])
    assert any(e.category == "preferred_technology" and e.status == RequirementMatchStatus.SUPPORTED for e in analysis.requirement_evaluations)


def test_no_generic_token_false_positive():
    evaluation = eval_req("Experience working with teams", category="general", hard=True)
    assert evaluation.status == RequirementMatchStatus.UNSUPPORTED


def test_strong_design_system_job_reaches_review_or_auto_apply():
    profile, evidence, prefs, jobs = fixtures()
    analysis = match_job(profile, evidence, prefs, jobs[0])
    assert analysis.classification in {Classification.REVIEW_REQUIRED, Classification.AUTO_APPLY_ELIGIBLE}
    assert analysis.role_match_score >= prefs.review_threshold
    assert analysis.matched_requirements
    assert analysis.requirement_evaluations
    assert analysis.score_breakdown


def test_defense_react_ml_job_rejected():
    profile, evidence, prefs, jobs = fixtures()
    analysis = match_job(profile, evidence, prefs, jobs[1])
    assert analysis.classification == Classification.REJECT
    assert analysis.hard_constraint_violations
    assert analysis.contradicted_requirements
