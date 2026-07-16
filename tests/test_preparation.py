from __future__ import annotations

import json
from pathlib import Path
from typer.testing import CliRunner

from job_agent.audit import audit_resume
from job_agent.cli import app
from job_agent.io import load_model, load_model_list
from job_agent.matching import match_job
from job_agent.models import CandidateProfile, ExperienceEvidence, JobPosting
from job_agent.persistence import ApplicationStore
from job_agent.profile import validate_candidate_profile
from job_agent.resume.engine import build_resume_plan, build_structured_resume, render_resume_html
from job_agent.models import SearchPreferences

runner = CliRunner()


def fixtures():
    profile = load_model(Path("config/candidate_profile.json"), CandidateProfile)
    evidence = load_model_list(Path("config/career_evidence.json"), ExperienceEvidence)
    prefs = load_model(Path("config/search_preferences.json"), SearchPreferences)
    job = load_model_list(Path("data/sample_jobs/jobs.json"), JobPosting)[0]
    analysis = match_job(profile, evidence, prefs, job)
    return profile, evidence, job, analysis


def test_profile_validation_detects_placeholder_and_accepts_public_urls():
    p = CandidateProfile(candidate_id="x", full_name="Bob Elicker", email="bob@example.com", phone=None, portfolio_url="https://robertelicker.com", linkedin_url="https://www.linkedin.com/in/bob-elicker")
    findings = validate_candidate_profile(p)
    assert any(f.category == "placeholder_contact" for f in findings)
    assert not any(f.category == "invalid_profile_url" for f in findings)


def test_missing_contact_blocks_and_draft_status():
    profile, evidence, job, analysis = fixtures()
    resume = build_structured_resume(profile, evidence, job, build_resume_plan(job, analysis, evidence))
    audit = audit_resume(job, evidence, resume, profile)
    assert resume.status == "DRAFT_INCOMPLETE"
    assert not audit.passed
    assert any(f.category in {"missing_contact", "draft_incomplete"} for f in audit.findings)


def test_resume_has_dates_provenance_relevant_competencies_and_no_placeholder_framing():
    profile, evidence, job, analysis = fixtures()
    profile.email = "bob@private.test"; profile.phone = "555-555-1212"
    resume = build_structured_resume(profile, evidence, job, build_resume_plan(job, analysis, evidence))
    html = render_resume_html(resume)
    text = resume.model_dump_json() + html
    assert "Frame verified experience around" not in text
    assert all(exp.start_date and exp.end_date for exp in resume.experience)
    assert all(exp.bullet_provenance for exp in resume.experience)
    assert "RoboHelp" not in resume.competencies
    assert "WHM" not in resume.competencies
    assert len(resume.competencies) <= 16
    assert "Aug 2023" in html


def test_audit_fails_unsupported_bullet_metric_and_duplicate():
    profile, evidence, job, analysis = fixtures()
    profile.email = "bob@private.test"; profile.phone = "555-555-1212"
    resume = build_structured_resume(profile, evidence, job, build_resume_plan(job, analysis, evidence))
    resume.status = "READY_FOR_REVIEW"
    resume.experience[0].bullets.append("Increased revenue by 400% with unverified AI leadership.")
    resume.experience[0].bullets.append(resume.experience[0].bullets[0])
    audit = audit_resume(job, evidence, resume, profile)
    assert not audit.passed
    cats = {f.category for f in audit.findings}
    assert "unsupported_bullet" in cats
    assert "unsupported_metric" in cats
    assert "duplicate_bullet" in cats


def test_discover_does_not_generate_completed_resumes_and_prepare_requires_approval(tmp_path):
    prefs = tmp_path / "prefs.json"
    prefs.write_text(json.dumps({"target_titles":["Product Designer"],"sources":[{"type":"local_json","path":"data/sample_jobs/jobs.json"}],"polling_interval_minutes":60}))
    db = tmp_path / "jobs.sqlite3"; out = tmp_path / "applications"
    result = runner.invoke(app, ["discover", "--preferences", str(prefs), "--db", str(db), "--output", str(out)])
    assert result.exit_code == 0
    assert list(out.glob("**/job.json"))
    assert not list(out.glob("**/resume.json"))
    job_id = load_model_list(Path("data/sample_jobs/jobs.json"), JobPosting)[0].source_job_id
    blocked = runner.invoke(app, ["prepare", job_id, "--db", str(db), "--output", str(out)])
    assert blocked.exit_code != 0
    store = ApplicationStore(db); assert store.record_decision(job_id, "approved")
    ok = runner.invoke(app, ["prepare", job_id, "--db", str(db), "--profile", "config/candidate_profile.json", "--output", str(out)])
    assert ok.exit_code == 0
    assert list(out.glob("**/resume-provenance.json"))
    assert list(out.glob("**/application-brief.md"))


def test_live_workflow_upload_excludes_resume_artifacts():
    workflow = Path(".github/workflows/live-greenhouse-validation.yml").read_text()
    upload = workflow.split("Upload validation artifacts", 1)[1]
    assert "resume.json" not in upload
    assert "resume.html" not in upload
    assert "resume-provenance.json" not in upload
