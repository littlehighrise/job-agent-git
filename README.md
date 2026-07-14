# job-agent

Local-first proof of concept for an AI-assisted personal job-search and application preparation agent.

The MVP is intentionally **not** a mass auto-apply bot. It discovers jobs from configured sources, normalizes postings, compares them against verified candidate evidence, scores fit/risk, creates review packages, and stops at human review.

## Architecture tradeoff for the first slice

This first version uses a Python CLI, Pydantic models, SQLite, deterministic matching, and local JSON job sources. That is less flashy than browser automation or full LLM orchestration, but it makes the highest-risk product principle inspectable immediately: every generated resume package is grounded in verified evidence and scored with readable logic.

Boundaries are separated into:

- `models.py`: candidate evidence, search preferences, job postings, match analysis, resume plans, audits.
- `sources/`: source adapter architecture; MVP includes `LocalJsonSourceAdapter` and leaves safe ATS adapters as future extensions.
- `matching.py`: deterministic constraint checks, title matching, requirement/evidence scoring, and classification.
- `resume/engine.py`: structured resume planning and HTML rendering.
- `audit.py`: factual consistency checks for prohibited claims and unsupported technologies.
- `persistence.py`: SQLite application history and deduplication by source/job ID.
- `llm/prompts.py`: narrow future LLM stage prompts; no giant autonomous prompt controls the system.
- `application_adapters/`: conservative seam for later ATS/browser support; automatic submission is disabled.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
job-agent
```

The command writes packages under `applications/` and records application history in `job_agent.sqlite3`.

## First vertical slice

The seeded workflow loads:

- `config/candidate_profile.json`
- `config/career_evidence.json`
- `config/search_preferences.json`
- `data/sample_jobs/jobs.json`

It then:

1. Fetches sample jobs through the adapter interface.
2. Scores each job against configured preferences and verified evidence.
3. Rejects excluded/contradicted jobs.
4. Generates review artifacts for non-rejected jobs:
   - `job.json`
   - `analysis.json`
   - `resume_plan.json`
   - `resume.json`
   - `resume.html`
   - `audit.json`
5. Writes `applications/review_queue.json`.

## Tests

```bash
pytest
```

## Security

Do not commit real API keys or sensitive private profile data. Use `.env` based on `.env.example` for future LLM/API settings.
