from __future__ import annotations

from collections import defaultdict
from html import escape

from job_agent.models import CandidateProfile, ExperienceEvidence, JobPosting, MatchAnalysis, ResumeContentPlan, ResumeExperience, ResumePlanItem, StructuredResume


def build_resume_plan(job: JobPosting, analysis: MatchAnalysis) -> ResumeContentPlan:
    items = [
        ResumePlanItem(
            job_requirement=m.requirement,
            selected_candidate_evidence=m.evidence_ids,
            why_relevant=m.explanation,
            intended_resume_section="experience",
            proposed_framing=f"Frame verified experience around: {m.requirement}",
            confidence=m.confidence,
        )
        for m in analysis.matched_requirements[:8]
    ]
    return ResumeContentPlan(job_id=job.source_job_id, items=items)


def build_structured_resume(profile: CandidateProfile, evidence: list[ExperienceEvidence], job: JobPosting, plan: ResumeContentPlan) -> StructuredResume:
    used_ids = {eid for item in plan.items for eid in item.selected_candidate_evidence}
    exp_by_id = {exp.experience_id: exp for exp in evidence}
    grouped: dict[str, list[str]] = defaultdict(list)
    for item in plan.items:
        for eid in item.selected_candidate_evidence:
            exp = exp_by_id.get(eid)
            if exp and exp.eligibility.resume:
                grouped[eid].append(item.proposed_framing)
    experiences = []
    for eid, framings in grouped.items():
        exp = exp_by_id[eid]
        bullets = []
        for ev in exp.all_evidence()[:4]:
            if ev.eligibility.resume:
                bullets.append(ev.text)
        bullets.extend(framings[:2])
        experiences.append(ResumeExperience(employer=exp.employer, title=exp.role, bullets=list(dict.fromkeys(bullets))[:5]))
    competencies = sorted({term for exp in evidence if exp.experience_id in used_ids for term in exp.skills + exp.technologies})[:18]
    return StructuredResume(
        candidate_name=profile.full_name,
        headline=job.canonical_job_title or job.job_title,
        contact={"email": profile.email, "phone": profile.phone, "portfolio": profile.portfolio_url, "linkedin": profile.linkedin_url},
        summary=f"{profile.full_name} is positioned for {job.job_title} roles using verified evidence only; review all generated wording before submission.",
        competencies=competencies,
        experience=experiences,
        project_highlights=[],
    )


def render_resume_html(resume: StructuredResume) -> str:
    contact = " | ".join(escape(v) for v in resume.contact.values() if v)
    skills = ", ".join(escape(s) for s in resume.competencies)
    exp_html = "".join(
        f"<section><h2>{escape(exp.title)} — {escape(exp.employer)}</h2><ul>" + "".join(f"<li>{escape(b)}</li>" for b in exp.bullets) + "</ul></section>"
        for exp in resume.experience
    )
    return f"""<!doctype html>
<html><head><meta charset='utf-8'><title>{escape(resume.candidate_name)} Resume</title>
<style>body{{font-family:Arial,sans-serif;line-height:1.45;max-width:820px;margin:40px auto;color:#111}}h1{{margin-bottom:0}}.contact{{color:#444}}h2{{font-size:1.05rem;margin-top:1.5rem}}li{{margin:.25rem 0}}</style></head>
<body><h1>{escape(resume.candidate_name)}</h1><p class='contact'>{contact}</p><h2>{escape(resume.headline)}</h2><p>{escape(resume.summary)}</p><h2>Competencies</h2><p>{skills}</p>{exp_html}</body></html>"""
