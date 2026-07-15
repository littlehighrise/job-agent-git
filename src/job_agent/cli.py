from __future__ import annotations

import json
from pathlib import Path
import typer

from job_agent.audit import audit_resume
from job_agent.io import load_model, load_model_list, write_json
from job_agent.live_validation import SourceResult
from job_agent.matching import match_job
from job_agent.models import CandidateProfile, Classification, ExperienceEvidence, MatchAnalysis, SearchPreferences
from job_agent.persistence import ApplicationStore
from job_agent.resume.engine import build_resume_plan, build_structured_resume, render_resume_html
from job_agent.sources import ADAPTERS
from job_agent.sources.greenhouse import GreenhouseSourceError

app = typer.Typer(help="Local-first job search and application preparation agent.")


def _slug(employer: str, source_job_id: str) -> str:
    return f"{employer}_{source_job_id}".lower().replace(" ", "-").replace("/", "-")


def _load(profile: Path, evidence: Path, preferences: Path):
    return load_model(profile, CandidateProfile), load_model_list(evidence, ExperienceEvidence), load_model(preferences, SearchPreferences)


def _write_artifacts(output: Path, job, analysis: MatchAnalysis, candidate, evidence_items) -> Path:
    package_dir = output / _slug(job.employer, job.source_job_id)
    analysis_path = package_dir / "analysis.json"
    if analysis_path.exists():
        return package_dir
    write_json(package_dir / "job.json", job)
    write_json(analysis_path, analysis)
    if analysis.classification != Classification.REJECT:
        plan = build_resume_plan(job, analysis)
        resume = build_structured_resume(candidate, evidence_items, job, plan)
        audit = audit_resume(job, evidence_items, resume)
        write_json(package_dir / "resume_plan.json", plan)
        write_json(package_dir / "resume.json", resume)
        write_json(package_dir / "audit.json", audit)
        (package_dir / "resume.html").write_text(render_resume_html(resume))
    return package_dir


