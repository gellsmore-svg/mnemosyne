from fastapi.testclient import TestClient

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
