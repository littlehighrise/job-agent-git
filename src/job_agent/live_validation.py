from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from job_agent.io import write_json
from job_agent.models import Classification

SECRET_KEYS = ("token", "secret", "key", "password", "authorization")


@dataclass(frozen=True)
class SourceResult:
    board_token: str
    success: bool
    jobs_fetched: int = 0
    error_type: str | None = None
    error_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"board_token": self.board_token, "success": self.success, "jobs_fetched": self.jobs_fetched}
        if self.error_type:
            data["error_type"] = self.error_type
        if self.error_message:
            data["error_message"] = _sanitize(self.error_message)
        return data


def _sanitize(value: str) -> str:
    cleaned = value.replace("\n", " ").strip()
    for key in SECRET_KEYS:
        cleaned = cleaned.replace(key.upper(), "[redacted]").replace(key, "[redacted]")
    return cleaned[:240]


def parse_board_tokens(raw: str, max_boards: int | None = None) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for item in raw.split(","):
        token = item.strip()
        if not token or token in seen:
            continue
        tokens.append(token)
        seen.add(token)
        if max_boards and len(tokens) >= max_boards:
            break
    return tokens


def create_preferences(base_path: Path, output_path: Path, board_tokens: list[str], minimum_compensation_usd: int, remote_preference_mode: str) -> None:
    prefs = json.loads(base_path.read_text())
    prefs["minimum_compensation_usd"] = minimum_compensation_usd
    prefs["remote_preference_mode"] = remote_preference_mode
    prefs["sources"] = [{"type": "greenhouse", "board_token": token, "timeout_seconds": 15.0} for token in board_tokens]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(prefs, indent=2, sort_keys=True) + "\n")


def load_source_results(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text())


def summarize(db_path: Path, source_results_path: Path, output_json: Path, output_markdown: Path, top_limit: int = 10) -> dict[str, Any]:
    source_results = load_source_results(source_results_path)
    rows = _application_rows(db_path)
    counts = {c.value: 0 for c in Classification}
    for row in rows:
        counts[row.get("classification") or ""] = counts.get(row.get("classification") or "", 0) + 1

    top_jobs = [_top_job(row) for row in _rank_user_facing_top_jobs(rows)[:top_limit]]
    highest_scoring_rejected_jobs = [_top_job(row) for row in sorted([r for r in rows if r.get("classification") == Classification.REJECT.value], key=lambda r: (r.get("match_score") or 0, r.get("evidence_confidence") or 0), reverse=True)[:top_limit]]
    parsing_counts = _parsing_counts(rows)
    validation_warnings = _validation_warnings(len(rows), parsing_counts)
    summary = {
        "configured_boards": len(source_results),
        "successful_boards": [r for r in source_results if r.get("success")],
        "failed_boards": [r for r in source_results if not r.get("success")],
        "discovered": len(rows),
        "new": len(rows),
        "rejected": counts.get(Classification.REJECT.value, 0),
        "review_required": counts.get(Classification.REVIEW_REQUIRED.value, 0),
        "auto_apply_eligible": counts.get(Classification.AUTO_APPLY_ELIGIBLE.value, 0),
        "parsing_quality_counts": parsing_counts["parsing_quality_counts"],
        "jobs_with_explicit_requirements": parsing_counts["jobs_with_explicit_requirements"],
        "jobs_with_responsibilities": parsing_counts["jobs_with_responsibilities"],
        "jobs_with_requirement_evaluations": parsing_counts["jobs_with_requirement_evaluations"],
        "jobs_with_nonzero_evidence_confidence": parsing_counts["jobs_with_nonzero_evidence_confidence"],
        "jobs_with_zero_hard_requirements": parsing_counts["jobs_with_zero_hard_requirements"],
        "jobs_with_one_or_more_hard_requirements": parsing_counts["jobs_with_one_or_more_hard_requirements"],
        "jobs_with_absent_scoring_dimensions": parsing_counts["jobs_with_absent_scoring_dimensions"],
        "jobs_rejected_solely_below_review_threshold": parsing_counts["jobs_rejected_solely_below_review_threshold"],
        "jobs_within_5_points_of_review_threshold": parsing_counts["jobs_within_5_points_of_review_threshold"],
        "validation_warnings": validation_warnings,
        "top_jobs": top_jobs,
        "highest_scoring_rejected_jobs": highest_scoring_rejected_jobs,
    }
    write_json(output_json, summary)
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.write_text(to_markdown(summary))
    return summary


