from __future__ import annotations
from job_agent.models import AuditFinding, ExperienceEvidence, FactualConsistencyAudit, JobPosting, StructuredResume


def audit_resume(job: JobPosting, evidence: list[ExperienceEvidence], resume: StructuredResume) -> FactualConsistencyAudit:
    allowed_text = "\n".join(
        [exp.employer + " " + exp.role + " " + " ".join(exp.skills + exp.technologies + exp.allowed_claims + exp.responsibilities + exp.accomplishments) for exp in evidence]
        + [ev.text + " " + " ".join(ev.skills + ev.technologies + ev.allowed_claims) for exp in evidence for ev in exp.all_evidence()]
    ).lower()
    prohibited = [p for exp in evidence for p in exp.prohibited_claims]
    findings: list[AuditFinding] = []
    resume_text = resume.model_dump_json().lower()
    for claim in prohibited:
        if claim.lower() in resume_text:
            findings.append(AuditFinding(severity="blocker", category="prohibited_claim", message=f"Resume contains prohibited claim: {claim}", referenced_text=claim))
    for tech in job.required_technologies + job.preferred_technologies:
        if tech.lower() in resume_text and tech.lower() not in allowed_text:
            findings.append(AuditFinding(severity="warning", category="unsupported_technology", message=f"Resume mentions job technology without verified evidence: {tech}", referenced_text=tech))
    return FactualConsistencyAudit(job_id=job.source_job_id, passed=not any(f.severity == "blocker" for f in findings), findings=findings)
