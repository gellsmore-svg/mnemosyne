from tirzah.semantic import (
    MahalathResolver,
    SemanticLabel,
    annotate,
    extract_candidate_terms,
    make_resolver,
    render_prompt_block,
    summarize_labels,
)


class FakeResolver:
    """Returns a label only for terms in its dict (case-insensitive)."""

    def __init__(self, table):
        self.table = {k.casefold(): v for k, v in table.items()}
        self.seen = None

    def resolve(self, terms):
        self.seen = list(terms)
        return [self.table[t.casefold()] for t in terms if t.casefold() in self.table]


def _label(term, label="MPL-004", canon="form (structure)", senses=("structural",), stale=False, kind="exact"):
    return SemanticLabel(term=term, mpl_label=label, canonical_term=canon,
                         senses=list(senses), match_kind=kind, is_stale=stale)


def test_extract_terms_orders_query_first_and_drops_stopwords():
    terms = extract_candidate_terms("what is form", "The spirit of the law and the form.")
    assert "form" in terms and "spirit" in terms
    assert "the" not in terms and "what" not in terms  # stopwords gone
    assert terms.index("form") < terms.index("spirit")  # query read first


def test_extract_keeps_quoted_phrases_whole():
    terms = extract_candidate_terms('explain "topological form"', "")
    assert "topological form" in terms


def test_extract_respects_limit():
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu"
    assert len(extract_candidate_terms(words, "", limit=5)) == 5


def test_annotate_resolves_and_drops_stale_and_empty():
    resolver = FakeResolver({
        "form": _label("form"),
        "spirit": _label("spirit", label="MPL-009", senses=("theological",), stale=True),  # dropped: stale
    })
    labels = annotate("what is form and spirit", "the form of the spirit", resolver)
    assert [l.term for l in labels] == ["form"]
    assert "form" in resolver.seen and "spirit" in resolver.seen  # both offered to resolver


def test_annotate_no_terms_returns_empty_without_calling_resolver():
    resolver = FakeResolver({})
    assert annotate("", "", resolver) == []
    assert resolver.seen is None


def test_render_prompt_block_and_summary():
    labels = [_label("form", "MPL-004", "form (structure)", ("structural", "logical"))]
    block = render_prompt_block(labels)
    assert "## Semantic Precision (MPL)" in block
    assert "[MPL-004]" in block and "form (structure)" in block and "structural" in block
    assert summarize_labels(labels) == "interpreted as: form→MPL-004 (structural)"


def test_render_empty_is_blank():
    assert render_prompt_block([]) == ""
    assert summarize_labels([]) == ""


def test_strict_drops_fuzzy_matches_but_keeps_strong():
    resolver = FakeResolver({
        "form": _label("form", kind="exact"),
        "spirit": _label("spirit", label="MPL-009", senses=("theological",), kind="text"),  # fuzzy
    })
    loose = annotate("form spirit", "", resolver)
    assert {l.term for l in loose} == {"form", "spirit"}        # fuzzy kept by default
    strict = annotate("form spirit", "", resolver, strict=True)
    assert {l.term for l in strict} == {"form"}                  # fuzzy dropped under strict


def test_fuzzy_match_flagged_approx_in_prompt_block():
    fuzzy = [_label("spirit", label="MPL-009", senses=("theological",), kind="partial")]
    block = render_prompt_block(fuzzy)
    assert "approx match" in block
    strong = [_label("form", kind="exact")]
    assert "approx match" not in render_prompt_block(strong)


def test_make_resolver_disabled_returns_none():
    class Cfg:
        mahalath_enabled = False
    assert make_resolver(Cfg()) is None


def test_make_resolver_enabled_builds_mahalath_resolver():
    class Cfg:
        mahalath_enabled = True
        mahalath_mongo_uri = "mongodb://localhost:27017"
        mahalath_mongo_db = "mahalath_dev"
        mahalath_language = "en"
    r = make_resolver(Cfg())
    assert isinstance(r, MahalathResolver) and r.database == "mahalath_dev"


def test_mahalath_resolver_fails_soft_when_unavailable():
    # Unresolvable host + (likely) no mahalath package -> no exception, just [].
    r = MahalathResolver(mongo_uri="mongodb://198.51.100.9:1/", per_term_timeout_ms=200)
    assert r.resolve(["form"]) == []
