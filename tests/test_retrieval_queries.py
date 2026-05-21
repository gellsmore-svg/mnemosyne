from datetime import datetime, timezone

from mnemosyne.retrieval.queries import (
    build_prompt_envelope,
    build_prompt_envelope_without_context,
    context_record,
    default_no_context_system_instruction,
    estimate_tokens,
    nearby_siblings,
    node_search_score,
    node_search_sort_key,
    parse_iso_datetime,
    prioritize_records,
    render_context_document,
    render_record,
    serialize_document,
    serialize_node,
    text_query_filters,
    text_query_terms,
)


def test_serialize_document_handles_source_and_dates() -> None:
    document = {
        "_id": "doc1",
        "schema_version": 1,
        "title": "Title",
        "summary": "Summary",
        "source": {"path": "source.md"},
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }

    serialized = serialize_document(document)

    assert serialized["document_id"] == "doc1"
    assert serialized["source"]["path"] == "source.md"
    assert serialized["created_at"] == "2026-01-01T00:00:00+00:00"


def test_serialize_node_returns_preview_and_ids() -> None:
    last_used_at = datetime(2026, 1, 3, tzinfo=timezone.utc)
    node = {
        "_id": "node1",
        "document_id": "doc1",
        "tree_id": "tree1",
        "parent_id": None,
        "node_key": "root",
        "parent_key": None,
        "title": "Title",
        "text": "x" * 400,
        "labels": ["source_root"],
        "endorsement_label": "unreviewed",
        "usage_score": 7,
        "last_used_at": last_used_at,
        "provenance": {"adapter": "mock"},
        "created_at": None,
    }

    serialized = serialize_node(node)

    assert serialized["node_id"] == "node1"
    assert serialized["document_id"] == "doc1"
    assert serialized["text_preview"] == "x" * 300
    assert serialized["usage_score"] == 7
    assert serialized["last_used_at"] == "2026-01-03T00:00:00+00:00"


def test_serialize_node_bounds_malformed_usage_score() -> None:
    node = {
        "_id": "node1",
        "document_id": "doc1",
        "tree_id": "tree1",
        "title": "Title",
        "text": "text",
        "usage_score": "many",
    }

    assert serialize_node(node)["usage_score"] == 0


def test_parse_iso_datetime_accepts_z_suffix() -> None:
    parsed = parse_iso_datetime("2026-01-01T00:00:00Z")

    assert parsed.isoformat() == "2026-01-01T00:00:00+00:00"


def test_text_query_terms_extracts_natural_query_content_terms() -> None:
    assert text_query_terms(
        "Who commissioned the Taj Mahal, and who was it built to commemorate?"
    ) == ["commissioned", "Taj", "Mahal", "built", "commemorate"]


def test_text_query_filters_combines_exact_query_and_content_terms() -> None:
    filters = text_query_filters("Who commissioned the Taj Mahal?")

    assert len(filters) == 12
    assert [set(query_filter) for query_filter in filters] == [
        {"title"},
        {"text"},
        {"labels"},
        {"title"},
        {"text"},
        {"labels"},
        {"title"},
        {"text"},
        {"labels"},
        {"title"},
        {"text"},
        {"labels"},
    ]
    assert filters[0]["title"].pattern == "Who\\ commissioned\\ the\\ Taj\\ Mahal\\?"
    assert filters[3]["title"].pattern == "\\bcommissioned\\b"
    assert filters[6]["title"].pattern == "\\bTaj\\b"
    assert filters[9]["title"].pattern == "\\bMahal\\b"


def test_text_query_filters_deduplicates_single_term_query() -> None:
    assert len(text_query_filters("Mahal")) == 3


def test_node_search_score_uses_whole_terms_for_query_tokens() -> None:
    exact_term = {
        "title": "Construction note",
        "text": "The tomb was built as an object.",
        "labels": ["source_chunk"],
    }
    substring_only = {
        "title": "Construction note",
        "text": "The tomb was rebuilt as an object.",
        "labels": ["source_chunk"],
    }

    assert node_search_score(exact_term, "built object") > node_search_score(
        substring_only,
        "built object",
    )


def test_context_record_adds_role_and_distance() -> None:
    node = {
        "_id": "node1",
        "document_id": "doc1",
        "tree_id": "tree1",
        "parent_id": None,
        "title": "Title",
        "text": "Body",
    }

    record = context_record("focus", node, 0)

    assert record["role"] == "focus"
    assert record["distance"] == 0
    assert record["node_id"] == "node1"


class FakeCursor(list):
    def sort(self, *_args):
        return self


