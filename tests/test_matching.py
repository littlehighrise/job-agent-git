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
    assert "caci_senior_ui_ux" in evaluation.matched_experience_ids


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
    assert evaluation.status == RequirementMatchStatus.SUPPORTED


def test_required_years_of_experience_supported_from_dates():
    evaluation = eval_req("5+ years of product or UX design experience", category="experience_years")
    assert evaluation.status == RequirementMatchStatus.SUPPORTED
    assert "caci_senior_ui_ux" in evaluation.matched_experience_ids


def test_overlapping_employment_dates_are_not_double_counted():
    _, evidence, _, _ = fixtures()
    overlapping = deepcopy(evidence[0])
    overlapping.experience_id = "overlap_design_role"
    overlapping.start_date = date(2022, 1, 1)
    overlapping.end_date = date(2023, 1, 1)
    years = professional_design_years([evidence[0], overlapping], today=date(2026, 1, 1))
    assert 3.4 <= years <= 3.5


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


def test_multiple_direct_evidence_statements_produce_high_confidence():
    evaluation = eval_req("Hands-on design systems and design tokens experience")
    assert evaluation.status == RequirementMatchStatus.SUPPORTED
    assert len(evaluation.matched_evidence_statement_ids) >= 2
    assert evaluation.confidence >= 90


def test_experience_only_support_has_lower_confidence_than_statement_support():
    evaluation = eval_req("enterprise software", category="domain", hard=False)
    assert evaluation.status == RequirementMatchStatus.PARTIALLY_SUPPORTED
    assert evaluation.matched_experience_ids
    assert not evaluation.matched_evidence_statement_ids
    assert evaluation.confidence <= 72


def test_minor_soft_gap_does_not_create_extreme_application_risk():
    profile, evidence, prefs, jobs = fixtures()
    job = jobs[0].model_copy(update={
        "inferred_preferences": [JobRequirement(text="Startup experience preferred", requirement_type="inferred", category="preference")],
        "preferred_technologies": [],
    })
    analysis = match_job(profile, evidence, prefs, job)
    assert analysis.application_risk_score < 25
    assert analysis.classification in {Classification.REVIEW_REQUIRED, Classification.AUTO_APPLY_ELIGIBLE}


def test_unsupported_hard_requirement_blocks_auto_without_extreme_risk_or_rejection():
    profile, evidence, prefs, jobs = fixtures()
    job = jobs[0].model_copy(update={
        "explicit_requirements": [*jobs[0].explicit_requirements, JobRequirement(text="Required expertise in Kubernetes administration", requirement_type="explicit", category="required_technology", is_hard_requirement=True)],
        "required_technologies": ["Figma"],
    })
    analysis = match_job(profile, evidence, prefs, job)
    assert any("Kubernetes" in blocker for blocker in analysis.auto_apply_blockers)
    assert 35 <= analysis.application_risk_score < 70
    assert analysis.classification == Classification.REVIEW_REQUIRED


def test_absolute_blocker_remains_high_risk_rejection():
    profile, evidence, prefs, jobs = fixtures()
    analysis = match_job(profile, evidence, prefs, jobs[1])
    assert analysis.classification == Classification.REJECT
    assert analysis.application_risk_score >= 70


def test_people_management_role_mismatch_lowers_ranking_and_adds_review_concern():
    profile, evidence, prefs, jobs = fixtures()
    manager_job = jobs[0].model_copy(update={"job_title": "Product Design Manager", "management_expectations": "People manager role with direct reports; manage a team of designers."})
    staff_job = jobs[0].model_copy(update={"job_title": "Staff Product Designer", "management_expectations": "Individual contributor role with cross-functional leadership."})
    manager = match_job(profile, evidence, prefs, manager_job)
    staff = match_job(profile, evidence, prefs, staff_job)
    assert manager.score_breakdown.ic_management_alignment < staff.score_breakdown.ic_management_alignment
    assert any("people-management" in c for c in manager.review_concerns)


def test_empty_requirements_are_not_perfect_coverage_or_auto_apply():
    profile, evidence, prefs, jobs = fixtures()
    prefs.review_threshold = 50
    prefs.auto_apply_threshold = 50
    prefs.evidence_confidence_threshold = 0
    job = jobs[0].model_copy(update={
        "explicit_requirements": [],
        "inferred_preferences": [],
        "required_technologies": [],
        "required_years_experience": None,
        "parsing_quality": "INSUFFICIENT",
    })
    analysis = match_job(profile, evidence, prefs, job)
    assert analysis.requirement_coverage_score == 0
    assert analysis.score_breakdown.weighted_requirement_possible == 0
    assert analysis.evidence_confidence_score == 0
    assert analysis.classification != Classification.AUTO_APPLY_ELIGIBLE
    assert "Insufficient parsed qualification data for confident automated evaluation." in analysis.review_concerns


