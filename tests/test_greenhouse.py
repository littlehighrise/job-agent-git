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

DATADOG_STYLE_HTML = """
<p><strong>What You’ll Do:</strong></p>
<ul>
  <li>Partner with engineering teams to design complex technical products.</li>
  <li>Use systems thinking to improve observability workflows.</li>
</ul>
<p><strong>Who You Are:</strong></p>
<ul>
  <li>You have 10+ years of experience in digital product design.</li>
  <li>Experience designing complex technical products.</li>
  <li>Experience with developer platforms, observability, infrastructure, or technical domains.</li>
  <li>Research experience and cross-functional engineering collaboration.</li>
</ul>
<p><strong>Preferred Qualifications:</strong></p>
<ul><li>Bonus points for Figma experience.</li></ul>
<p><strong>Benefits:</strong></p>
<ul><li>Medical, dental, and vision benefits.</li></ul>
<p>We encourage you to apply even if you do not meet every qualification.</p>
<p>The reasonably estimated yearly salary for this role at Datadog is:<br>$204,000 — $255,000 USD</p>
<p>Equal opportunity employer. Privacy notice.</p>
"""


def test_greenhouse_html_strong_paragraph_headings_and_lists():
    parsed = parse_greenhouse_description(DATADOG_STYLE_HTML)
    assert parsed["responsibilities"] == [
        "Partner with engineering teams to design complex technical products.",
        "Use systems thinking to improve observability workflows.",
    ]
    req_texts = [r.text for r in parsed["explicit_requirements"]]
    assert "You have 10+ years of experience in digital product design." in req_texts
    assert "Experience designing complex technical products." in req_texts
    assert len(req_texts) >= 4
    assert all("Medical" not in text for text in req_texts)
    assert all("encourage you to apply" not in text.lower() for text in req_texts)
    assert parsed["required_years_experience"] == 10
    assert parsed["salary_min"] == 204000
    assert parsed["salary_max"] == 255000
    assert parsed["currency"] == "USD"
    assert parsed["parsing_quality"] == "HIGH"
    assert [r.text for r in parsed["inferred_preferences"]] == ["Bonus points for Figma experience."]
    assert parsed["required_technologies"] == []
    assert parsed["preferred_technologies"] == ["Figma"]


def test_greenhouse_adapter_preserves_raw_description_and_extracts_country(monkeypatch):
    def fake_urlopen(req, timeout):
        return FakeResponse(payload([{
            "id": 456, "title": "Staff Product Designer", "absolute_url": "https://boards.greenhouse.io/datadog/jobs/456",
            "location": {"name": "Tel Aviv, Israel"}, "updated_at": "2026-07-01T00:00:00Z", "content": DATADOG_STYLE_HTML,
        }]))
    monkeypatch.setattr("job_agent.sources.greenhouse.urlopen", fake_urlopen)
    job = GreenhouseSourceAdapter("datadog", company="Datadog").fetch_jobs()[0]
    assert job.description == DATADOG_STYLE_HTML
    assert job.country == "Israel"
    assert job.required_years_experience == 10
    assert job.salary_min == 204000
    assert job.explicit_requirements

DATADOG_ENCODED_HTML = """
&lt;p&gt;&lt;strong&gt;What You’ll Do:&lt;/strong&gt;&lt;/p&gt;
&lt;ul&gt;
&lt;li&gt;Partner closely with PMs and engineers to design intuitive experiences for highly technical systems.&lt;/li&gt;
&lt;li&gt;Conduct and synthesize qualitative and quantitative research.&lt;/li&gt;
&lt;/ul&gt;
&lt;p&gt;&lt;strong&gt;Who You Are:&lt;/strong&gt;&lt;/p&gt;
&lt;ul&gt;
&lt;li&gt;You have 10+ years of experience in digital product design.&lt;/li&gt;
&lt;li&gt;You have experience designing complex technical products and systems-oriented workflows.&lt;/li&gt;
&lt;/ul&gt;
&lt;p&gt;&lt;strong&gt;Benefits:&lt;/strong&gt;&lt;/p&gt;
&lt;ul&gt;&lt;li&gt;Medical benefits and 401(k).&lt;/li&gt;&lt;/ul&gt;
"""

