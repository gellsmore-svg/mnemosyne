import json
import sys
from pathlib import Path
from types import SimpleNamespace

from bson import ObjectId

from mnemosyne.cli import (
    chronological_folder_source_plan,
    discover_folder_sources,
    document_ids_for_label,
    destructive_rebuild_refusal,
    existing_document_extra_labels,
    main,
    rebuild_document_from_existing_source,
)


def test_discover_folder_sources_finds_markdown_and_text(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "a.md").write_text("a", encoding="utf-8")
    (root / "a2.markdown").write_text("a2", encoding="utf-8")
    (root / "b.txt").write_text("b", encoding="utf-8")
    (root / "c.json").write_text("{}", encoding="utf-8")

    assert [path.name for path in discover_folder_sources(root)] == ["a.md", "a2.markdown", "b.txt"]


def test_discover_folder_sources_skips_git_directory(tmp_path: Path) -> None:
    root = tmp_path / "source"
    git_dir = root / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "ignored.md").write_text("ignored", encoding="utf-8")
    (root / "included.md").write_text("included", encoding="utf-8")

    assert [path.name for path in discover_folder_sources(root)] == ["included.md"]


def test_chronological_folder_source_plan_orders_by_origin_date(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    late_filename = root / "source-2026.md"
    explicit_earlier = root / "source-2024.md"
    undated = root / "undated.md"
    late_filename.write_text("No explicit date.", encoding="utf-8")
    explicit_earlier.write_text("Date: 2020\n\nEarlier content.", encoding="utf-8")
    undated.write_text("No date marker.", encoding="utf-8")

    plan = chronological_folder_source_plan(root)

    assert [item["path"].name for item in plan] == [
        "source-2024.md",
        "source-2026.md",
        "undated.md",
    ]
    assert plan[0]["origin_date"] == "2020-01-01"
    assert plan[0]["origin_date_source"] == "explicit_content"


def test_chronological_folder_source_plan_keeps_unreadable_file_as_error(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    readable = root / "readable.md"
    unreadable = root / "unreadable.md"
    readable.write_text("Date: 2020\n\nReadable.", encoding="utf-8")
    unreadable.write_bytes(b"\xff\xfe\x00\x00")

    plan = chronological_folder_source_plan(root)

    assert [item["path"].name for item in plan] == ["readable.md", "unreadable.md"]
    assert plan[1]["error"] == "UnicodeDecodeError"
    assert plan[1]["origin_date"] is None


def test_existing_document_extra_labels_excludes_structural_labels() -> None:
    document_id = ObjectId()
    db = FakeDb(
        [
            {"document_id": document_id, "labels": ["source_root", "memory_reference"]},
            {"document_id": document_id, "labels": ["source_chunk", "external_corpus"]},
        ]
    )

    assert existing_document_extra_labels(db, str(document_id)) == [
        "external_corpus",
        "memory_reference",
    ]


def test_document_ids_for_label_returns_sorted_strings() -> None:
    first = ObjectId()
    second = ObjectId()
    db = FakeDb(
        [
            {"document_id": second, "labels": ["ams_domain"]},
            {"document_id": first, "labels": ["ams_domain"]},
            {"document_id": ObjectId(), "labels": ["other"]},
        ]
    )

    assert document_ids_for_label(db, "ams_domain") == sorted([str(first), str(second)])


def test_destructive_rebuild_refusal_explains_force_replace() -> None:
    refusal = destructive_rebuild_refusal("rebuild-document")

    assert refusal["ok"] is False
    assert refusal["reason"] == "destructive_rebuild_requires_force_replace"
    assert "--force-replace" in refusal["message"]


def test_rebuild_document_uses_original_source_path_for_adapter_title(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive = tmp_path / "abc123.txt"
    archive.write_text("plain text", encoding="utf-8")
    document_id = ObjectId()
    db = FakeDb(
        [],
        document={
            "document_id": str(document_id),
            "source": {
                "path": "data/ingest/original-name.txt",
                "archive_path": str(archive),
                "checksum_sha256": "abc123",
            },
        },
    )
    captured = {}

    def fake_rebuild(_db, _document_id, result):
        captured["title"] = result.title
        return {"document_id": str(document_id), "replaced": True}

    monkeypatch.setattr("mnemosyne.cli.get_document", lambda _db, _document_id: db.document)
    monkeypatch.setattr("mnemosyne.cli.rebuild_document", fake_rebuild)

    result = rebuild_document_from_existing_source(db, str(document_id))

    assert result["ok"] is True
    assert captured["title"] == "original-name"


def test_cli_graph_edges_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["mnemosyne", "graph-edges", "node1", "--limit", "2"])
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.graph_edges_for_node",
        lambda _db, node_id, direction="both", relation_type=None, limit=10: [
            {"node_id": node_id, "direction": direction, "limit": limit}
        ],
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "edges": [{"node_id": "node1", "direction": "both", "limit": 2}],
    }


def test_cli_backfill_structural_graph_edges_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["mnemosyne", "backfill-structural-graph-edges", "--limit", "7"],
    )
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.backfill_structural_graph_edges",
        lambda _db, limit=None: {
            "scanned_node_count": limit,
            "edge_count": 3,
            "skipped_existing_count": 4,
            "skipped_missing_parent_count": 0,
        },
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "scanned_node_count": 7,
        "edge_count": 3,
        "skipped_existing_count": 4,
        "skipped_missing_parent_count": 0,
    }