def test_actual_evidence_matching_after_greenhouse_html_parsing():
    from job_agent.sources.greenhouse import parse_greenhouse_description
    profile, evidence, prefs, jobs = fixtures()
    html = """<p><strong>What You’ll Do:</strong></p><ul><li>Partner with engineering teams to design complex technical products.</li></ul><p><strong>Who You Are:</strong></p><ul><li>You have 10+ years of experience in digital product design.</li><li>Experience with developer platforms, observability, infrastructure, or technical domains.</li><li>Research experience and cross-functional engineering collaboration.</li></ul>"""
    parsed = parse_greenhouse_description(html)
    job = jobs[0].model_copy(update={
        "source_job_id": "datadog-style",
        "job_title": "Staff Product Designer, APM",
        "description": html,
        **parsed,
    })
    analysis = match_job(profile, evidence, prefs, job)
    assert analysis.requirement_evaluations
    assert analysis.evidence_confidence_score > 0
    assert any("10+ years" in e.requirement for e in analysis.requirement_evaluations)
    assert analysis.requirement_coverage_score < 100
    assert analysis.classification != Classification.AUTO_APPLY_ELIGIBLE


def test_encoded_greenhouse_html_matching_produces_evidence_traceability():
    from job_agent.sources.greenhouse import parse_greenhouse_description
    profile, evidence, prefs, jobs = fixtures()
    encoded = """&lt;p&gt;&lt;strong&gt;What You’ll Do:&lt;/strong&gt;&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Partner with engineers on design systems and component standardization.&lt;/li&gt;&lt;/ul&gt;&lt;p&gt;&lt;strong&gt;Who You Are:&lt;/strong&gt;&lt;/p&gt;&lt;ul&gt;&lt;li&gt;You have 3+ years of product design experience.&lt;/li&gt;&lt;li&gt;Hands-on Figma design systems experience.&lt;/li&gt;&lt;li&gt;Experience partnering with engineers on component standardization.&lt;/li&gt;&lt;/ul&gt;"""
    parsed = parse_greenhouse_description(encoded)
    job = jobs[0].model_copy(update={
        "source_job_id": "encoded-product-design",
        "job_title": "Product Designer, Design Systems",
        "description": encoded,
        **parsed,
    })
    analysis = match_job(profile, evidence, prefs, job)
    assert job.parsing_quality.value != "INSUFFICIENT"
    assert analysis.requirement_evaluations
    assert analysis.evidence_confidence_score > 0
    assert analysis.classification in {Classification.REVIEW_REQUIRED, Classification.AUTO_APPLY_ELIGIBLE, Classification.REJECT}
    assert any(e.matched_evidence_statement_ids for e in analysis.requirement_evaluations)
    assert any("caci_figma_admin" in e.matched_evidence_statement_ids or "caci_primeng_standardization" in e.matched_evidence_statement_ids for e in analysis.requirement_evaluations)


def test_full_configured_career_evidence_restored_and_validates():
    _, evidence, _, _ = fixtures()
    employers = {exp.employer for exp in evidence}
    assert employers == {"CACI International", "The Matchstick Group", "HDR", "Independent", "Little Highrise LLC"}
    assert len(evidence) == 5
    assert sum(len(exp.all_evidence()) for exp in evidence) == 28
    for exp in evidence:
        assert exp.allowed_claims
        assert exp.prohibited_claims
        assert exp.skills
        assert exp.technologies
        assert exp.responsibilities
        assert exp.accomplishments
        assert exp.industries
        assert exp.all_evidence()


def test_complete_configured_design_experience_exceeds_ten_years():
    _, evidence, _, _ = fixtures()
    years = professional_design_years(evidence, today=date(2026, 7, 16))
    assert 16 <= years <= 18
    evaluation = eval_req("You have 10+ years of experience in digital product design", category="experience_years")
    assert evaluation.status == RequirementMatchStatus.SUPPORTED


