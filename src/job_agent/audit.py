from __future__ import annotations
import re
from job_agent.models import AuditFinding, ExperienceEvidence, FactualConsistencyAudit, JobPosting, ResumeBullet, StructuredResume
from job_agent.profile import validate_candidate_profile


def audit_resume(job: JobPosting, evidence: list[ExperienceEvidence], resume: StructuredResume, profile=None) -> FactualConsistencyAudit:
    findings: list[AuditFinding] = []
    if profile is not None:
        findings.extend(validate_candidate_profile(profile, submission_required=True))
    allowed_employers = {e.employer for e in evidence}; allowed_titles = {e.role for e in evidence}
    allowed_text = "\n".join([e.employer+" "+e.role+" "+" ".join(e.skills+e.technologies+e.allowed_claims+e.responsibilities+e.accomplishments) for e in evidence] + [ev.text+" "+" ".join(ev.skills+ev.technologies+ev.allowed_claims+ev.verified_metrics) for e in evidence for ev in e.all_evidence()]).lower()
    allowed_statements = {ev.statement_id: ev for e in evidence for ev in e.all_evidence()}
    prohibited = [p for exp in evidence for p in exp.prohibited_claims]
    text = resume.model_dump_json().lower()
    for bad in ["frame verified experience around", "job-agent", "matching engine", "evidence system", "generated wording", "example.com", "placeholder"]:
        if bad in text:
            findings.append(AuditFinding(severity="blocker", category="placeholder_or_system_text", message=f"Resume contains prohibited placeholder/system text: {bad}", referenced_text=bad))
    for claim in prohibited:
        if claim.lower() in text:
            findings.append(AuditFinding(severity="blocker", category="prohibited_claim", message=f"Resume contains prohibited claim: {claim}", referenced_text=claim))
    seen=[]
    for exp in resume.experience:
        if exp.employer not in allowed_employers: findings.append(AuditFinding(severity="blocker", category="unsupported_employer", message=f"Unsupported employer: {exp.employer}"))
        if exp.title not in allowed_titles: findings.append(AuditFinding(severity="blocker", category="unsupported_title", message=f"Unsupported title: {exp.title}"))
        if not exp.start_date or not exp.end_date: findings.append(AuditFinding(severity="blocker", category="missing_dates", message=f"Missing dates for {exp.employer}"))
        prov_by_text = {p.text: p for p in exp.bullet_provenance}
        for b in exp.bullets:
            if b.lower() in seen: findings.append(AuditFinding(severity="blocker", category="duplicate_bullet", message="Duplicate resume bullet.", referenced_text=b))
            seen.append(b.lower())
            prov = prov_by_text.get(b)
            if not prov or not prov.statement_ids: findings.append(AuditFinding(severity="blocker", category="missing_provenance", message="Bullet lacks evidence provenance.", referenced_text=b))
            if not prov or b.lower() not in allowed_text: findings.append(AuditFinding(severity="blocker", category="unsupported_bullet", message="Bullet text is not verified evidence.", referenced_text=b))
            if prov and not any(s in allowed_statements for s in prov.statement_ids): findings.append(AuditFinding(severity="blocker", category="unsupported_bullet", message="Bullet provenance references unknown evidence.", referenced_text=b))
            nums = re.findall(r"\b\d+(?:\.\d+)?%?\b", b)
            if nums and not any(n in allowed_text for n in nums): findings.append(AuditFinding(severity="blocker", category="unsupported_metric", message="Bullet contains unsupported metric.", referenced_text=b))
    for tech in job.required_technologies + job.preferred_technologies + resume.competencies:
        if tech and tech.lower() in text and tech.lower() not in allowed_text:
            findings.append(AuditFinding(severity="blocker", category="unsupported_technology", message=f"Unsupported technology/competency: {tech}", referenced_text=tech))
    if len(resume.competencies) > 18:
        findings.append(AuditFinding(severity="warning", category="competency_stuffing", message="Too many competencies for a targeted resume."))
    if resume.status == "DRAFT_INCOMPLETE":
        findings.append(AuditFinding(severity="blocker", category="draft_incomplete", message="Resume is draft-only until private contact information is complete."))
    return FactualConsistencyAudit(job_id=job.source_job_id, passed=not any(f.severity == "blocker" for f in findings), findings=findings)