def _application_rows(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM applications").fetchall()]
    finally:
        conn.close()



def _is_target_role_family(row: dict[str, Any]) -> bool:
    title = str(row.get("job_title") or "").lower()
    positive = any(token in title for token in ["product designer", "ux designer", "ui/ux", "design systems", "visual designer", "staff designer", "principal designer", "lead designer", "senior designer"])
    negative = any(token in title for token in ["product manager", "marketing", "software engineer", "people partner", "content manager", "sales", "recruiter"])
    return positive and not negative

def _rank_user_facing_top_jobs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(row: dict[str, Any]) -> tuple[int, int, int]:
        classification = row.get("classification")
        if classification == Classification.REVIEW_REQUIRED.value:
            bucket = 3
        elif classification == Classification.AUTO_APPLY_ELIGIBLE.value:
            bucket = 2
        elif _is_target_role_family(row):
            bucket = 1
        else:
            bucket = 0
        return (bucket, row.get("match_score") or 0, row.get("evidence_confidence") or 0)
    return sorted(rows, key=key, reverse=True)

def _parsing_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    quality_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "INSUFFICIENT": 0}
    explicit = responsibilities = evaluations = nonzero_confidence = 0
    zero_hard = one_or_more_hard = absent_dimensions = rejected_score_only = near_review = 0
    for row in rows:
        job = json.loads(row.get("job_json") or "{}")
        analysis = json.loads(row.get("analysis_json") or "{}")
        quality = str(job.get("parsing_quality") or "").upper()
        if quality in quality_counts:
            quality_counts[quality] += 1
        explicit_reqs = job.get("explicit_requirements") or []
        if explicit_reqs:
            explicit += 1
        if any(req.get("is_hard_requirement") for req in explicit_reqs if isinstance(req, dict)):
            one_or_more_hard += 1
        else:
            zero_hard += 1
        if job.get("responsibilities"):
            responsibilities += 1
        if analysis.get("requirement_evaluations"):
            evaluations += 1
        if (row.get("evidence_confidence") or 0) > 0 or (analysis.get("evidence_confidence_score") or 0) > 0:
            nonzero_confidence += 1
        breakdown = analysis.get("score_breakdown") or {}
        if breakdown.get("absent_scoring_dimensions"):
            absent_dimensions += 1
        rationale = " ".join(analysis.get("final_classification_rationale") or analysis.get("rationale") or [])
        if row.get("classification") == Classification.REJECT.value and "below configured review threshold" in rationale and not analysis.get("auto_apply_blockers") and not analysis.get("hard_constraint_violations"):
            rejected_score_only += 1
        review_threshold = 75
        score = row.get("match_score") or analysis.get("role_match_score") or 0
        if review_threshold - 5 <= score < review_threshold:
            near_review += 1
    return {
        "parsing_quality_counts": quality_counts,
        "jobs_with_explicit_requirements": explicit,
        "jobs_with_responsibilities": responsibilities,
        "jobs_with_requirement_evaluations": evaluations,
        "jobs_with_nonzero_evidence_confidence": nonzero_confidence,
        "jobs_with_zero_hard_requirements": zero_hard,
        "jobs_with_one_or_more_hard_requirements": one_or_more_hard,
        "jobs_with_absent_scoring_dimensions": absent_dimensions,
        "jobs_rejected_solely_below_review_threshold": rejected_score_only,
        "jobs_within_5_points_of_review_threshold": near_review,
    }


def _validation_warnings(discovered: int, counts: dict[str, Any]) -> list[str]:
    if discovered == 0:
        return []
    warnings: list[str] = []
    quality_counts = counts["parsing_quality_counts"]
    if quality_counts.get("INSUFFICIENT", 0) == discovered:
        warnings.append("All fetched jobs have INSUFFICIENT parsing quality.")
    if counts["jobs_with_explicit_requirements"] == 0:
        warnings.append("Zero fetched jobs contain explicit requirements.")
    if counts["jobs_with_requirement_evaluations"] == 0:
        warnings.append("Zero jobs produced requirement evaluations.")
    return warnings


def _top_job(row: dict[str, Any]) -> dict[str, Any]:
    job = json.loads(row.get("job_json") or "{}")
    return {
        "employer": row.get("employer"),
        "job_title": row.get("job_title"),
        "location": row.get("location"),
        "remote_status": job.get("remote_status", "unknown"),
        "role_match_score": row.get("match_score"),
        "evidence_confidence_score": row.get("evidence_confidence"),
        "application_risk_score": row.get("risk_score"),
        "classification": row.get("classification"),
        "application_url": row.get("application_url"),
    }