FIGMA_ENCODED_HTML = """
&lt;h4&gt;&lt;strong&gt;What you&#39;ll do at Figma:&lt;/strong&gt;&lt;/h4&gt;
&lt;ul&gt;
&lt;li&gt;Work cross-functionally with product management, engineering, design, and research peers&lt;/li&gt;
&lt;/ul&gt;
&lt;h4&gt;&lt;strong&gt;We’d love to hear from you if you have:&lt;/strong&gt;&lt;/h4&gt;
&lt;ul&gt;
&lt;li&gt;3+ years of experience designing UX and UI for a software product&lt;/li&gt;
&lt;/ul&gt;
&lt;h4&gt;&lt;strong&gt;While it’s not required, it’s an added plus if you also have:&lt;/strong&gt;&lt;/h4&gt;
&lt;ul&gt;
&lt;li&gt;Prior work creating, maintaining, or contributing to a design system&lt;/li&gt;
&lt;/ul&gt;
&lt;p&gt;Equal opportunity employer. Privacy notice. Reasonable accommodations are available.&lt;/p&gt;
"""


def _texts(items):
    return [item.text for item in items]


def test_entity_encoded_datadog_fixture_matches_raw_html_and_excludes_benefits():
    raw = DATADOG_ENCODED_HTML.replace("&lt;", "<").replace("&gt;", ">")
    encoded = parse_greenhouse_description(DATADOG_ENCODED_HTML)
    raw_parsed = parse_greenhouse_description(raw)
    assert encoded["responsibilities"] == raw_parsed["responsibilities"]
    assert _texts(encoded["explicit_requirements"]) == _texts(raw_parsed["explicit_requirements"])
    assert encoded["responsibilities"] == [
        "Partner closely with PMs and engineers to design intuitive experiences for highly technical systems.",
        "Conduct and synthesize qualitative and quantitative research.",
    ]
    assert _texts(encoded["explicit_requirements"]) == [
        "You have 10+ years of experience in digital product design.",
        "You have experience designing complex technical products and systems-oriented workflows.",
    ]
    assert encoded["required_years_experience"] == 10
    assert encoded["parsing_quality"] in {"MEDIUM", "HIGH"}
    assert all("benefit" not in text.lower() and "401" not in text for text in _texts(encoded["explicit_requirements"]))


def test_entity_encoded_figma_fixture_classifies_required_and_preferred_sections():
    parsed = parse_greenhouse_description(FIGMA_ENCODED_HTML)
    assert parsed["responsibilities"] == ["Work cross-functionally with product management, engineering, design, and research peers"]
    assert _texts(parsed["explicit_requirements"]) == ["3+ years of experience designing UX and UI for a software product"]
    assert _texts(parsed["inferred_preferences"]) == ["Prior work creating, maintaining, or contributing to a design system"]
    assert parsed["required_years_experience"] == 3
    assert parsed["parsing_quality"] == "HIGH"
    assert all("privacy" not in text.lower() and "accommodation" not in text.lower() for text in _texts(parsed["explicit_requirements"]))


def test_plain_text_and_nested_entities_are_normalized_without_damage():
    parsed = parse_greenhouse_description("Responsibilities\n- Design systems &amp;nbsp; for teams &amp;mdash; globally\nRequirements\n- You have 3+ years &quot;UX&quot; experience")
    assert parsed["responsibilities"] == ["Design systems for teams - globally"]
    assert _texts(parsed["explicit_requirements"]) == ['You have 3+ years "UX" experience']
    assert parsed["required_years_experience"] == 3


def test_salary_extraction_after_entity_decoding():
    parsed = parse_greenhouse_description("""
&lt;div class=&quot;pay-range&quot;&gt;
&lt;span&gt;$165,000&lt;/span&gt;
&lt;span class=&quot;divider&quot;&gt;&amp;mdash;&lt;/span&gt;
&lt;span&gt;$190,000 USD&lt;/span&gt;
&lt;/div&gt;
""")
    assert parsed["salary_min"] == 165000
    assert parsed["salary_max"] == 190000
    assert parsed["currency"] == "USD"


