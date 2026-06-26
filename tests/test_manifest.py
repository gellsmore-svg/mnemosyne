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


def test_planner_tool_hint_derives_from_manifest_and_enablement():
    from types import SimpleNamespace

    from tirzah.manifest import planner_tools, render_planner_tool_hint

    # disabled -> no tools advertised
    assert render_planner_tool_hint(SimpleNamespace(milcah_enabled=False)) == ""
    assert planner_tools(SimpleNamespace(milcah_enabled=False)) == []

    # enabled -> coherence_check is advertised, with its description from the manifest
    runtime = SimpleNamespace(milcah_enabled=True)
    assert [c.name for c in planner_tools(runtime)] == ["coherence_check"]
    hint = render_planner_tool_hint(runtime)
    assert "coherence_check" in hint and "counter-framework" in hint


def test_specialist_capability_federates_from_milcah_when_present(monkeypatch):
    import tirzah.manifest as tm
    from keturah import capability

    # Milcah absent here -> local fallback is used (still conformant + planner-tagged)
    local = tm._specialist_capability()
    assert local.name == "coherence_check" and "planner" in local.tags

    # Simulate Milcah present: its manifest owns the declaration -> Tirzah advertises it
    federated = capability("coherence_check", "Milcah's own description.", tags=["specialist", "coherence"])
    monkeypatch.setattr(tm, "_milcah_coherence_capability", lambda: federated)
    got = tm._specialist_capability()
    assert got.description == "Milcah's own description."  # sourced from Milcah
    assert "planner" in got.tags  # Tirzah ensures it's planner-callable


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