@app.command()
def discover(
    profile: Path = Path("config/candidate_profile.json"),
    evidence: Path = Path("config/career_evidence.json"),
    preferences: Path = Path("config/search_preferences.json"),
    db: Path = Path("job_agent.sqlite3"),
    output: Path = Path("applications"),
    source_results: Path | None = None,
):
    """Fetch configured sources, match new jobs, persist history, and create local review artifacts."""
    candidate, evidence_items, prefs = _load(profile, evidence, preferences)
    store = ApplicationStore(db)
    rows = []
    discovered = new = rejected = review = auto = failed = 0
    seen_keys: set[tuple[str, str]] = set()
    source_result_rows = []
    for source in prefs.sources:
        adapter_type = source.get("type")
        board_token = str(source.get("board_token") or source.get("path") or adapter_type or "unknown")
        try:
            adapter = ADAPTERS[adapter_type](**{k: v for k, v in source.items() if k != "type"})
            jobs = adapter.fetch_jobs()
            source_result_rows.append(SourceResult(board_token=board_token, success=True, jobs_fetched=len(jobs)).as_dict())
        except (KeyError, GreenhouseSourceError, OSError, ValueError) as exc:
            failed += 1
            source_result_rows.append(SourceResult(board_token=board_token, success=False, error_type=exc.__class__.__name__, error_message=str(exc)).as_dict())
            typer.echo(f"Source failed ({adapter_type} {board_token}): {exc}")
            continue
        for job in jobs:
            key = (job.source, job.source_job_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            discovered += 1
            already_seen = store.has_seen(job)
            if not already_seen:
                new += 1
            analysis = match_job(candidate, evidence_items, prefs, job, already_applied=False)
            package_dir = None
            if not already_seen or analysis.classification != Classification.REJECT:
                package_dir = _write_artifacts(output, job, analysis, candidate, evidence_items)
            store.upsert(job, analysis, str(package_dir) if package_dir else None)
            if analysis.classification == Classification.REJECT:
                rejected += 1
            elif analysis.classification == Classification.REVIEW_REQUIRED:
                review += 1
            elif analysis.classification == Classification.AUTO_APPLY_ELIGIBLE:
                auto += 1
            rows.append({"employer": job.employer, "title": job.job_title, "classification": analysis.classification.value, "role_score": analysis.role_match_score, "evidence_confidence": analysis.evidence_confidence_score, "risk": analysis.application_risk_score, "already_seen": already_seen, "package": str(package_dir) if package_dir else None})
    write_json(output / "review_queue.json", rows)
    if source_results is not None:
        write_json(source_results, source_result_rows)
    typer.echo(f"Discovered: {discovered}")
    typer.echo(f"New: {new}")
    typer.echo(f"Rejected: {rejected}")
    typer.echo(f"Review required: {review}")
    typer.echo(f"Auto-apply eligible: {auto}")
    if failed:
        typer.echo(f"Failed sources: {failed}")


@app.command()
def run(profile: Path = Path("config/candidate_profile.json"), evidence: Path = Path("config/career_evidence.json"), preferences: Path = Path("config/search_preferences.json"), db: Path = Path("job_agent.sqlite3"), output: Path = Path("applications"), source_results: Path | None = None):
    """Backward-compatible alias for discover."""
    discover(profile, evidence, preferences, db, output, source_results)


@app.command("queue")
def queue_cmd(db: Path = Path("job_agent.sqlite3")):
    """Display jobs awaiting human review."""
    rows = ApplicationStore(db).queue()
    if not rows:
        typer.echo("No jobs awaiting review.")
        return
    for row in rows:
        analysis = MatchAnalysis.model_validate_json(row["analysis_json"])
        supported = [e.requirement for e in analysis.requirement_evaluations if e.status == "SUPPORTED"][:3]
        unsupported = analysis.unsupported_requirements[:3]
        typer.echo(f"[{row['source_job_id']}] {row['employer']} — {row['job_title']} ({row.get('location') or 'location unknown'})")
        typer.echo(f"  classification={row['classification']} role={row['match_score']} evidence={row['evidence_confidence']} risk={row['risk_score']}")
        if supported: typer.echo(f"  supported: {'; '.join(supported)}")
        if unsupported: typer.echo(f"  unsupported: {'; '.join(unsupported)}")
        if analysis.contradicted_requirements: typer.echo(f"  contradictions: {'; '.join(analysis.contradicted_requirements[:3])}")
        if analysis.auto_apply_blockers: typer.echo(f"  auto-apply blockers: {'; '.join(analysis.auto_apply_blockers[:3])}")
        if analysis.review_concerns: typer.echo(f"  review concerns: {'; '.join(analysis.review_concerns[:3])}")


@app.command()
def show(job_id: str, db: Path = Path("job_agent.sqlite3")):
    """Show a detailed deterministic review for one persisted job."""
    row = ApplicationStore(db).get(job_id)
    if not row:
        raise typer.Exit(f"Job not found: {job_id}")
    analysis = MatchAnalysis.model_validate_json(row["analysis_json"])
    job = json.loads(row["job_json"])
    typer.echo(f"{row['employer']} — {row['job_title']}")
    typer.echo(f"Job ID: {row['source_job_id']}  Source: {row['source']}")
    typer.echo(f"Location: {row.get('location') or 'unknown'}  Remote: {job.get('remote_status', 'unknown')}")
    typer.echo(f"Application URL: {row['application_url']}")
    typer.echo(f"Classification: {row['classification']} | role={row['match_score']} evidence={row['evidence_confidence']} risk={row['risk_score']}")
    if analysis.score_breakdown:
        typer.echo(f"Score formula: {analysis.score_breakdown.formula}")
    typer.echo("Requirements:")
    for e in analysis.requirement_evaluations:
        typer.echo(f"- {e.status.value} ({e.confidence}%, hard={e.is_hard_requirement}) {e.requirement}")
        typer.echo(f"  evidence statements={e.matched_evidence_statement_ids} experiences={e.matched_experience_ids}")
        typer.echo(f"  {e.explanation}")
    if analysis.hard_constraint_violations: typer.echo(f"Blockers: {'; '.join(analysis.hard_constraint_violations)}")
    if analysis.review_concerns: typer.echo(f"Review concerns: {'; '.join(analysis.review_concerns)}")
    if analysis.final_classification_rationale: typer.echo(f"Rationale: {'; '.join(analysis.final_classification_rationale)}")
    typer.echo(f"Artifacts: {row.get('artifact_dir') or 'none'}")


@app.command()
def approve(job_id: str, notes: str | None = None, db: Path = Path("job_agent.sqlite3")):
    """Record human approval without submitting an application."""
    if not ApplicationStore(db).record_decision(job_id, "approved", notes):
        raise typer.Exit(f"Job not found: {job_id}")
    typer.echo(f"Approved {job_id}. No application was submitted.")


@app.command()
def reject(job_id: str, notes: str | None = None, db: Path = Path("job_agent.sqlite3")):
    """Record human rejection so the job leaves the review queue."""
    if not ApplicationStore(db).record_decision(job_id, "rejected", notes):
        raise typer.Exit(f"Job not found: {job_id}")
    typer.echo(f"Rejected {job_id}.")


if __name__ == "__main__":
    app()