def test_benefits_growth_pay_transparency_and_salary_excluded_from_requirements_but_salary_extracted():
    parsed = parse_greenhouse_description("""
<h2>Qualifications</h2>
<ul><li>10+ years of experience in digital product design</li></ul>
<h2>Benefits and Growth:</h2>
<ul>
  <li>New hire stock equity (RSUs) and employee stock purchase plan (ESPP)</li>
  <li>Continuous professional development benefits, product training, and career pathing</li>
  <li>Intradepartmental mentor and buddy program and employee resource groups</li>
  <li>Health insurance, dental insurance, vision insurance, 401(k), paid parental leave, and paid time off</li>
</ul>
<h2>Pay Transparency Disclosure:</h2>
<p>Annual Base Salary Range</p>
<p>$204,000-$255,000 USD</p>
""")
    req_texts = _texts(parsed["explicit_requirements"])
    assert req_texts == ["10+ years of experience in digital product design"]
    assert parsed["salary_min"] == 204000
    assert parsed["salary_max"] == 255000
    assert parsed["currency"] == "USD"


def test_ignored_heading_terminates_active_qualification_section():
    parsed = parse_greenhouse_description("""
Requirements
- Experience designing complex technical products
Our Benefits:
- RSUs and ESPP
- Employee resource groups
""")
    assert _texts(parsed["explicit_requirements"]) == ["Experience designing complex technical products"]


def test_content_level_perks_without_clean_heading_are_excluded():
    parsed = parse_greenhouse_description("""
Requirements
- Experience with Figma and product design
- RSUs and employee stock purchase plan
- Health insurance and 401(k)
- Salary range: $100,000-$120,000 USD
""")
    assert _texts(parsed["explicit_requirements"]) == ["Experience with Figma and product design"]
    assert parsed["salary_min"] == 100000
    assert parsed["salary_max"] == 120000


def test_greenhouse_excludes_benefits_bullets_but_preserves_business_benefits_requirement():
    from job_agent.sources.greenhouse import parse_greenhouse_description
    html = """
    <p><strong>Who You Are:</strong></p><ul>
    <li>Generous and competitive global and US benefits</li>
    <li>Free, global mental health benefits for employees and dependents age 6+</li>
    <li>Ability to articulate the business benefits and technical advantages of design decisions</li>
    </ul><p><strong>Benefits and Growth:</strong></p><ul><li>Benefits and Growth listed above may vary based on the country of your employment.</li></ul>
    """
    parsed = parse_greenhouse_description(html)
    texts = [r.text for r in parsed["explicit_requirements"]]
    assert texts == ["Ability to articulate the business benefits and technical advantages of design decisions"]


def test_discord_sections_and_boilerplate_termination():
    parsed = parse_greenhouse_description("""
<h2>What You'll Be Doing</h2><ul><li>Design engagement experiences</li></ul>
<h2>What you should have</h2><ul>
<li>5+ years designing and shipping digital products</li>
<li>Strong product thinking</li>
<li>Advanced Figma experience</li>
<li>Experience contributing to a design system</li>
<li>Cross-functional collaboration</li>
</ul>
<h2>Bonus Points</h2><ul><li>Gaming experience</li></ul>
<h2>Why Discord?</h2><p>Company mission text.</p>
<h2>Applicant and Candidate Privacy Policy</h2><ul><li>Privacy text should not be a preference</li></ul>
<h2>Reasonable Accommodations</h2><ul><li>Accommodation text should not be a preference</li></ul>
""")
    assert parsed["responsibilities"] == ["Design engagement experiences"]
    assert "5+ years designing and shipping digital products" in _texts(parsed["explicit_requirements"])
    assert "Gaming experience" in _texts(parsed["inferred_preferences"])
    assert all("privacy" not in t.lower() and "accommodation" not in t.lower() and "mission" not in t.lower() for t in _texts(parsed["inferred_preferences"]))
    assert parsed["parsing_quality"] in {"HIGH", "MEDIUM"}


def test_work_arrangement_and_country_inference_regressions():
    from job_agent.sources.greenhouse import infer_country, infer_remote_status
    assert infer_remote_status("Staff Product Designer", "New York, NY", "Designing observability for distributed systems. We operate as a hybrid workplace.") == RemoteStatus.HYBRID
    assert infer_remote_status("Product Designer", "San Francisco, CA", "This role can be held from one of our US hubs or remotely in the United States.") == RemoteStatus.REMOTE
    assert infer_remote_status("Designer", None, "Distributed team across several time zones") == RemoteStatus.REMOTE
    assert infer_remote_status("Designer", None, "Designing observability for distributed systems") == RemoteStatus.UNKNOWN
    assert infer_country("San Francisco, CA • New York, NY • United States") == "United States"
    assert infer_country("Tel Aviv, Israel") == "Israel"
    assert infer_country("Paris, France") == "France"
    assert infer_country("San Francisco Bay Area") == "United States"
