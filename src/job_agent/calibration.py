from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from job_agent.models import MatchAnalysis, RequirementMatchStatus


def _bucket(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    return {"count": len(values), "min": min(values), "max": max(values), "average": round(mean(values), 1), "exact_80": values.count(80)}


def load_application_rows(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM applications").fetchall()]
    finally:
        conn.close()


def build_calibration_report(db_path: Path, top_n: int = 20) -> dict[str, Any]:
    rows = load_application_rows(db_path)
    sorted_rows = sorted(rows, key=lambda r: (r.get("match_score") or 0, r.get("evidence_confidence") or 0), reverse=True)
    selected = []
    for row in sorted_rows[:top_n]:
        analysis = MatchAnalysis.model_validate_json(row["analysis_json"])
        job = json.loads(row.get("job_json") or "{}")
        evals = analysis.requirement_evaluations
        selected.append({
            "employer": row.get("employer"),
            "title": row.get("job_title"),
            "source_job_id": row.get("source_job_id"),
            "application_url": row.get("application_url"),
            "role_match_score": row.get("match_score"),
            "evidence_confidence_score": row.get("evidence_confidence"),
            "application_risk_score": row.get("risk_score"),
            "classification": row.get("classification"),
            "top_supported_requirements": [e.requirement for e in evals if e.status == RequirementMatchStatus.SUPPORTED][:5],
            "unsupported_requirements": [e.requirement for e in evals if e.status == RequirementMatchStatus.UNSUPPORTED],
            "contradicted_requirements": [e.requirement for e in evals if e.status == RequirementMatchStatus.CONTRADICTED],
            "transferable_requirements": [e.requirement for e in evals if e.status == RequirementMatchStatus.TRANSFERABLE],
            "hard_requirements": [e.requirement for e in evals if e.is_hard_requirement],
            "auto_apply_blockers": analysis.auto_apply_blockers,
            "review_concerns": analysis.review_concerns,
            "score_breakdown": analysis.score_breakdown.model_dump() if analysis.score_breakdown else None,
            "calibration_judgment": "NEEDS_HUMAN_REVIEW",
            "recommended_action": "Inspect the normalized job, requirement evaluations, and cited candidate evidence before changing scoring.",
            "job_snapshot": {
                "location": job.get("location"),
                "remote_status": job.get("remote_status"),
                "responsibilities": job.get("responsibilities", []),
                "explicit_requirements": job.get("explicit_requirements", []),
                "inferred_preferences": job.get("inferred_preferences", []),
            },
        })
    role = [r.get("match_score") for r in rows if r.get("match_score") is not None]
    conf = [r.get("evidence_confidence") for r in rows if r.get("evidence_confidence") is not None]
    risk = [r.get("risk_score") for r in rows if r.get("risk_score") is not None]
    return {
        "input_database": str(db_path),
        "artifact_available": db_path.exists(),
        "jobs_available": len(rows),
        "selected_count": len(selected),
        "classification_counts": dict(Counter(r.get("classification") for r in rows)),
        "score_distributions": {"role_match_score": _bucket(role), "evidence_confidence_score": _bucket(conf), "application_risk_score": _bucket(risk)},
        "selected_jobs": selected,
    }


def report_to_markdown(report: dict[str, Any]) -> str:
    lines = ["# Job Matching Calibration Report", ""]
    if not report["artifact_available"]:
        lines.append("No validation database was found at the requested path; no live jobs were reviewed.")
        return "\n".join(lines) + "\n"
    lines += [f"- Jobs available: {report['jobs_available']}", f"- Selected jobs: {report['selected_count']}", f"- Classification counts: `{report['classification_counts']}`", f"- Score distributions: `{report['score_distributions']}`", "", "## Selected jobs", ""]
    for job in report["selected_jobs"]:
        lines += [f"### {job['employer']} — {job['title']}", f"- Scores: role={job['role_match_score']} evidence={job['evidence_confidence_score']} risk={job['application_risk_score']} classification={job['classification']}", f"- Supported: {job['top_supported_requirements']}", f"- Unsupported: {job['unsupported_requirements']}", f"- Transferable: {job['transferable_requirements']}", f"- Blockers: {job['auto_apply_blockers']}", f"- Review concerns: {job['review_concerns']}", f"- Calibration judgment: {job['calibration_judgment']}", ""]
    return "\n".join(lines)
