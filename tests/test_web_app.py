from datetime import datetime, timezone

from bson import ObjectId
from fastapi.testclient import TestClient

from mnemosyne.config import load_config
from mnemosyne.db.client import get_database
from mnemosyne.web.app import app


def test_health_endpoint() -> None:
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True


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