class FakeNodes:
    def __init__(self, nodes):
        self.nodes = nodes

    def find(self, query):
        return FakeCursor(
            [node for node in self.nodes if node.get("parent_id") == query["parent_id"]]
        )


class FakeDb:
    def __init__(self, nodes):
        self.nodes = FakeNodes(nodes)


def test_nearby_siblings_uses_sibling_position_not_order_delta() -> None:
    nodes = [
        {"_id": "a", "parent_id": "root", "order": 1},
        {"_id": "b", "parent_id": "root", "order": 4},
        {"_id": "c", "parent_id": "root", "order": 9},
    ]

    siblings = nearby_siblings(FakeDb(nodes), nodes[1], window=1)

    assert [sibling["_id"] for sibling in siblings] == ["a", "c"]


def test_render_record_includes_role_title_and_source() -> None:
    block = render_record(
        {
            "role": "focus",
            "distance": 0,
            "title": "Title",
            "node_id": "node1",
            "labels": ["source_chunk"],
            "endorsement_label": "unreviewed",
            "provenance": {"archive_path": "archive.md"},
            "text_preview": "Body",
        }
    )

    assert "### focus d=0: Title" in block
    assert "archive.md" in block
    assert "Body" in block


def test_render_context_document_enforces_char_budget() -> None:
    context = {
        "document": {"title": "Doc", "document_id": "doc1"},
        "focus_node_id": "node1",
        "records": [
            {
                "role": "focus",
                "distance": 0,
                "title": "A",
                "node_id": "node1",
                "labels": [],
                "endorsement_label": "unreviewed",
                "provenance": {},
                "text_preview": "short",
            },
            {
                "role": "descendant",
                "distance": 1,
                "title": "B",
                "node_id": "node2",
                "labels": [],
                "endorsement_label": "unreviewed",
                "provenance": {},
                "text_preview": "x" * 1000,
            },
        ],
    }

    rendered = render_context_document(context, char_budget=350)

    assert len(rendered["included"]) == 1
    assert rendered["skipped"][0]["node_id"] == "node2"


def test_prioritize_records_keeps_focus_before_descendants_and_ancestors() -> None:
    records = [
        {"role": "ancestor", "distance": 1, "title": "A"},
        {"role": "descendant", "distance": 1, "title": "B"},
        {"role": "focus", "distance": 0, "title": "C"},
    ]

    ordered = prioritize_records(records)

    assert [record["role"] for record in ordered] == ["focus", "descendant", "ancestor"]


def test_estimate_tokens_uses_four_char_approximation() -> None:
    assert estimate_tokens("12345") == 2


def test_build_prompt_envelope_includes_query_budget_and_context() -> None:
    context = {
        "document": {"title": "Doc", "document_id": "doc1"},
        "focus_node_id": "node1",
        "records": [
            {
                "role": "focus",
                "distance": 0,
                "title": "A",
                "node_id": "node1",
                "labels": [],
                "endorsement_label": "unreviewed",
                "provenance": {},
                "text_preview": "context body",
            },
        ],
    }

    envelope = build_prompt_envelope(
        context,
        query="What matters?",
        token_budget=200,
        reserved_response_tokens=25,
    )

    assert envelope["query"] == "What matters?"
    assert "context body" in envelope["prompt_text"]
    assert envelope["budget"]["available_context_tokens"] < 175
    assert envelope["budget"]["estimated_total_with_reserved_response_tokens"] <= 200


def test_render_context_document_uses_full_context_text() -> None:
    long_text = "A" * 350 + " full-tail"
    rendered = render_context_document(
        {
            "document": {"title": "Doc", "document_id": "doc1"},
            "focus_node_id": "node1",
            "records": [
                {
                    "role": "focus",
                    "distance": 0,
                    "title": "A",
                    "node_id": "node1",
                    "labels": [],
                    "endorsement_label": "unreviewed",
                    "provenance": {},
                    "text_preview": long_text[:300],
                    "text": long_text,
                },
            ],
        },
        char_budget=1000,
    )

    assert "full-tail" in rendered["text"]


def test_build_prompt_envelope_without_context_uses_no_context_instruction() -> None:
    envelope = build_prompt_envelope_without_context("What is stored?")

    assert envelope["system_instruction"] == default_no_context_system_instruction()
    assert "No retrieved Mongo context matched this request." in envelope["prompt_text"]
    assert "- Matching Mongo context used: no" in envelope["prompt_text"]
    assert "Treat the Runtime Facts as the source of truth" in envelope["prompt_text"]
    assert "For this request, Mongo lookup ran but no matching Mongo context was used." in envelope["prompt_text"]
    assert "Do not withhold useful general answers" in envelope["prompt_text"]
    assert envelope["context_metadata"]["included"] == []


