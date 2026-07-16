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

The Greenhouse adapter uses public Greenhouse job-board endpoints, preserves the original application URL and raw HTML description, stores source job IDs from Greenhouse, sets `ats_type` to `greenhouse`, and preserves unknown values rather than inventing missing facts. It deterministically extracts only reliable structured fields such as explicit salary ranges, recognizable countries in locations, remote status, seniority, technologies, and requirements.

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

The Greenhouse parser is deterministic and conservative. It first boundedly decodes entity-encoded Greenhouse markup (for example `&lt;p&gt;...&lt;/p&gt;`, nested `&amp;nbsp;`, and `&amp;mdash;`) before normalizing HTML into structured text while preserving heading, paragraph, and list-item boundaries; this specifically supports real postings whose headings are represented by `<h1>`-`<h6>`, `<strong>`, `<b>`, or bold text inside paragraphs. It recognizes common headings such as Responsibilities, What you'll do, What you'll be doing, Who you are, What you bring, What you should have, What you'll need, What we're looking for in you, Requirements, Qualifications, Minimum qualifications, Preferred qualifications, Nice to have, Bonus Points, and About you. Benefits, company description, Why Discord-style company pitches, equal-opportunity, privacy, accommodations, inclusion, compensation, and employer-encouragement boilerplate terminate active qualification sections and are excluded from candidate requirements. It extracts responsibilities, individual explicit requirement bullets, inferred preferences, named technologies, years of experience, salary ranges, recognizable countries, education requirements, clearance requirements, and clearly stated travel requirements.

Hard requirements are marked hard only when wording strongly supports it, such as “required,” “must have,” “minimum qualification,” or required clearance language. Preferred, bonus, and nice-to-have items remain preferences. If a posting has substantive explicit qualifications but none are worded as hard requirements, hard-requirement coverage is treated as a not-applicable scoring dimension rather than a failed zero; explicit requirement coverage is still scored normally and unsupported hard requirements still block auto-apply.

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

`config/career_evidence.json` is the authoritative source for candidate-side matching evidence. If that file is incomplete, otherwise relevant roles can appear unsupported because dated experience totals, statement-level evidence IDs, allowed claims, technologies, responsibilities, accomplishments, and industries are all derived from the configured evidence rather than inferred from a resume or generated by an LLM.

Benefits and compensation content from job descriptions is parsed separately from candidate qualifications. Salary ranges can populate structured compensation fields such as `salary_min`, `salary_max`, and `currency` without becoming explicit requirements, inferred preferences, requirement evaluations, qualification coverage, or evidence-confidence inputs. The parser excludes contextual employee-benefit bullets such as competitive benefits, mental-health benefits, benefits-and-growth headings, and country-varying benefits notices, while preserving legitimate qualification language that uses “benefits” in a business sense, such as articulating business benefits and technical advantages.

Matching uses phrase-level concept normalization before token fallback. The concept map distinguishes formal user research from requirements discovery and research-informed design; data-informed design from design-system tokens; and technical systems reasoning from design systems. Compound requirements are evaluated conservatively so one matched clause does not prove every substantial concept in a multi-part bullet, and broad occupational tokens such as design, product, systems, user, team, work, and experience are not enough by themselves to establish support. Weighted scoring is inspectable and favors hard requirement coverage over soft preferences. Preferred qualifications cannot compensate for absolute blockers such as an excluded hard industry, unsupported required clearance, prior application, country mismatch, or a contradicted explicit hard requirement.

## Tests

```bash
pytest
```

Tests use mocked Greenhouse API responses and do not depend on live internet access.

## Security and boundaries

Do not commit real API keys or sensitive private profile data. This slice does **not** automate applications, browser sessions, LinkedIn scraping, CAPTCHA handling, LLM calls, autonomous legal answers, attestations, or form submission. Real application automation is deferred.

## Live Greenhouse Validation with GitHub Actions

The repository includes a manually triggered GitHub Actions workflow named **Live Greenhouse Validation** for validating real public Greenhouse discovery and the deterministic matching pipeline outside the Codex cloud environment.

To run it:

1. Open the GitHub repository.
2. Click **Actions**.
3. Select **Live Greenhouse Validation**.
4. Click **Run workflow**.
5. Optionally change the comma-separated Greenhouse board-token list, minimum compensation target, remote preference mode, or maximum board limit.
6. Run the workflow.
7. Review the GitHub Step Summary on the workflow run page.
8. Download the `greenhouse-live-validation-results` artifact if deeper inspection is needed.