def to_markdown(summary: dict[str, Any]) -> str:
    failed = summary["failed_boards"]
    successful = summary["successful_boards"]
    result = "Application completed; inspect source warnings." if failed else "Application completed with all configured sources reachable."
    if summary["discovered"] == 0:
        result += " No jobs were discovered, so this is not proof that matching produced live recommendations."
    lines = ["# Live Greenhouse Validation", "", "## Workflow Result", "", result, "", "## Source Results", ""]
    lines.append(f"- Configured boards: {summary['configured_boards']}")
    lines.append(f"- Successful boards: {len(successful)}")
    lines.append(f"- Failed boards: {len(failed)}")
    for item in successful:
        lines.append(f"- ✅ `{item['board_token']}` fetched {item.get('jobs_fetched', 0)} jobs")
    for item in failed:
        msg = item.get("error_message") or item.get("error_type") or "unknown error"
        lines.append(f"- ⚠️ `{item['board_token']}` failed: {_sanitize(msg)}")
    lines.extend(["", "## Discovery Counts", "", f"- Jobs discovered: {summary['discovered']}", f"- New jobs: {summary['new']}", f"- Rejected jobs: {summary['rejected']}", f"- REVIEW_REQUIRED jobs: {summary['review_required']}", f"- AUTO_APPLY_ELIGIBLE jobs: {summary['auto_apply_eligible']}"])
    quality = summary.get("parsing_quality_counts", {})
    lines.extend(["", "## Parsing and Evidence Health", "", f"- HIGH parsing quality: {quality.get('HIGH', 0)}", f"- MEDIUM parsing quality: {quality.get('MEDIUM', 0)}", f"- LOW parsing quality: {quality.get('LOW', 0)}", f"- INSUFFICIENT parsing quality: {quality.get('INSUFFICIENT', 0)}", f"- Jobs with explicit requirements: {summary.get('jobs_with_explicit_requirements', 0)}", f"- Jobs with responsibilities: {summary.get('jobs_with_responsibilities', 0)}", f"- Jobs with requirement evaluations: {summary.get('jobs_with_requirement_evaluations', 0)}", f"- Jobs with non-zero evidence confidence: {summary.get('jobs_with_nonzero_evidence_confidence', 0)}", f"- Jobs with zero hard requirements: {summary.get('jobs_with_zero_hard_requirements', 0)}", f"- Jobs with one or more hard requirements: {summary.get('jobs_with_one_or_more_hard_requirements', 0)}", f"- Jobs with absent scoring dimensions: {summary.get('jobs_with_absent_scoring_dimensions', 0)}", f"- Jobs rejected solely below review threshold: {summary.get('jobs_rejected_solely_below_review_threshold', 0)}", f"- Jobs within 5 points of review threshold: {summary.get('jobs_within_5_points_of_review_threshold', 0)}"])
    for warning in summary.get("validation_warnings", []):
        lines.append(f"- ⚠️ {warning}")
    lines.extend(["", "## Top Matching Jobs", ""])
    if not summary["top_jobs"]:
        lines.append("No jobs were discovered. Check source failures and board tokens before interpreting matcher coverage.")
    else:
        lines.append("| Employer | Title | Location | Remote | Role | Evidence | Risk | Classification | URL |")
        lines.append("| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |")
        for job in summary["top_jobs"]:
            lines.append("| " + " | ".join(str(job.get(k) or "") for k in ["employer", "job_title", "location", "remote_status", "role_match_score", "evidence_confidence_score", "application_risk_score", "classification", "application_url"]) + " |")
    lines.extend(["", "## Highest-scoring Rejected Jobs", ""])
    if summary.get("highest_scoring_rejected_jobs"):
        lines.append("| Employer | Title | Location | Remote | Role | Evidence | Risk | Classification | URL |")
        lines.append("| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |")
        for job in summary["highest_scoring_rejected_jobs"]:
            lines.append("| " + " | ".join(str(job.get(k) or "") for k in ["employer", "job_title", "location", "remote_status", "role_match_score", "evidence_confidence_score", "application_risk_score", "classification", "application_url"]) + " |")
    else:
        lines.append("No rejected jobs were persisted.")
    lines.extend(["", "## Failures / Warnings", "", "Individual board failures are warnings unless the CLI, database, configuration, or test suite fails.", "", "## Artifact Contents", "", "- live-validation-summary.json", "- source-results.json", "- validation.sqlite3", "- applications/ review artifacts", "- discovery.log"])
    return "\n".join(lines) + "\n"


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create-config")
    c.add_argument("--base", type=Path, required=True)
    c.add_argument("--output", type=Path, required=True)
    c.add_argument("--board-tokens", required=True)
    c.add_argument("--minimum-compensation-usd", type=int, required=True)
    c.add_argument("--remote-preference-mode", required=True)
    c.add_argument("--max-boards", type=int)
    r = sub.add_parser("summarize")
    r.add_argument("--db", type=Path, required=True)
    r.add_argument("--source-results", type=Path, required=True)
    r.add_argument("--output-json", type=Path, required=True)
    r.add_argument("--output-markdown", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "create-config":
        tokens = parse_board_tokens(args.board_tokens, args.max_boards)
        create_preferences(args.base, args.output, tokens, args.minimum_compensation_usd, args.remote_preference_mode)
    else:
        summarize(args.db, args.source_results, args.output_json, args.output_markdown)


if __name__ == "__main__":
    main()
