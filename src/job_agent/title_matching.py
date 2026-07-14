from __future__ import annotations

import re

SENIORITY_ALIASES = {
    "sr": "senior",
    "senior": "senior",
    "lead": "lead",
    "staff": "staff",
    "principal": "principal",
}
ROLE_FAMILIES = {
    "product_designer": {"product", "designer", "ux", "ui"},
    "ux_engineer": {"ux", "engineer", "frontend", "front-end"},
    "design_systems": {"design", "systems", "system"},
}


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower().replace("ui/ux", "ui ux")))


def canonicalize_title(title: str) -> str:
    t = tokens(title)
    seniority = next((v for k, v in SENIORITY_ALIASES.items() if k in t), None)
    if "designer" in t and ({"product"} & t or {"ux", "ui"} & t):
        family = "Product Designer"
    elif {"design", "systems"}.issubset(t) or {"design", "system"}.issubset(t):
        family = "Design Systems"
    elif "engineer" in t and "ux" in t:
        family = "UX Engineer"
    else:
        family = title.strip()
    return f"{seniority.title()} {family}" if seniority else family


def title_match_score(job_title: str, target_titles: list[str], configured_variations: dict[str, list[str]]) -> int:
    job_canon = canonicalize_title(job_title).lower()
    all_targets: list[str] = []
    for title in target_titles:
        all_targets.append(title)
        all_targets.extend(configured_variations.get(title, []))
    best = 0
    job_tokens = tokens(job_canon)
    for target in all_targets:
        target_canon = canonicalize_title(target).lower()
        if job_canon == target_canon:
            best = max(best, 100)
            continue
        target_tokens = tokens(target_canon)
        overlap = len(job_tokens & target_tokens) / max(len(job_tokens | target_tokens), 1)
        score = int(overlap * 100)
        if "designer" in job_tokens and "designer" in target_tokens:
            score += 15
        if ({"lead", "staff", "principal", "senior"} & job_tokens) and ({"lead", "staff", "principal", "senior"} & target_tokens):
            score += 10
        best = max(best, min(score, 95))
    return best
