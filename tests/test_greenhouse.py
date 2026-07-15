from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from job_agent.cli import app
from job_agent.models import RemoteStatus
from job_agent.sources.greenhouse import GreenhouseSourceAdapter, parse_greenhouse_description


def payload(jobs):
    return {"jobs": jobs}


class FakeResponse:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.data).encode()


def test_greenhouse_normalization(monkeypatch):
    def fake_urlopen(req, timeout):
        return FakeResponse(payload([{
            "id": 123, "title": "Senior Product Designer - Remote", "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
            "location": {"name": "Remote - US"}, "updated_at": "2026-07-01T00:00:00Z",
            "content": "<h2>What you'll do</h2><ul><li>Partner with engineers</li></ul><h2>Minimum qualifications</h2><ul><li>Must have 5+ years of Figma experience</li></ul><h2>Preferred qualifications</h2><ul><li>Bonus: Angular</li></ul>"
        }]))
    monkeypatch.setattr("job_agent.sources.greenhouse.urlopen", fake_urlopen)
    jobs = GreenhouseSourceAdapter("acme", company="Acme").fetch_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "greenhouse"
    assert job.ats_type == "greenhouse"
    assert job.source_job_id == "123"
    assert job.employer == "Acme"
    assert job.application_url.endswith("/123")
    assert job.remote_status == RemoteStatus.REMOTE
    assert job.required_years_experience == 5
    assert "Figma" in job.required_technologies
    assert "Angular" in job.preferred_technologies


def test_multiple_greenhouse_boards(monkeypatch):
    called = []
    def fake_urlopen(req, timeout):
        called.append(req.full_url)
        token = req.full_url.split('/boards/')[1].split('/')[0]
        return FakeResponse(payload([{"id": token, "title": "Designer", "absolute_url": f"https://x/{token}", "location": {}, "content": ""}]))
    monkeypatch.setattr("job_agent.sources.greenhouse.urlopen", fake_urlopen)
    assert GreenhouseSourceAdapter("one").fetch_jobs()[0].source_job_id == "one"
    assert GreenhouseSourceAdapter("two").fetch_jobs()[0].source_job_id == "two"
    assert len(called) == 2


def test_malformed_and_missing_location(monkeypatch):
    monkeypatch.setattr("job_agent.sources.greenhouse.urlopen", lambda req, timeout: FakeResponse(payload([{"bad": True}, {"id": 1, "title": "Designer", "absolute_url": "https://x/1", "content": ""}])))
    job = GreenhouseSourceAdapter("x").fetch_jobs()[0]
    assert job.location is None
    assert job.remote_status == RemoteStatus.UNKNOWN


def test_section_parsing_hard_vs_preferred():
    parsed = parse_greenhouse_description("""
Responsibilities
- Build design systems
Requirements
- Experience with Figma
- Must have 4+ years of product design experience
Preferred qualifications
- Bonus points for Angular
- Nice to have: React
- Travel up to 10% required
""")
    assert parsed["responsibilities"] == ["Build design systems"]
    reqs = parsed["explicit_requirements"]
    prefs = parsed["inferred_preferences"]
    assert any(r.text.startswith("Must have") and r.is_hard_requirement for r in reqs)
    assert any(r.text == "Experience with Figma" and not r.is_hard_requirement for r in reqs)
    assert all(not r.is_hard_requirement for r in prefs)
    assert "Angular" in parsed["preferred_technologies"]


def test_repeated_discovery_queue_approve_reject(tmp_path):
    runner = CliRunner()
    db = tmp_path / "jobs.sqlite3"
    out = tmp_path / "apps"
    result = runner.invoke(app, ["discover", "--db", str(db), "--output", str(out)])
    assert result.exit_code == 0, result.output
    second = runner.invoke(app, ["discover", "--db", str(db), "--output", str(out)])
    assert second.exit_code == 0, second.output
    assert "New: 0" in second.output
    queue = runner.invoke(app, ["queue", "--db", str(db)])
    assert queue.exit_code == 0
    assert "northstar-sr-product-designer" in queue.output
    show = runner.invoke(app, ["show", "northstar-sr-product-designer", "--db", str(db)])
    assert show.exit_code == 0
    assert "Requirements:" in show.output
    approve = runner.invoke(app, ["approve", "northstar-sr-product-designer", "--db", str(db)])
    assert approve.exit_code == 0
    assert "No application was submitted" in approve.output
    reject = runner.invoke(app, ["reject", "aegis-defense-react-ml", "--db", str(db)])
    assert reject.exit_code == 0
