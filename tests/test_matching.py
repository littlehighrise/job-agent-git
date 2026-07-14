from pathlib import Path

from job_agent.io import load_model, load_model_list
from job_agent.matching import match_job
from job_agent.models import CandidateProfile, ExperienceEvidence, JobPosting, SearchPreferences, Classification


def fixtures():
    profile = load_model(Path("config/candidate_profile.json"), CandidateProfile)
    evidence = load_model_list(Path("config/career_evidence.json"), ExperienceEvidence)
    prefs = load_model(Path("config/search_preferences.json"), SearchPreferences)
    jobs = load_model_list(Path("data/sample_jobs/jobs.json"), JobPosting)
    return profile, evidence, prefs, jobs


def test_strong_design_system_job_reaches_review_or_auto_apply():
    profile, evidence, prefs, jobs = fixtures()
    analysis = match_job(profile, evidence, prefs, jobs[0])
    assert analysis.classification in {Classification.REVIEW_REQUIRED, Classification.AUTO_APPLY_ELIGIBLE}
    assert analysis.role_match_score >= prefs.review_threshold
    assert analysis.matched_requirements


def test_excluded_contradicted_defense_job_rejected():
    profile, evidence, prefs, jobs = fixtures()
    analysis = match_job(profile, evidence, prefs, jobs[1])
    assert analysis.classification == Classification.REJECT
    assert analysis.hard_constraint_violations
    assert analysis.contradicted_requirements
