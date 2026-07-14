from job_agent.title_matching import canonicalize_title, title_match_score


def test_canonicalizes_senior_product_designer_variation():
    assert canonicalize_title("Sr. Product Designer") == "Senior Product Designer"


def test_semantic_title_variation_scores_high():
    score = title_match_score("Senior Product Designer, Design Systems", ["Senior Product Designer"], {"Senior Product Designer": ["Sr. Product Designer"]})
    assert score >= 80
