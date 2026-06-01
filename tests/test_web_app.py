from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId
from fastapi.testclient import TestClient

from mnemosyne.config import load_config
from mnemosyne.db.client import get_database
from mnemosyne.web.app import (
    app,
    parse_ollama_model_list,
    parse_ollama_model_rows,
    process_inbox_activity_log,
)


def test_health_endpoint() -> None:
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_process_inbox_activity_log_prefers_human_summary() -> None:
    log = process_inbox_activity_log(
        [{"status": "pending"}, {"status": "rejected"}],
        [{"status": "completed", "activity_log": "Ingestion Activity Log\n- Status: completed."}],
    )

    assert log.startswith("Inbox Processing Activity Log")
    assert "Queue intake: 1 accepted, 1 rejected." in log
    assert "Run 1" in log
    assert "Status: completed." in log


def test_ingestion_status_endpoint_reports_epochs_and_runs(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.list_ingestion_epochs",
        lambda _db, limit=8: [
            {
                "ingestion_epoch": "epoch1",
                "document_count": 2,
                "dated_document_count": 1,
                "limit": limit,
            }
        ],
    )
    monkeypatch.setattr(
        "mnemosyne.web.app.list_process_runs",
        lambda _db, session_id=None, status=None, limit=20: [
            {"run_id": "run1", "session_id": session_id, "status": status, "limit": limit}
        ],
    )

    response = client.get("/api/ingestion/status", params={"limit": 4})

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "epochs": [
            {
                "ingestion_epoch": "epoch1",
                "document_count": 2,
                "dated_document_count": 1,
                "limit": 4,
            }
        ],
        "runs": [{"run_id": "run1", "session_id": "ingestion", "status": None, "limit": 4}],
    }


def test_index_serves_html() -> None:
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Mnemosyne" in response.text


def test_queue_endpoint() -> None:
    client = TestClient(app)

    response = client.get("/api/queue")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_runtime_endpoint_lists_llm_controls() -> None:
    client = TestClient(app)

    response = client.get("/api/runtime")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "ollama_cli" in data["available_adapters"]
    assert data["default_model"]
    assert data["memory_agent_model"]
    assert "gemma4:latest" in data["known_models"]
    assert data["model_options"]


def test_parse_ollama_model_list_returns_model_names() -> None:
    output = """NAME                    ID              SIZE      MODIFIED
gemma4:26b              5571076f3d70    17 GB     16 hours ago
qwen3.5:35b             3460ffeede54    23 GB     17 hours ago
mistral-small:latest    8039dd90c113    14 GB     17 hours ago
"""

    assert parse_ollama_model_list(output) == [
        "qwen3.5:35b",
        "gemma4:26b",
        "mistral-small:latest",
    ]


def test_parse_ollama_model_rows_sorts_largest_first_and_labels_size() -> None:
    output = """NAME                    ID              SIZE      MODIFIED
small:latest            aaa             815 MB    1 day ago
large:latest            bbb             23 GB     1 day ago
medium:latest           ccc             7.2 GB    1 day ago
"""

    rows = parse_ollama_model_rows(output)

    assert [row["name"] for row in rows] == [
        "large:latest",
        "medium:latest",
        "small:latest",
    ]
    assert [row["size_category"] for row in rows] == ["large", "medium", "small"]


def test_session_endpoints() -> None:
    client = TestClient(app)

    created = client.post(
        "/api/sessions",
        json={"title": "Web Test Session", "session_id": "web-test-session"},
    )
    listed = client.get("/api/sessions")

    assert created.status_code == 200
    assert created.json()["session"]["session_id"] == "web-test-session"
    assert listed.status_code == 200
    assert any(
        session["session_id"] == "web-test-session"
        for session in listed.json()["sessions"]
    )


def test_active_documents_endpoint_filters_by_session() -> None:
    client = TestClient(app)
    db = get_database(load_config().mongo)
    session_id = "web-active-documents-test"
    db.active_documents.delete_many({"session_id": session_id})
    now = datetime.now(timezone.utc)
    db.active_documents.insert_one(
        {
            "schema_version": 1,
            "session_id": session_id,
            "document_id": str(ObjectId()),
            "title": "Active Doc",
            "source": {"path": "active.md"},
            "labels": ["source_section"],
            "node_ids": [str(ObjectId())],
            "reference_count": 1,
            "first_referenced_at": now,
            "last_referenced_at": now,
        }
    )

    try:
        response = client.get(
            "/api/active-documents",
            params={"session_id": session_id, "limit": 5},
        )
    finally:
        db.active_documents.delete_many({"session_id": session_id})

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["session_id"] == session_id
    assert data["documents"][0]["title"] == "Active Doc"


