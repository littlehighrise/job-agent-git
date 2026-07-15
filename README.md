# job-agent

Local-first proof of concept for an AI-assisted personal job-search and application preparation agent.

The MVP is intentionally **not** a mass auto-apply bot. It discovers jobs from configured sources, normalizes postings, compares them against verified candidate evidence, scores fit/risk, creates review packages, and stops at human review.

## Current vertical slice

This slice supports both sample local jobs and real public Greenhouse job boards. Matching remains deterministic and inspectable; no LLM, browser automation, LinkedIn scraping, CAPTCHA handling, or automatic form submission is included.

Boundaries are separated into:

- `models.py`: candidate evidence, search preferences, job postings, match analysis, resume plans, audits.
- `sources/`: source adapter architecture with `LocalJsonSourceAdapter` and `GreenhouseSourceAdapter`.
- `sources/greenhouse.py`: public Greenhouse board fetching, conservative description parsing, and remote-status inference.
- `matching.py`: deterministic constraint checks, title matching, requirement/evidence scoring, and classification.
- `resume/engine.py`: structured resume planning and HTML rendering.
- `audit.py`: factual consistency checks for prohibited claims and unsupported technologies.
- `persistence.py`: SQLite application history, safe schema initialization, decisions, artifact paths, and deduplication.
- `application_adapters/`: conservative seam for later ATS/browser support; automatic submission is disabled.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
job-agent discover
```

The command writes packages under `applications/` and records application history in `job_agent.sqlite3`.

## Source configuration

Sources are configured in `config/search_preferences.json` under the `sources` list. Multiple source types can coexist.

Local JSON source:

```json
{
  "type": "local_json",
  "path": "data/sample_jobs/jobs.json"
}
```

Greenhouse source:

```json
{
  "type": "greenhouse",
  "company": "Example Company",
  "board_token": "examplecompany"
}
```

The `board_token` is the public Greenhouse board token from URLs such as `https://boards.greenhouse.io/examplecompany`. It is not an API key. You can add multiple Greenhouse companies by adding multiple objects with `type: "greenhouse"`. See `config/search_preferences.greenhouse.example.json` for a safe editable example.

The Greenhouse adapter uses public Greenhouse job-board endpoints, preserves the original application URL, stores source job IDs from Greenhouse, sets `ats_type` to `greenhouse`, and preserves unknown values rather than inventing salary, country, seniority, industry, technologies, or remote status.

## Discovery workflow

Run discovery:

```bash
job-agent discover
```

Discovery:

1. Loads configured job sources.
2. Fetches jobs.
3. Normalizes them into `JobPosting`.
4. Deduplicates by source job ID, canonical URL, then employer/title/location.
5. Checks whether each job was already seen.
6. Runs deterministic matching.
7. Persists job and analysis JSON in SQLite.
8. Generates local artifacts for `REVIEW_REQUIRED` and `AUTO_APPLY_ELIGIBLE` jobs.
9. Prints a concise summary.

Example summary:

```text
Discovered: 2
New: 2
Rejected: 1
Review required: 1
Auto-apply eligible: 0
```

`job-agent run` remains as a backward-compatible alias for `job-agent discover`.

## Review queue workflow

List jobs awaiting review:

```bash
job-agent queue
```

Show detailed deterministic reasoning for a job:

```bash
job-agent show <job-id>
```

Approve a job for later manual action:

```bash
job-agent approve <job-id>
```

Reject a job so it leaves the local review queue:

```bash
job-agent reject <job-id>
```

Approval records a local human decision and timestamp only. **Approval does not submit an application.** Discovery also does not apply to jobs.

## Generated artifacts

For jobs that reach `REVIEW_REQUIRED` or `AUTO_APPLY_ELIGIBLE`, the project writes a local package containing:

- `job.json`
- `analysis.json`
- `resume_plan.json`
- `resume.json`
- `resume.html`
- `audit.json`

Packages are not regenerated for the same already-created analysis path during repeated discovery, avoiding unnecessary duplicate output for unchanged jobs.

## Greenhouse parsing approach

The Greenhouse parser is deterministic and conservative. It recognizes common headings such as Responsibilities, What you'll do, Requirements, Qualifications, Minimum qualifications, Preferred qualifications, Nice to have, Bonus, and About you. It extracts responsibilities, explicit requirements, inferred preferences, technologies, years of experience, education requirements, clearance requirements, and clearly stated travel requirements.

Hard requirements are marked hard only when wording strongly supports it, such as “required,” “must have,” “minimum qualification,” or required clearance language. Preferred, bonus, and nice-to-have items remain preferences.

## Persistence and schema changes

SQLite initialization is additive and does not destroy existing local data. The `applications` table now stores enough information for local review:

- source and source job ID
- employer, title, location, application URL
- first discovered and last seen timestamps
- match score, evidence confidence, risk score, classification
- serialized job and analysis JSON
- artifact directory
- user decision, decision timestamp, and notes

Deduplication uses the strongest available identifiers in order: source/job ID, application URL, then employer + normalized title + location. It does not merge jobs merely because titles are similar.

## Deterministic matching foundation

The matcher intentionally stays deterministic in this slice. It evaluates each requirement into a structured status: `SUPPORTED`, `PARTIALLY_SUPPORTED`, `TRANSFERABLE`, `UNSUPPORTED`, or `CONTRADICTED`. Each evaluation records the original requirement text, category, hard-requirement flag, confidence, matched evidence statement IDs, matched experience IDs, explanation, weight, automatic-application blockers, and absolute application blockers.

Matching uses phrase-level concept normalization before token fallback. Weighted scoring is inspectable and favors hard requirement coverage over soft preferences. Preferred qualifications cannot compensate for absolute blockers such as an excluded hard industry, unsupported required clearance, prior application, country mismatch, or a contradicted explicit hard requirement.

## Tests

```bash
pytest
```

Tests use mocked Greenhouse API responses and do not depend on live internet access.

## Security and boundaries

Do not commit real API keys or sensitive private profile data. This slice does **not** automate applications, browser sessions, LinkedIn scraping, CAPTCHA handling, LLM calls, autonomous legal answers, attestations, or form submission. Real application automation is deferred.
