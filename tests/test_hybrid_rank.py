from tirzah.retrieval.queries import hybrid_rank, merge_candidate_pools


QUERY = "machine learning"


def lexical_strong(node_id="lex", **extra):
    # query in title (+50) + text (+15) + term hits + source_chunk → high lexical.
    return {
        "node_id": node_id,
        "title": "Machine Learning",
        "text": "machine learning basics",
        "labels": ["source_chunk"],
        **extra,
    }


def vector_only(node_id="vec", similarity=0.85):
    # no lexical overlap and no structural bonus → lexical 0; kept only by vector.
    return {
        "node_id": node_id,
        "title": "Quantum Physics",
        "text": "entirely unrelated content",
        "labels": [],
        "embedding_similarity": similarity,
    }


def irrelevant(node_id="out"):
    return {
        "node_id": node_id,
        "title": "Quantum Physics",
        "text": "entirely unrelated content",
        "labels": [],
    }


def ids(results):
    return [item["node"]["node_id"] for item in results]


def test_gate_keeps_lexical_or_vector_drops_irrelevant_and_rejected() -> None:
    rejected = lexical_strong(node_id="rej", endorsement_label="rejected")
    results = hybrid_rank([lexical_strong(), vector_only(), irrelevant(), rejected], QUERY)
    kept = set(ids(results))
    assert kept == {"lex", "vec"}  # irrelevant + rejected gated out


def test_default_blend_orders_strong_lexical_first() -> None:
    results = hybrid_rank([vector_only(), lexical_strong()], QUERY)
    assert ids(results) == ["lex", "vec"]
    top = results[0]
    # component scores are exposed for transparency / "why included".
    assert top["lexical_score"] > 0
    assert 0.0 <= top["hybrid_score"] <= 1.0
    assert "normalized_lexical" in top


def test_vector_weight_favors_similarity() -> None:
    results = hybrid_rank(
        [lexical_strong(), vector_only()], QUERY, lexical_weight=0.0, vector_weight=1.0
    )
    assert ids(results) == ["vec", "lex"]


def test_lexical_weight_favors_keywords() -> None:
    results = hybrid_rank(
        [vector_only(), lexical_strong()], QUERY, lexical_weight=1.0, vector_weight=0.0
    )
    assert ids(results) == ["lex", "vec"]


def test_limit_truncates_to_top_k() -> None:
    results = hybrid_rank([lexical_strong(), vector_only(), irrelevant()], QUERY, limit=1)
    assert ids(results) == ["lex"]


def test_empty_pool_returns_empty() -> None:
    assert hybrid_rank([], QUERY) == []


def test_merge_unions_by_id_and_carries_similarity() -> None:
    lexical_nodes = [{"node_id": "a", "title": "X", "text": "x"}]
    embedding_candidates = [
        {"node_id": "a", "title": "X", "text": "x", "embedding_similarity": 0.7},
        {"node_id": "b", "title": "Y", "text": "y", "embedding_similarity": 0.6},
    ]
    merged = merge_candidate_pools(lexical_nodes, embedding_candidates)
    assert [node["node_id"] for node in merged] == ["a", "b"]
    by_id = {node["node_id"]: node for node in merged}
    # lexical node 'a' wins for fields but keeps the embedding similarity.
    assert by_id["a"]["embedding_similarity"] == 0.7
    assert by_id["b"]["embedding_similarity"] == 0.6