def test_cli_graph_status_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["mnemosyne", "graph-status", "--limit", "3"])
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.graph_edge_status",
        lambda _db, limit=10: {
            "edge_count": 12,
            "relation_types": [{"value": "contains", "count": 12}],
            "provenance_sources": [{"value": "node_parent_link", "count": 12}],
            "limit": limit,
        },
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "edge_count": 12,
        "relation_types": [{"value": "contains", "count": 12}],
        "provenance_sources": [{"value": "node_parent_link", "count": 12}],
        "limit": 3,
    }


def test_cli_expand_proximity_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["mnemosyne", "expand-proximity", "node1", "--direction", "incoming"],
    )
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.expand_proximity",
        lambda _db, node_id, direction="both", relation_type=None, limit=10: [
            {"node_id": node_id, "direction": direction, "limit": limit}
        ],
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "nodes": [{"node_id": "node1", "direction": "incoming", "limit": 10}],
    }


def test_cli_expand_graph_paths_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mnemosyne",
            "expand-graph-paths",
            "node1",
            "--direction",
            "outgoing",
            "--max-depth",
            "3",
        ],
    )
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)

    def fake_expand_graph_paths(
        _db,
        node_id,
        direction="both",
        relation_type=None,
        max_depth=2,
        branch_limit=5,
        limit=10,
    ):
        return [
            {
                "node_id": node_id,
                "direction": direction,
                "max_depth": max_depth,
                "branch_limit": branch_limit,
                "limit": limit,
            }
        ]

    monkeypatch.setattr("mnemosyne.cli.expand_graph_paths", fake_expand_graph_paths)

    main()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "paths": [
            {
                "node_id": "node1",
                "direction": "outgoing",
                "max_depth": 3,
                "branch_limit": 5,
                "limit": 10,
            }
        ],
    }


def test_cli_semantic_candidates_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["mnemosyne", "semantic-candidates", "node1", "--include-same-document"],
    )
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.semantic_candidate_nodes",
        lambda _db, node_id, limit=10, include_same_document=False: [
            {
                "node_id": node_id,
                "limit": limit,
                "include_same_document": include_same_document,
            }
        ],
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "nodes": [
            {
                "node_id": "node1",
                "limit": 10,
                "include_same_document": True,
            }
        ],
    }


def test_cli_create_semantic_edge_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mnemosyne",
            "create-semantic-edge",
            "source1",
            "target1",
            "--relation-type",
            "supports",
            "--weight",
            "0.8",
            "--confidence",
            "0.9",
            "--reviewer",
            "cello",
            "--note",
            "Reviewed candidate.",
        ],
    )
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.create_reviewed_semantic_edge",
        lambda _db, **kwargs: {"ok": True, "edge": kwargs},
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "edge": {
            "source_node_id": "source1",
            "target_node_id": "target1",
            "relation_type": "supports",
            "weight": 0.8,
            "confidence": 0.9,
            "reviewer": "cello",
            "note": "Reviewed candidate.",
        },
    }


def test_cli_enqueue_semantic_candidates_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mnemosyne",
            "enqueue-semantic-candidates",
            "node1",
            "--include-same-document",
            "--relation-type",
            "supports",
            "--created-by",
            "cello",
            "--limit",
            "3",
        ],
    )
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.enqueue_semantic_edge_candidates",
        lambda _db, **kwargs: {"ok": True, **kwargs},
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "node_id": "node1",
        "include_same_document": True,
        "relation_type": "supports",
        "created_by": "cello",
        "limit": 3,
    }


def test_cli_vector_semantic_candidates_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mnemosyne",
            "vector-semantic-candidates",
            "node1",
            "--include-same-document",
            "--min-similarity",
            "0.82",
            "--limit",
            "3",
        ],
    )
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.embedding_candidate_nodes",
        lambda _db, node_id, **kwargs: [{"node_id": node_id, **kwargs}],
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "nodes": [
            {
                "node_id": "node1",
                "include_same_document": True,
                "min_similarity": 0.82,
                "limit": 3,
            }
        ],
    }


def test_cli_enqueue_vector_semantic_candidates_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mnemosyne",
            "enqueue-vector-semantic-candidates",
            "node1",
            "--include-same-document",
            "--relation-type",
            "supports",
            "--created-by",
            "cello",
            "--min-similarity",
            "0.82",
            "--limit",
            "3",
        ],
    )
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.enqueue_vector_semantic_edge_candidates",
        lambda _db, **kwargs: {"ok": True, **kwargs},
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "node_id": "node1",
        "include_same_document": True,
        "relation_type": "supports",
        "created_by": "cello",
        "min_similarity": 0.82,
        "limit": 3,
    }


