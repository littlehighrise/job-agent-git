"""Prompt architecture placeholders for future LLM-backed semantic stages.

The MVP keeps scoring deterministic and inspectable. These prompts define narrow future
LLM responsibilities rather than one giant autonomous prompt.
"""

GOVERNING_PRINCIPLES = """
You are the decision engine for an AI-assisted job application system.
Use only VERIFIED CANDIDATE EVIDENCE. Never invent, infer, embellish, or fabricate candidate experience.
For every matched job requirement, identify supporting evidence IDs. For every gap, identify missing evidence.
Separate explicit requirements from inferred preferences. Return structured JSON only.
"""

STAGE_PROMPTS = {
    "job_requirement_classification": GOVERNING_PRINCIPLES + "\nClassify job text into explicit requirements and inferred preferences.",
    "requirement_evidence_matching": GOVERNING_PRINCIPLES + "\nMatch each requirement to evidence IDs or mark unsupported/contradicted.",
    "resume_planning": GOVERNING_PRINCIPLES + "\nCreate a ResumeContentPlan preserving factual meaning and avoiding unsupported metrics or technologies.",
    "application_answer_generation": GOVERNING_PRINCIPLES + "\nDraft answers only for allowed question categories; flag legal attestations for user review unless pre-approved.",
    "factual_consistency_audit": GOVERNING_PRINCIPLES + "\nAudit job analysis, resume content, answers, and evidence for unsupported or overstated claims.",
}