def test_governance_agent_identities_endpoint(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.list_agent_identities",
        lambda _db, limit=20: [{"identity_id": "mnemosyne_shared", "limit": limit}],
    )

    response = client.get("/api/governance/agent-identities", params={"limit": 2})

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "identities": [{"identity_id": "mnemosyne_shared", "limit": 2}],
    }


def test_governance_agent_identity_endpoint(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.get_agent_identity",
        lambda _db, identity_id: {"identity_id": identity_id},
    )

    response = client.get("/api/governance/agent-identities/mnemosyne_shared")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "identity": {"identity_id": "mnemosyne_shared"},
    }


def test_governance_trust_weighting_profiles_endpoint(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.list_trust_weighting_profiles",
        lambda _db, limit=20: [{"weighting_profile_id": "default_balanced", "limit": limit}],
    )

    response = client.get("/api/governance/trust-weighting-profiles", params={"limit": 3})

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "profiles": [{"weighting_profile_id": "default_balanced", "limit": 3}],
    }


def test_governance_trust_weighting_profile_endpoint_reports_missing(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.get_trust_weighting_profile",
        lambda _db, weighting_profile_id: None,
    )

    response = client.get("/api/governance/trust-weighting-profiles/missing")

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "profile": None,
    }


def test_governance_trust_diagnostic_endpoint(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.trust_temporal_diagnostic_for_node",
        lambda _db, node_id, weighting_profile_id=None: {
            "node_id": node_id,
            "profile_id": weighting_profile_id,
        },
    )

    response = client.get(
        "/api/governance/trust-diagnostics/nodes/node1",
        params={"profile_id": "default_balanced"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "result": {"node_id": "node1", "profile_id": "default_balanced"},
    }


def test_governance_policies_endpoint(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.list_governance_policies",
        lambda _db, limit=20: [{"policy_id": "read_only", "limit": limit}],
    )

    response = client.get("/api/governance/policies", params={"limit": 4})

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "policies": [{"policy_id": "read_only", "limit": 4}],
    }


def test_governance_policy_endpoint(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.get_governance_policy",
        lambda _db, policy_id: {"policy_id": policy_id},
    )

    response = client.get("/api/governance/policies/read_only")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "policy": {"policy_id": "read_only"},
    }


def test_governance_process_objects_endpoint(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.list_process_objects",
        lambda _db, limit=20: [{"process_id": "review_before_write", "limit": limit}],
    )

    response = client.get("/api/governance/process-objects", params={"limit": 5})

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "processes": [{"process_id": "review_before_write", "limit": 5}],
    }


def test_governance_process_object_endpoint(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.get_process_object",
        lambda _db, process_id: {"process_id": process_id},
    )

    response = client.get("/api/governance/process-objects/review_before_write")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "process": {"process_id": "review_before_write"},
    }


def test_governance_process_runs_endpoint(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.list_process_runs",
        lambda _db, session_id=None, status=None, limit=20: [
            {"run_id": "run1", "session_id": session_id, "status": status, "limit": limit}
        ],
    )

    response = client.get(
        "/api/governance/process-runs",
        params={"session_id": "s1", "status": "active", "limit": 6},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "runs": [{"run_id": "run1", "session_id": "s1", "status": "active", "limit": 6}],
    }


def test_governance_process_run_endpoint(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.get_process_run",
        lambda _db, run_id: {"run_id": run_id},
    )

    response = client.get("/api/governance/process-runs/run1")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "run": {"run_id": "run1"},
    }