def test_node_search_score_demotes_empty_title_only_chunks() -> None:
    empty_chunk = {
        "title": "AMS Substrate Coherence Schema v1 / paragraph 1",
        "text": "",
        "labels": ["source_chunk"],
    }
    useful_section = {
        "title": "AMS Substrate Coherence Schema v1",
        "text": "Purpose and shared fields for the substrate coherence programme.",
        "labels": ["source_section"],
    }

    assert node_search_score(useful_section, "substrate coherence schema") > node_search_score(
        empty_chunk,
        "substrate coherence schema",
    )


def test_node_search_score_prefers_human_endorsed_nodes() -> None:
    unreviewed = {
        "title": "Memory note",
        "text": "memory context",
        "labels": ["source_chunk"],
        "endorsement_label": "unreviewed",
    }
    endorsed = {
        **unreviewed,
        "endorsement_label": "explicit_endorsed",
    }

    assert node_search_score(endorsed, "memory") > node_search_score(unreviewed, "memory")


def test_node_search_score_penalizes_rejected_nodes() -> None:
    unreviewed = {
        "title": "Memory note",
        "text": "memory context",
        "labels": ["source_chunk"],
        "endorsement_label": "unreviewed",
    }
    rejected = {
        **unreviewed,
        "endorsement_label": "rejected",
    }

    assert node_search_score(unreviewed, "memory") > node_search_score(rejected, "memory")


def test_node_search_score_penalizes_unreviewed_generated_output() -> None:
    source = {
        "title": "Memory note",
        "text": "memory context",
        "labels": ["source_chunk"],
        "endorsement_label": "unreviewed",
    }
    generated = {
        **source,
        "labels": ["source_chunk", "generated_output"],
    }

    assert node_search_score(source, "memory") > node_search_score(generated, "memory")


def test_node_search_score_adds_bounded_usage_signal() -> None:
    unused = {
        "title": "Memory note",
        "text": "memory context",
        "labels": ["source_chunk"],
        "endorsement_label": "unreviewed",
        "usage_score": 0,
    }
    heavily_used = {
        **unused,
        "usage_score": 50,
    }

    assert node_search_score(heavily_used, "memory") == node_search_score(unused, "memory") + 10


def test_usage_signal_does_not_outweigh_rejection_penalty() -> None:
    unreviewed = {
        "title": "Memory note",
        "text": "memory context",
        "labels": ["source_chunk"],
        "endorsement_label": "unreviewed",
    }
    rejected_used = {
        **unreviewed,
        "endorsement_label": "rejected",
        "usage_score": 100,
    }

    assert node_search_score(unreviewed, "memory") > node_search_score(rejected_used, "memory")


def test_node_search_sort_key_uses_last_used_at_after_score_ties() -> None:
    older = {
        "title": "Memory note",
        "text": "memory context",
        "labels": ["source_chunk"],
        "last_used_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    newer = {
        **older,
        "last_used_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }

    assert node_search_sort_key(newer, "memory") > node_search_sort_key(older, "memory")


def test_node_search_score_prefers_specific_chunk_over_large_container_section() -> None:
    query = "Taj Mahal commissioned Shah Jahan Mumtaz"
    large_section = {
        "title": "Taj Mahal - Wikipedia Extract",
        "text": "Taj Mahal commissioned Shah Jahan Mumtaz " + ("background " * 400),
        "labels": ["source_section"],
    }
    specific_chunk = {
        "title": "Taj Mahal - Wikipedia Extract / paragraph 6",
        "text": "The Taj Mahal was commissioned by Shah Jahan for Mumtaz Mahal.",
        "labels": ["source_chunk"],
    }

    assert node_search_score(specific_chunk, query) > node_search_score(
        large_section,
        query,
    )


def test_node_search_sort_key_breaks_score_ties_toward_compact_nodes() -> None:
    concise = {
        "title": "Taj Mahal - Wikipedia Extract / paragraph 6",
        "text": "The Taj Mahal was commissioned by Shah Jahan for Mumtaz Mahal.",
        "labels": ["source_chunk"],
    }
    broad = {
        **concise,
        "text": concise["text"] + (" Construction details." * 100),
    }

    assert node_search_score(concise, "Taj Mahal commissioned Mumtaz") == node_search_score(
        broad,
        "Taj Mahal commissioned Mumtaz",
    )
    assert node_search_sort_key(concise, "Taj Mahal commissioned Mumtaz") > node_search_sort_key(
        broad,
        "Taj Mahal commissioned Mumtaz",
    )
