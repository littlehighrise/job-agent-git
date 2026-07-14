from __future__ import annotations

from pathlib import Path
import typer

from job_agent.io import load_model, load_model_list, write_json
from job_agent.matching import match_job
from job_agent.models import CandidateProfile, Classification, ExperienceEvidence, SearchPreferences
from job_agent.persistence import ApplicationStore
from job_agent.resume.engine import build_resume_plan, build_structured_resume, render_resume_html
from job_agent.audit import audit_resume
from job_agent.sources import ADAPTERS

app = typer.Typer(help="Local-first job search and application preparation agent.")

@app.command()
def run(
    profile: Path = Path("config/candidate_profile.json"),
    evidence: Path = Path("config/career_evidence.json"),
    preferences: Path = Path("config/search_preferences.json"),
    db: Path = Path("job_agent.sqlite3"),
    output: Path = Path("applications"),
):
    """Discover, score, and prepare review packages for configured job sources."""
    candidate = load_model(profile, CandidateProfile)
    evidence_items = load_model_list(evidence, ExperienceEvidence)
    prefs = load_model(preferences, SearchPreferences)
    store = ApplicationStore(db)
    review_rows = []
    for source in prefs.sources:
        adapter_type = source["type"]
        adapter = ADAPTERS[adapter_type](**{k: v for k, v in source.items() if k != "type"})
        for job in adapter.fetch_jobs():
            already_seen = store.has_seen(job)
            analysis = match_job(candidate, evidence_items, prefs, job, already_applied=False)
            store.upsert(job, analysis)
            slug = f"{job.employer}_{job.source_job_id}".lower().replace(" ", "-").replace("/", "-")
            package_dir = output / slug
            write_json(package_dir / "job.json", job)
            write_json(package_dir / "analysis.json", analysis)
            if analysis.classification != Classification.REJECT:
                plan = build_resume_plan(job, analysis)
                resume = build_structured_resume(candidate, evidence_items, job, plan)
                audit = audit_resume(job, evidence_items, resume)
                write_json(package_dir / "resume_plan.json", plan)
                write_json(package_dir / "resume.json", resume)
                write_json(package_dir / "audit.json", audit)
                (package_dir / "resume.html").write_text(render_resume_html(resume))
            review_rows.append({
                "employer": job.employer,
                "title": job.job_title,
                "classification": analysis.classification.value,
                "role_score": analysis.role_match_score,
                "evidence_confidence": analysis.evidence_confidence_score,
                "risk": analysis.application_risk_score,
                "already_seen": already_seen,
                "package": str(package_dir),
            })
    write_json(output / "review_queue.json", review_rows)
    typer.echo(f"Processed {len(review_rows)} jobs. Review queue: {output / 'review_queue.json'}")

if __name__ == "__main__":
    app()