def test_governance_create_process_run_endpoint(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.create_process_run",
        lambda _db, **kwargs: {"run_id": "run1", **kwargs},
    )

    response = client.post(
        "/api/governance/process-runs",
        json={
            "process_id": "restart_continuity",
            "session_id": "s1",
            "identity_id": "mnemosyne_shared",
            "current_step_id": "inspect_state",
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["run"]["process_id"] == "restart_continuity"
    assert response.json()["run"]["current_step_id"] == "inspect_state"


def test_governance_create_process_run_endpoint_reports_invalid_status() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/governance/process-runs",
        json={"process_id": "restart_continuity", "status": "invalid"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "error": "unsupported_process_run_status",
        "run": None,
    }


def test_governance_update_process_run_endpoint(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.update_process_run",
        lambda _db, run_id, **kwargs: {"run_id": run_id, **kwargs},
    )

    response = client.patch(
        "/api/governance/process-runs/run1",
        json={"status": "completed", "completed_step_id": "write_restart"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["run"]["run_id"] == "run1"
    assert response.json()["run"]["status"] == "completed"


def test_output_ingestion_endpoint_filters_by_session() -> None:
    client = TestClient(app)
    db = get_database(load_config().mongo)
    session_id = "web-output-ingestion-test"
    db.output_ingestion_queue.delete_many({"session_id": session_id})
    now = datetime.now(timezone.utc)
    db.output_ingestion_queue.insert_one(
        {
            "schema_version": 1,
            "status": "pending",
            "source_type": "llm_answer",
            "exchange_id": str(ObjectId()),
            "session_id": session_id,
            "query": "What changed?",
            "answer_text": "Captured output.",
            "used_node_ids": [],
            "active_document_ids": [],
            "content_hash_sha256": "hash",
            "attempts": 0,
            "created_at": now,
            "updated_at": now,
        }
    )

    try:
        response = client.get(
            "/api/output-ingestion",
            params={"session_id": session_id, "status": "pending", "limit": 5},
        )
    finally:
        db.output_ingestion_queue.delete_many({"session_id": session_id})

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["jobs"][0]["source_type"] == "llm_answer"
    assert data["jobs"][0]["answer_preview"] == "Captured output."


def test_process_output_ingestion_endpoint(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.process_next_output_ingestion",
        lambda _db: {"ok": True, "status": "idle"},
    )

    response = client.post("/api/process-output-ingestion")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "status": "idle"}


def test_generated_output_review_endpoint(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.list_generated_output_nodes",
        lambda _db, limit=20, endorsement_label=None: [
            {"node_id": "node1", "endorsement_label": endorsement_label, "limit": limit}
        ],
    )

    response = client.get(
        "/api/review/generated-output",
        params={"endorsement": "unreviewed", "limit": 1},
    )

    assert response.status_code == 200
    assert response.json()["nodes"] == [
        {"node_id": "node1", "endorsement_label": "unreviewed", "limit": 1}
    ]


def test_generated_output_review_endpoint_reports_invalid_filter(monkeypatch) -> None:
    client = TestClient(app)

    def fake_list(*_args, **_kwargs):
        raise ValueError("Unsupported endorsement label: trusted")

    monkeypatch.setattr("mnemosyne.web.app.list_generated_output_nodes", fake_list)

    response = client.get(
        "/api/review/generated-output",
        params={"endorsement": "trusted"},
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "invalid_endorsement_label"


def test_endorse_node_endpoint(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.update_node_endorsement",
        lambda _db, node_id, endorsement_label, reviewer="user", note=None: {
            "ok": True,
            "node_id": node_id,
            "endorsement_label": endorsement_label,
            "reviewer": reviewer,
            "note": note,
        },
    )

    response = client.post(
        "/api/review/endorse-node",
        json={
            "node_id": "node1",
            "endorsement": "rejected",
            "reviewer": "tester",
            "note": "bad answer",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "node_id": "node1",
        "endorsement_label": "rejected",
        "reviewer": "tester",
        "note": "bad answer",
    }


def test_semantic_edge_candidates_endpoint(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.list_semantic_edge_candidates",
        lambda _db, status="pending", limit=20: [
            {"candidate_id": "candidate1", "status": status, "limit": limit}
        ],
    )

    response = client.get(
        "/api/review/semantic-edge-candidates",
        params={"status": "pending", "limit": 2},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "candidates": [{"candidate_id": "candidate1", "status": "pending", "limit": 2}],
    }


def test_review_semantic_edge_candidate_endpoint(monkeypatch) -> None:
    client = TestClient(app)

    monkeypatch.setattr(
        "mnemosyne.web.app.review_semantic_edge_candidate",
        lambda _db, **kwargs: {"ok": True, **kwargs},
    )

    response = client.post(
        "/api/review/semantic-edge-candidate",
        json={
            "candidate_id": "candidate1",
            "action": "accept",
            "reviewer": "tester",
            "note": "looks useful",
            "weight": 0.8,
            "confidence": 0.9,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "candidate_id": "candidate1",
        "action": "accept",
        "reviewer": "tester",
        "note": "looks useful",
        "weight": 0.8,
        "confidence": 0.9,
    }


def test_history_endpoint_filters_seeded_rows() -> None:
    client = TestClient(app)
    db = get_database(load_config().mongo)
    session_id = "web-filter-test"
    db.exchanges.delete_many({"session_id": session_id})
    now = datetime.now(timezone.utc)
    db.exchanges.insert_many(
        [
            {
                "schema_version": 1,
                "session_id": session_id,
                "focus_node_id": None,
                "query": "Mnemosyne design purpose",
                "answer": {
                    "adapter": "ollama_cli",
                    "model": "qwen3.6:latest",
                    "answer": "graph memory layer",
                    "used_node_ids": [],
                },
                "created_at": now,
            },
            {
                "schema_version": 1,
                "session_id": session_id,
                "focus_node_id": None,
                "query": "Other prompt",
                "answer": {
                    "adapter": "mock_answer",
                    "model": None,
                    "answer": "unrelated",
                    "used_node_ids": [],
                },
                "created_at": now,
            },
        ]
    )

    try:
        response = client.get(
            "/api/history",
            params={
                "limit": 3,
                "session_id": session_id,
                "q": "Mnemosyne",
                "adapter": "ollama_cli",
                "model": "qwen3.6:latest",
            },
        )
    finally:
        db.exchanges.delete_many({"session_id": session_id})

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert [row["query"] for row in data["exchanges"]] == ["Mnemosyne design purpose"]


def test_jobs_endpoint_filters_seeded_rows() -> None:
    client = TestClient(app)
    db = get_database(load_config().mongo)
    marker = ObjectId().binary.hex()
    db.queue.delete_many({"path": {"$regex": marker}})
    now = datetime.now(timezone.utc)
    db.queue.insert_many(
        [
            {
                "path": f"data/ingest/{marker}-missing.md",
                "checksum_sha256": f"{marker}aaa",
                "status": "failed",
                "reason": "source_missing",
                "attempts": 1,
                "created_at": now,
                "updated_at": now,
            },
            {
                "path": f"data/ingest/{marker}-duplicate.md",
                "checksum_sha256": f"{marker}bbb",
                "status": "rejected",
                "reason": "duplicate_checksum",
                "attempts": 0,
                "created_at": now,
                "updated_at": now,
            },
        ]
    )

    try:
        response = client.get(
            "/api/jobs",
            params={
                "limit": 3,
                "status": "failed",
                "q": marker,
                "reason": "source_missing",
            },
        )
    finally:
        db.queue.delete_many({"path": {"$regex": marker}})

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert [row["reason"] for row in data["jobs"]] == ["source_missing"]


def test_upload_source_stages_supported_file() -> None:
    client = TestClient(app)
    config = load_config()
    filename = f"web-upload-{ObjectId()}.md"
    path = config.paths.ingest / filename
    path.unlink(missing_ok=True)

    try:
        response = client.post(
            "/api/upload-source",
            json={"filename": f"../{filename}", "content": "# Uploaded\n\nSource text."},
        )
        data = response.json()
    finally:
        path.unlink(missing_ok=True)

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["status"] == "staged"
    assert data["filename"] == filename
    assert Path(data["path"]).name == filename


def test_upload_source_rejects_unsupported_suffix() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/upload-source",
        json={"filename": "source.pdf", "content": "%PDF"},
    )

    assert response.status_code == 400
    assert "Unsupported source type" in response.json()["detail"]


def test_ingest_folder_lists_supported_files() -> None:
    client = TestClient(app)
    config = load_config()
    filename = f"web-folder-{ObjectId()}.txt"
    supported_path = config.paths.ingest / filename
    unsupported_path = config.paths.ingest / f"{filename}.pdf"
    config.paths.ingest.mkdir(parents=True, exist_ok=True)
    supported_path.write_text("Date: 2020\n\nfolder source", encoding="utf-8")
    unsupported_path.write_text("ignored", encoding="utf-8")

    try:
        response = client.get("/api/ingest-folder")
        data = response.json()
    finally:
        supported_path.unlink(missing_ok=True)
        unsupported_path.unlink(missing_ok=True)

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["ordering"] == "origin_date_then_path"
    names = [row["name"] for row in data["files"]]
    assert filename in names
    assert f"{filename}.pdf" not in names
    row = next(row for row in data["files"] if row["name"] == filename)
    assert row["origin_date"] == "2020-01-01"
    assert row["origin_date_source"] == "explicit_content"
    assert row["date_candidate_count"] >= 1