def test_evidence_index_contains_statement_ids_from_multiple_employers():
    _, evidence, _, _ = fixtures()
    records = build_evidence_index(evidence)
    statement_ids = {record.statement_id for record in records if record.statement_id}
    assert {record.experience_id for record in records} >= {
        "caci_senior_ui_ux", "matchstick_senior_ux_consultant", "hdr_senior_designer", "independent_creative_consultant", "little_highrise_creative_director"
    }
    assert {"caci_figma_admin", "matchstick_healthcare_mobile", "hdr_carolina_crossroads", "independent_client_delivery", "little_highrise_end_to_end"} <= statement_ids


def test_restored_evidence_increases_legitimate_traceability_across_records():
    profile, evidence, prefs, jobs = fixtures()
    job = jobs[0].model_copy(update={
        "source_job_id": "multi-record-design",
        "explicit_requirements": [
            JobRequirement(text="10+ years of product or UX design experience", requirement_type="explicit", category="experience_years", is_hard_requirement=True),
            JobRequirement(text="Experience with UX design for mobile and responsive interfaces", requirement_type="explicit", category="skill", is_hard_requirement=True),
        ],
        "required_years_experience": 10,
        "required_technologies": [],
        "preferred_technologies": [],
    })
    analysis = match_job(profile, evidence, prefs, job)
    matched_experiences = set().union(*(set(e.matched_experience_ids) for e in analysis.requirement_evaluations))
    matched_statements = set().union(*(set(e.matched_evidence_statement_ids) for e in analysis.requirement_evaluations))
    assert len(matched_experiences) > 1
    assert {"matchstick_healthcare_mobile", "hdr_responsive_digital", "little_highrise_end_to_end"} & matched_statements
    assert analysis.evidence_confidence_score > 0


def test_aegis_remains_rejected_with_restored_evidence():
    profile, evidence, prefs, jobs = fixtures()
    analysis = match_job(profile, evidence, prefs, jobs[1])
    assert analysis.classification == Classification.REJECT


def test_no_hard_requirements_absent_dimension_not_zero_penalty():
    profile, evidence, prefs, jobs = fixtures()
    job = jobs[0].model_copy(update={
        "source_job_id": "no-hard",
        "explicit_requirements": [
            JobRequirement(text="Experience partnering with engineers on component standardization", requirement_type="explicit", category="skill", is_hard_requirement=False),
            JobRequirement(text="Hands-on Figma design systems experience", requirement_type="explicit", category="skill", is_hard_requirement=False),
        ],
        "required_technologies": [],
        "required_years_experience": None,
    })
    analysis = match_job(profile, evidence, prefs, job)
    hard = analysis.score_breakdown.components["hard_requirement_coverage"]
    assert hard.applicability == "not_applicable"
    assert hard.effective_weight == 0
    assert "hard_requirement_coverage" in analysis.score_breakdown.absent_scoring_dimensions
    assert analysis.role_match_score >= prefs.review_threshold


def test_absent_dimension_effective_weights_sum_to_one():
    profile, evidence, prefs, jobs = fixtures()
    job = jobs[0].model_copy(update={"inferred_preferences": [], "preferred_technologies": [], "management_expectations": None, "industry": None, "domain_experience_expectations": []})
    analysis = match_job(profile, evidence, prefs, job)
    total = sum(c.effective_weight for c in analysis.score_breakdown.components.values())
    assert 0.99 <= total <= 1.01
    assert analysis.score_breakdown.components["preferred_qualification_alignment"].applicability == "not_applicable"
    assert analysis.score_breakdown.components["industry_domain_alignment"].applicability == "unknown"


def test_unknown_work_arrangement_differs_from_confirmed_mismatch():
    profile, evidence, prefs, jobs = fixtures()
    unknown = match_job(profile, evidence, prefs, jobs[0].model_copy(update={"remote_status": RemoteStatus.UNKNOWN}))
    onsite = match_job(profile, evidence, prefs, jobs[0].model_copy(update={"remote_status": RemoteStatus.ONSITE}))
    assert unknown.score_breakdown.components["work_arrangement_alignment"].applicability == "unknown"
    assert onsite.score_breakdown.components["work_arrangement_alignment"].applicability == "applicable"
    assert unknown.score_breakdown.work_arrangement_alignment > onsite.score_breakdown.work_arrangement_alignment


def test_visual_craft_uses_verified_visual_evidence_but_generic_design_does_not():
    visual = eval_req("Great attention to detail and a strong eye for visual craft, such as composition, typography, and layout.", category="skill", hard=False)
    generic = eval_req("Design work", category="skill", hard=False)
    assert visual.status in {RequirementMatchStatus.SUPPORTED, RequirementMatchStatus.PARTIALLY_SUPPORTED}
    assert visual.matched_evidence_statement_ids
    assert generic.status == RequirementMatchStatus.UNSUPPORTED


