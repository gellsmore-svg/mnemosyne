from fastapi.testclient import TestClient
from keturah import validate_manifest

from tirzah.manifest import build_manifest
from tirzah.web.app import app


def test_tirzah_manifest_is_conformant_and_built_from_contracts():
    m = build_manifest()
    assert validate_manifest(m) == []
    assert m.product == "tirzah"
    names = m.names()
    assert {"ask", "coherence_check", "semantic_annotate", "capabilities"} <= set(names)

    # the specialist capability's schema is built from the live contract enum
    from tirzah.coherence import SPECIALIST_MODES

    coherence = next(c for c in m.capabilities if c.name == "coherence_check")
    assert set(coherence.input_schema["properties"]["mode"]["enum"]) == set(SPECIALIST_MODES)


def test_capabilities_endpoint_full_and_mcp():
    client = TestClient(app)

    full = client.get("/api/capabilities").json()
    assert full["product"] == "tirzah"
    assert any(c["name"] == "ask" for c in full["capabilities"])

    mcp = client.get("/api/capabilities", params={"format": "mcp"}).json()
    tool_names = [t["name"] for t in mcp["tools"]]
    assert "ask" in tool_names
    # resources are not MCP tools
    assert "capabilities" not in tool_names
    # every MCP tool has the required shape
    for tool in mcp["tools"]:
        assert tool["name"] and tool["description"] and isinstance(tool["inputSchema"], dict)