def test_cli_semantic_edge_candidates_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["mnemosyne", "semantic-edge-candidates", "--status", "pending", "--limit", "4"],
    )
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.list_semantic_edge_candidates",
        lambda _db, status="pending", limit=20: [{"status": status, "limit": limit}],
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "candidates": [{"status": "pending", "limit": 4}],
    }


def test_cli_review_semantic_edge_candidate_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mnemosyne",
            "review-semantic-edge-candidate",
            "candidate1",
            "--action",
            "accept",
            "--reviewer",
            "cello",
            "--note",
            "Reviewed candidate.",
            "--weight",
            "0.8",
            "--confidence",
            "0.9",
        ],
    )
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.review_semantic_edge_candidate",
        lambda _db, **kwargs: {"ok": True, **kwargs},
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "candidate_id": "candidate1",
        "action": "accept",
        "reviewer": "cello",
        "note": "Reviewed candidate.",
        "weight": 0.8,
        "confidence": 0.9,
    }


def test_cli_agent_identities_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["mnemosyne", "agent-identities", "--limit", "3"])
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.list_agent_identities",
        lambda _db, limit=20: [{"identity_id": "mnemosyne_shared", "limit": limit}],
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "identities": [{"identity_id": "mnemosyne_shared", "limit": 3}],
    }


def test_cli_agent_identity_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["mnemosyne", "agent-identity", "mnemosyne_shared"])
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.get_agent_identity",
        lambda _db, identity_id: {"identity_id": identity_id},
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "identity": {"identity_id": "mnemosyne_shared"},
    }


def test_cli_trust_weighting_profiles_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["mnemosyne", "trust-weighting-profiles", "--limit", "2"],
    )
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.list_trust_weighting_profiles",
        lambda _db, limit=20: [{"weighting_profile_id": "default_balanced", "limit": limit}],
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "profiles": [{"weighting_profile_id": "default_balanced", "limit": 2}],
    }


def test_cli_trust_weighting_profile_command_reports_missing(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["mnemosyne", "trust-weighting-profile", "missing"],
    )
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.get_trust_weighting_profile",
        lambda _db, weighting_profile_id: None,
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": False,
        "profile": None,
    }


def test_cli_trust_diagnostic_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["mnemosyne", "trust-diagnostic", "node1", "--profile-id", "default_balanced"],
    )
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.trust_temporal_diagnostic_for_node",
        lambda _db, node_id, weighting_profile_id=None: {
            "node_id": node_id,
            "profile_id": weighting_profile_id,
        },
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "result": {"node_id": "node1", "profile_id": "default_balanced"},
    }


def test_cli_process_runs_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["mnemosyne", "process-runs", "--session-id", "s1", "--status", "active", "--limit", "2"],
    )
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.list_process_runs",
        lambda _db, session_id=None, status=None, limit=20: [
            {"run_id": "run1", "session_id": session_id, "status": status, "limit": limit}
        ],
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "runs": [{"run_id": "run1", "session_id": "s1", "status": "active", "limit": 2}],
    }


def test_cli_start_process_run_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mnemosyne",
            "start-process-run",
            "restart_continuity",
            "--session-id",
            "s1",
            "--identity-id",
            "mnemosyne_shared",
            "--current-step-id",
            "inspect_state",
        ],
    )
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.create_process_run",
        lambda _db, **kwargs: {"run_id": "run1", **kwargs},
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["run"]["process_id"] == "restart_continuity"
    assert output["run"]["current_step_id"] == "inspect_state"


def test_cli_update_process_run_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mnemosyne",
            "update-process-run",
            "run1",
            "--status",
            "exception_requested",
            "--completed-step-id",
            "inspect_state",
            "--exception-reason",
            "better path",
            "--exception-proposal",
            "skip duplicate step",
        ],
    )
    monkeypatch.setattr(
        "mnemosyne.cli.load_config",
        lambda _path: SimpleNamespace(mongo=SimpleNamespace()),
    )
    monkeypatch.setattr("mnemosyne.cli.get_database", lambda _config: "db")
    monkeypatch.setattr("mnemosyne.cli.ensure_indexes", lambda _db: None)
    monkeypatch.setattr(
        "mnemosyne.cli.update_process_run",
        lambda _db, run_id, **kwargs: {"run_id": run_id, **kwargs},
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["run"]["run_id"] == "run1"
    assert output["run"]["status"] == "exception_requested"
    assert output["run"]["exception"]["reason"] == "better path"


class FakeCollection:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def find(self, query: dict, _projection: dict) -> list[dict]:
        return [row for row in self.rows if row["document_id"] == query["document_id"]]

    def distinct(self, field: str, query: dict) -> list:
        values = []
        for row in self.rows:
            if query["labels"] not in row.get("labels", []):
                continue
            value = row[field]
            if value not in values:
                values.append(value)
        return values


class FakeDb:
    def __init__(self, nodes: list[dict], document: dict | None = None) -> None:
        self.nodes = FakeCollection(nodes)
        self.document = document
