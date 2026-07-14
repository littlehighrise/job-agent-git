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


## Deterministic matching foundation

The matcher intentionally stays deterministic in this slice. It now evaluates each requirement into a structured status: `SUPPORTED`, `PARTIALLY_SUPPORTED`, `TRANSFERABLE`, `UNSUPPORTED`, or `CONTRADICTED`. Each evaluation records the original requirement text, category, hard-requirement flag, confidence, matched evidence statement IDs, matched experience IDs, explanation, weight, automatic-application blockers, and absolute application blockers.

Matching uses phrase-level concept normalization before token fallback. The built-in concept groups cover design systems, Figma, engineering collaboration, component standardization, product design, accessibility, responsive design, React engineering, ML engineering, and security clearance language. This is deliberately easy to extend in `src/job_agent/matching.py`.

Weighted scoring is inspectable and favors hard requirement coverage over soft preferences. The current formula is:

```text
final role score =
  title alignment * 0.22
  + weighted explicit requirement coverage * 0.32
  + hard requirement coverage * 0.18
  + preferred qualification alignment * 0.08
  + industry/domain alignment * 0.08
  + work-arrangement alignment * 0.07
  + IC/management alignment * 0.05
```

Preferred qualifications cannot compensate for absolute blockers such as an excluded hard industry, unsupported required clearance, prior application, country mismatch, or a contradicted explicit hard requirement. Unsupported hard requirements usually block automatic application rather than immediately rejecting the job, so a strong-but-imperfect match can still enter human review.

## Tests

```bash
pytest
```

## Security

Do not commit real API keys or sensitive private profile data. Use `.env` based on `.env.example` for future LLM/API settings.
