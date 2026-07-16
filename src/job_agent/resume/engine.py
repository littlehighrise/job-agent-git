from __future__ import annotations

from collections import defaultdict
from datetime import date
from html import escape

from job_agent.models import CandidateProfile, ExperienceEvidence, JobPosting, MatchAnalysis, RequirementMatchStatus, ResumeBullet, ResumeContentPlan, ResumeExperience, ResumePlanItem, StructuredResume
from job_agent.profile import contact_complete

RELEVANT = {"product design","ux design","ui design","interaction design","prototyping","figma","design systems","component libraries","design tokens","information architecture","visual design","responsive design","accessibility","html/css","cross-functional collaboration","developer collaboration","complex workflow design","wireframing","user flows"}
EXCLUDED_TOOLS = {"whm", "robohelp"}


def build_resume_plan(job: JobPosting, analysis: MatchAnalysis, evidence: list[ExperienceEvidence] | None = None) -> ResumeContentPlan:
    statements = {ev.statement_id: (exp, ev) for exp in (evidence or []) for ev in exp.all_evidence()}
    items: list[ResumePlanItem] = []
    for idx, req in enumerate(analysis.requirement_evaluations):
        if req.status not in {RequirementMatchStatus.SUPPORTED, RequirementMatchStatus.PARTIALLY_SUPPORTED, RequirementMatchStatus.TRANSFERABLE}:
            continue
        if not req.matched_evidence_statement_ids:
            continue
        sid = req.matched_evidence_statement_ids[0]
        exp, stmt = statements.get(sid, (None, None))
        if not stmt or not exp or not stmt.eligibility.resume:
            continue
        items.append(ResumePlanItem(
            job_requirement=req.requirement,
            selected_candidate_evidence=req.matched_experience_ids,
            selected_statement_ids=req.matched_evidence_statement_ids[:3],
            selected_experience_id=exp.experience_id,
            why_relevant=req.explanation,
            verified_statement_text=stmt.text,
            intended_resume_section="experience",
            proposed_framing=stmt.text,
            wording_type="verbatim_verified_evidence",
            confidence=req.confidence,
        ))
        if len(items) >= 12:
            break
    if not items:  # legacy analyses
        for m in analysis.matched_requirements[:8]:
            items.append(ResumePlanItem(job_requirement=m.requirement, selected_candidate_evidence=m.evidence_ids, selected_statement_ids=m.statement_ids, selected_experience_id=m.evidence_ids[0] if m.evidence_ids else None, why_relevant=m.explanation, verified_statement_text=None, intended_resume_section="experience", proposed_framing="", wording_type="excluded", confidence=m.confidence))
    return ResumeContentPlan(job_id=job.source_job_id, items=items)


def _fmt(d: date | str | None) -> str:
    if d is None: return ""
    if d == "present": return "Present"
    return d.strftime("%b %Y") if hasattr(d, "strftime") else str(d)


def build_structured_resume(profile: CandidateProfile, evidence: list[ExperienceEvidence], job: JobPosting, plan: ResumeContentPlan) -> StructuredResume:
    exp_by_id = {exp.experience_id: exp for exp in evidence}
    stmt_map = {ev.statement_id: (exp, ev) for exp in evidence for ev in exp.all_evidence()}
    grouped: dict[str, list[ResumeBullet]] = defaultdict(list)
    seen_text: set[str] = set()
    for item in plan.items:
        for sid in item.selected_statement_ids:
            got = stmt_map.get(sid)
            if not got: continue
            exp, stmt = got
            text = stmt.text.strip()
            norm = text.lower()
            if not stmt.eligibility.resume or norm in seen_text or "Frame verified experience around" in text:
                continue
            seen_text.add(norm)
            grouped[exp.experience_id].append(ResumeBullet(text=text, experience_id=exp.experience_id, statement_ids=[sid], confidence=item.confidence, targeted_requirements=[item.job_requirement]))
    # top up targeted roles with verified statements
    for exp in evidence:
        if exp.experience_id not in grouped and any(k in exp.experience_id for k in ["caci", "matchstick", "little_highrise", "hdr"]):
            grouped[exp.experience_id] = []
        for stmt in exp.all_evidence():
            if len(grouped.get(exp.experience_id, [])) >= 5: break
            text = stmt.text.strip(); norm = text.lower()
            if stmt.eligibility.resume and norm not in seen_text and (set(map(str.lower, stmt.skills + stmt.technologies)) & {r.lower() for r in RELEVANT}):
                seen_text.add(norm)
                grouped[exp.experience_id].append(ResumeBullet(text=text, experience_id=exp.experience_id, statement_ids=[stmt.statement_id], confidence=100))
    selected = [exp_by_id[eid] for eid in grouped if eid in exp_by_id and grouped[eid]][:5]
    selected.sort(key=lambda e: e.start_date or date.min, reverse=True)
    experiences = [ResumeExperience(employer=e.employer, title=e.role, start_date=e.start_date, end_date=e.end_date, bullets=[b.text for b in grouped[e.experience_id]][:5], bullet_provenance=grouped[e.experience_id][:5]) for e in selected]
    terms = []
    for e in selected:
        for t in e.skills + e.technologies:
            clean = "HTML/CSS" if t.lower() in {"html", "css", "html/css"} else t
            if clean.lower() in EXCLUDED_TOOLS: continue
            if clean.lower() in {r.lower() for r in RELEVANT} and clean not in terms:
                terms.append(clean.title() if clean.islower() else clean)
    return StructuredResume(
        candidate_name=profile.full_name,
        status="READY_FOR_REVIEW" if contact_complete(profile) else "DRAFT_INCOMPLETE",
        headline="Senior Product / UX Designer",
        contact={"email": profile.email, "phone": profile.phone, "portfolio": profile.portfolio_url, "linkedin": profile.linkedin_url},
        summary="I’m a senior product and UX designer with experience shaping complex workflows, reusable design systems, and polished interface experiences. I enjoy collaborating closely with engineers, stakeholders, and clients to turn requirements into clear user flows, prototypes, accessible UI patterns, and maintainable product design resources.",
        competencies=terms[:16],
        experience=experiences,
    )


def render_resume_html(resume: StructuredResume) -> str:
    contact = " | ".join(escape(v) for v in resume.contact.values() if v)
    dates = lambda e: f"{_fmt(e.start_date)} – {_fmt(e.end_date)}" if e.start_date or e.end_date else "Dates unavailable"
    warning = "<p class='draft'>DRAFT INCOMPLETE — private contact information required before submission.</p>" if resume.status == "DRAFT_INCOMPLETE" else ""
    exp_html = "".join(f"<section><h2>{escape(exp.title)} — {escape(exp.employer)}</h2><p class='dates'>{escape(dates(exp))}</p><ul>" + "".join(f"<li>{escape(b)}</li>" for b in exp.bullets) + "</ul></section>" for exp in resume.experience)
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>{escape(resume.candidate_name)} Resume</title><style>body{{font-family:Arial,sans-serif;line-height:1.45;max-width:820px;margin:40px auto;color:#111}}h1{{margin-bottom:0}}.contact,.dates{{color:#444}}.draft{{border:2px solid #b45309;padding:8px;color:#92400e}}h2{{font-size:1.05rem;margin-top:1.5rem}}li{{margin:.25rem 0}}</style></head><body><h1>{escape(resume.candidate_name)}</h1>{warning}<p class='contact'>{contact}</p><h2>{escape(resume.headline)}</h2><p>{escape(resume.summary)}</p><h2>Competencies</h2><p>{escape(', '.join(resume.competencies))}</p>{exp_html}</body></html>"""
