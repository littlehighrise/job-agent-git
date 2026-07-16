from __future__ import annotations

import json
from pathlib import Path

from job_agent.calibration import build_calibration_report, report_to_markdown
from job_agent.io import load_model_list
from job_agent.live_validation import SourceResult, create_preferences, parse_board_tokens, summarize, to_markdown
from job_agent.models import JobPosting, MatchAnalysis, ParsingQuality
from job_agent.persistence import ApplicationStore


def _persist_sample_jobs(tmp_path: Path) -> Path:
    db = tmp_path / "validation.sqlite3"
    store = ApplicationStore(db)
    jobs = load_model_list(Path("data/sample_jobs/jobs.json"), JobPosting)
    for index, job in enumerate(jobs):
        classification = "REVIEW_REQUIRED" if index == 0 else "REJECT"
        analysis = MatchAnalysis(
            job_id=job.source_job_id,
            role_match_score=90 - index,
            evidence_confidence_score=86 - index,
            application_risk_score=12 + index,
            classification=classification,
        )
        store.upsert(job, analysis, None)
    return db


def test_parse_board_tokens_dedupes_and_caps():
    assert parse_board_tokens(" figma, notion,figma,,discord ", max_boards=2) == ["figma", "notion"]


def test_create_preferences_generates_greenhouse_sources_without_overwriting_base(tmp_path):
    base = Path("config/search_preferences.json")
    out = tmp_path / "prefs.json"
    original = base.read_text()
    create_preferences(base, out, ["figma", "notion"], 120000, "preference")
    generated = json.loads(out.read_text())
    assert base.read_text() == original
    assert generated["minimum_compensation_usd"] == 120000
    assert generated["remote_preference_mode"] == "preference"
    assert generated["sources"] == [
        {"type": "greenhouse", "board_token": "figma", "timeout_seconds": 15.0},
        {"type": "greenhouse", "board_token": "notion", "timeout_seconds": 15.0},
    ]


def test_summary_aggregates_mixed_source_results_and_ranks_top_jobs(tmp_path):
    db = _persist_sample_jobs(tmp_path)
    source_results = tmp_path / "source-results.json"
    source_results.write_text(json.dumps([
        SourceResult("figma", True, jobs_fetched=2).as_dict(),
        SourceResult("missing", False, error_type="GreenhouseSourceError", error_message="SECRET_TOKEN should not leak").as_dict(),
    ]))
    summary = summarize(db, source_results, tmp_path / "summary.json", tmp_path / "summary.md")
    assert summary["configured_boards"] == 2
    assert len(summary["successful_boards"]) == 1
    assert len(summary["failed_boards"]) == 1
    assert summary["discovered"] == 2
    assert summary["new"] == 2
    assert summary["review_required"] == 1
    assert summary["rejected"] == 1
    assert summary["top_jobs"][0]["role_match_score"] >= summary["top_jobs"][1]["role_match_score"]
    assert summary["parsing_quality_counts"]["HIGH"] == 2
    assert summary["jobs_with_explicit_requirements"] == 2
    assert summary["jobs_with_responsibilities"] == 1
    assert summary["jobs_with_requirement_evaluations"] == 0
    assert "Zero jobs produced requirement evaluations." in summary["validation_warnings"]
    summary_md = (tmp_path / "summary.md").read_text()
    assert "Parsing and Evidence Health" in summary_md
    assert "SECRET_TOKEN" not in summary_md


def test_empty_summary_is_explicit(tmp_path):
    source_results = tmp_path / "source-results.json"
    source_results.write_text("[]")
    summary = summarize(tmp_path / "missing.sqlite3", source_results, tmp_path / "summary.json", tmp_path / "summary.md")
    assert summary["discovered"] == 0
    markdown = to_markdown(summary)
    assert "No jobs were discovered" in markdown


def test_calibration_report_from_validation_database(tmp_path):
    db = _persist_sample_jobs(tmp_path)
    report = build_calibration_report(db, top_n=1)
    assert report["artifact_available"] is True
    assert report["selected_count"] == 1
    assert report["score_distributions"]["role_match_score"]["count"] == 2
    markdown = report_to_markdown(report)
    assert "Job Matching Calibration Report" in markdown
    assert "Selected jobs" in markdown


def test_calibration_report_is_explicit_when_database_missing(tmp_path):
    report = build_calibration_report(tmp_path / "missing.sqlite3")
    assert report["artifact_available"] is False
    assert report["selected_count"] == 0
    assert "No validation database" in report_to_markdown(report)


def test_summary_warns_when_all_jobs_have_insufficient_parsing(tmp_path):
    db = tmp_path / "validation.sqlite3"
    store = ApplicationStore(db)
    job = load_model_list(Path("data/sample_jobs/jobs.json"), JobPosting)[0].model_copy(update={
        "source_job_id": "empty-live",
        "explicit_requirements": [],
        "responsibilities": [],
        "parsing_quality": ParsingQuality.INSUFFICIENT,
    })
    analysis = MatchAnalysis(job_id=job.source_job_id, role_match_score=10, evidence_confidence_score=0, application_risk_score=80, classification="REJECT")
    store.upsert(job, analysis, None)
    source_results = tmp_path / "source-results.json"
    source_results.write_text(json.dumps([SourceResult("datadog", True, jobs_fetched=1).as_dict()]))
    summary = summarize(db, source_results, tmp_path / "summary.json", tmp_path / "summary.md")
    assert summary["parsing_quality_counts"]["INSUFFICIENT"] == 1
    assert "All fetched jobs have INSUFFICIENT parsing quality." in summary["validation_warnings"]
    assert "Zero fetched jobs contain explicit requirements." in summary["validation_warnings"]
    assert "Zero jobs produced requirement evaluations." in summary["validation_warnings"]