The workflow discovers and scores jobs only. It does **not** submit applications, drive a browser, call an LLM API, or require an OpenAI API key. Public Greenhouse board validation uses temporary workflow data: a generated search-preferences file, temporary SQLite database, temporary application/review artifacts, discovery logs, source-result metadata, and a machine-readable `live-validation-summary.json`. The live summary includes parsing-quality counts, explicit-requirement/responsibility counts, requirement-evaluation counts, non-zero and zero evidence-confidence counts, hard-requirement presence counts, absent scoring-dimension counts, per-component applicability diagnostics, title-only analysis counts, LOW parsing counts, actionable review counts, parsing-review counts, below-threshold-only rejection counts, near-review-threshold counts, and prominent non-fatal warnings when every fetched job parses as insufficient or no jobs produce requirements/evaluations. User-facing Top Matching Jobs contain only actionable evaluated matches with substantive explicit requirements, MEDIUM/HIGH parsing quality, requirement evaluations, and non-zero evidence confidence. Exact-title or target-family postings with failed qualification parsing are kept visible in a separate Jobs Needing Parsing Review section with the parsing quality, title alignment, diagnostic reason, and URL; unrelated rejected roles are kept in a separate highest-scoring rejected diagnostic section.

Live boards can change or become inaccessible. A company may move away from Greenhouse, block access, return malformed data, or have no currently listed jobs. Individual source failures are reported separately in the summary and artifact metadata; they are warnings unless the application itself fails to parse configuration, create the database, run the CLI, or complete the automated tests. Zero discovered jobs is reported clearly and should not be treated as proof that the matcher produced live recommendations.

This manually triggered workflow is intentionally separate from any future scheduled hourly discovery workflow.

## Score calibration and reporting

The matcher produces three separate numbers:

- **Role match score** ranks apparent fit for review. It combines title alignment, explicit requirement coverage, hard-requirement coverage, preference alignment, domain signals, work arrangement, and IC/management alignment. The matcher uses normalized applicable-dimension scoring: applicable dimensions keep their configured relative weights, while `UNKNOWN` dimensions (posting lacks enough information, such as unavailable domain or unknown work arrangement) and `NOT_APPLICABLE` dimensions (posting has no items in that category, such as no preferred qualifications or no hard requirements) receive zero effective weight instead of zero fit. Each `analysis.json` score breakdown exposes component value, configured weight, applicability, effective normalized weight, contribution, and absent dimensions so a UI can explain why a dimension was not scored. Bad results remain bad: a present unsupported or contradicted hard requirement is scored as poor coverage and blocks auto-apply; contradictions and absolute blockers still force rejection.
- **Evidence confidence** estimates how strongly verified candidate evidence supports the evaluated requirements. It now includes unsupported and contradicted explicit requirements in the denominator, caps transferable and partial support below direct statement-level support, and treats broad experience-only matches as lower-confidence than direct verified evidence statements. Multiple direct evidence statements can raise confidence; generic overlap alone should not.
- **Application risk** estimates the risk of submitting an inaccurate, poorly supported, or unsuitable application. It is intentionally not a duplicate of role score or evidence confidence. Absolute blockers such as excluded hard industries, unsupported required clearance, country mismatch, prior application, or contradicted hard requirements produce high risk and rejection. A single unsupported hard requirement blocks auto-apply but does not by itself force maximum risk when the role otherwise remains reviewable. Minor preferred gaps add little or no risk.

A high role match score therefore does **not** automatically mean a job is safe to auto-apply. `AUTO_APPLY_ELIGIBLE` remains intentionally rare: the role score must meet the auto-apply threshold, evidence confidence must meet the configured confidence threshold, parsing quality must be sufficient, there must be substantive parsed requirements, there must be no auto-apply blockers, and application risk must remain low. If no substantive requirements are parsed, requirement coverage is not treated as 100%; title alignment is not normalized upward to consume the full score, evidence confidence remains low, and the analysis adds `Insufficient parsed qualification data for confident automated evaluation.` `REVIEW_REQUIRED` is a valid calibrated outcome for strong roles that need human judgment, missing facts, parsing gaps, or more precise evidence review.

To create a reproducible calibration report from a downloaded live-validation database or another persisted discovery database, run:

```bash
job-agent report-calibration --db validation.sqlite3 --output-json calibration-report.json --output-markdown calibration-report.md --top-n 20
```

The report selects the highest-ranked persisted jobs, preserves the normalized job snapshot and score breakdown, lists supported/unsupported/contradicted/transferable requirements, records matched blocker and review-concern context, and summarizes role-score, evidence-confidence, application-risk, and classification distributions. If no database is present at the requested path, the command writes an explicit empty report instead of implying that live jobs were reviewed.

Calibration should use downloaded live-validation artifacts when available. If artifacts are unavailable, use fixture-based checks only and state that no live artifact data was inspected. Scoring changes should be made only when supported by observed requirement/evidence behavior, not to manufacture more `AUTO_APPLY_ELIGIBLE` jobs.
