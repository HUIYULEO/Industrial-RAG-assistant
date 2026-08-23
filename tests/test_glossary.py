from app.core.glossary import find_relevant_terms, terminology_context


def test_finds_bilingual_terms_and_formats_prompt_context():
    context = terminology_context("仓控系统 WCS 如何协调 AGV？")

    assert "Warehouse Control System" in context
    assert "Automated Guided Vehicle" in context
    assert "not supplier-document evidence" in context


def test_does_not_match_abbreviation_inside_an_unrelated_word():
    terms = find_relevant_terms("This office workflow has no warehouse terminology.")

    assert terms == []


def test_respects_result_limit():
    terms = find_relevant_terms("WMS, WES, WCS and AGV", limit=2)

    assert [term.canonical for term in terms] == ["WCS", "WES"]
