from datetime import datetime, timezone

from mnemosyne.retrieval.queries import (
    build_prompt_envelope,
    build_prompt_envelope_without_context,
    context_record,
    default_no_context_system_instruction,
    estimate_tokens,
    nearby_siblings,
    parse_iso_datetime,
    prioritize_records,
    render_context_document,
    render_record,
    serialize_document,
    serialize_node,
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
        "provenance": {"adapter": "mock"},
        "created_at": None,
    }

    serialized = serialize_node(node)

    assert serialized["node_id"] == "node1"
    assert serialized["document_id"] == "doc1"
    assert serialized["text_preview"] == "x" * 300


def test_parse_iso_datetime_accepts_z_suffix() -> None:
    parsed = parse_iso_datetime("2026-01-01T00:00:00Z")

    assert parsed.isoformat() == "2026-01-01T00:00:00+00:00"


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