def test_user_research_distinctions_remain_conservative():
    formal = eval_req("Conducting formal user research studies and user interviews", category="skill", hard=False)
    discovery = eval_req("Stakeholder discovery and requirements discovery to inform design", category="skill", hard=False)
    assert formal.status in {RequirementMatchStatus.TRANSFERABLE, RequirementMatchStatus.PARTIALLY_SUPPORTED, RequirementMatchStatus.UNSUPPORTED}
    assert formal.status != RequirementMatchStatus.SUPPORTED
    assert discovery.status in {RequirementMatchStatus.SUPPORTED, RequirementMatchStatus.PARTIALLY_SUPPORTED}


def test_representative_live_score_patterns_no_hard_requirements():
    profile, evidence, prefs, jobs = fixtures()
    cases = [
        ("Staff Product Designer, APM", 100, 67, 64, 15, 65),
        ("Staff Product Designer, Metrics", 100, 70, 68, 14, 61),
        ("Product Designer, Design, Dev, & AI Tools", 81, 68, 60, 15, 58),
        ("Product Designer, Growth & Monetization", 81, 68, 58, 15, 58),
    ]
    for title, title_score, req, _conf, _risk, old in cases:
        values = {
            "title_alignment": title_score, "requirement_coverage": req, "hard_requirement_coverage": None,
            "preferred_qualification_alignment": None, "industry_domain_alignment": 50,
            "work_arrangement_alignment": 65, "ic_management_alignment": 90,
        }
        from job_agent.matching import _normalized_score_components
        new, comps, absent = _normalized_score_components(values, {"hard_requirement_coverage": "not_applicable", "preferred_qualification_alignment": "not_applicable", "industry_domain_alignment": "unknown", "work_arrangement_alignment": "unknown", "ic_management_alignment": "unknown"})
        assert new > old
        assert comps["hard_requirement_coverage"].effective_weight == 0
        assert "hard_requirement_coverage" in absent


def test_title_only_score_cannot_enter_review_queue():
    profile, evidence, prefs, jobs = fixtures()
    job = jobs[0].model_copy(update={"job_title": "Senior Product Designer", "explicit_requirements": [], "inferred_preferences": [], "required_technologies": [], "required_years_experience": None, "parsing_quality": "LOW"})
    analysis = match_job(profile, evidence, prefs, job)
    assert analysis.title_score >= 90
    assert analysis.role_match_score < prefs.review_threshold
    assert analysis.classification == Classification.REJECT
    assert analysis.score_breakdown.components["title_alignment"].applicability == "unknown"


def test_no_hard_requirements_valid_when_explicit_requirements_exist():
    profile, evidence, prefs, jobs = fixtures()
    job = jobs[0].model_copy(update={"explicit_requirements": [JobRequirement(text="Hands-on Figma design systems experience", requirement_type="explicit", category="skill", is_hard_requirement=False)], "required_technologies": [], "required_years_experience": None})
    analysis = match_job(profile, evidence, prefs, job)
    assert analysis.requirement_evaluations
    assert analysis.score_breakdown.components["hard_requirement_coverage"].applicability == "not_applicable"
    assert analysis.evidence_confidence_score > 0


def test_semantic_distinctions_reduce_false_positives():
    formal = eval_req("Conducting research, synthesizing insights, and influencing product strategy across cross-functional teams", category="skill", hard=False)
    assert formal.status != RequirementMatchStatus.SUPPORTED
    metrics = eval_req("Familiarity with experimentation, metrics, or using data to inform design decisions", category="skill", hard=False)
    assert "caci_tailwind_tokens" not in metrics.matched_evidence_statement_ids
    assert metrics.status in {RequirementMatchStatus.UNSUPPORTED, RequirementMatchStatus.TRANSFERABLE, RequirementMatchStatus.PARTIALLY_SUPPORTED}
    technical = eval_req("Fluency in data structures, system architecture, and distributed systems", category="skill", hard=False)
    assert "caci_figma_admin" not in technical.matched_evidence_statement_ids
    assert technical.status != RequirementMatchStatus.SUPPORTED
    generic = eval_req("Design decisions", category="skill", hard=False)
    assert generic.status == RequirementMatchStatus.UNSUPPORTED
    distinctive = eval_req("Component standardization with engineers", category="skill", hard=False)
    assert distinctive.status in {RequirementMatchStatus.SUPPORTED, RequirementMatchStatus.PARTIALLY_SUPPORTED}
