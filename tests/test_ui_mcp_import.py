import json

import httpx
import pytest
from fastapi.testclient import TestClient

from basal_ui import engine
from basal_ui.app import create_app
from basal_ui.mcp_import import MCPImportError, list_tools, shortlist


def test_shortlist_ranks_matching_weather_tool_first_and_keeps_ties_stable():
    tools = [
        {"name": "send_email", "description": "send an email"},
        {"name": "get_weather", "description": "weather by city"},
        {"name": "weather_forecast", "description": "weather by city"},
    ]
    result = shortlist(tools, "what is the weather", k=2)
    assert result[0]["name"] == "get_weather"
    assert [tool["name"] for tool in shortlist(tools, "weather by city", k=2)] == [
        "get_weather",
        "weather_forecast",
    ]


def test_list_tools_follows_sse_pages_and_session_id():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append((body, request.headers))
        if body["method"] == "initialize":
            return httpx.Response(
                200,
                headers={"content-type": "application/json", "Mcp-Session-Id": "session-1"},
                json={"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18"}},
            )
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        cursor = body.get("params", {}).get("cursor")
        result = {"tools": [{"name": "first", "description": "one", "inputSchema": {}}]}
        if cursor:
            result = {"tools": [{"name": "second", "description": "two", "inputSchema": {}}]}
        else:
            result["nextCursor"] = "next"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': result})}\n\n",
        )

    transport = httpx.MockTransport(handler)
    tools = list_tools(
        "http://127.0.0.1:8765/mcp",
        token="secret",
        client_factory=lambda **kw: httpx.Client(**{**kw, "transport": transport}),
    )
    assert [tool["name"] for tool in tools] == ["first", "second"]
    assert calls[-1][1]["mcp-session-id"] == "session-1"
    assert calls[-1][1]["authorization"] == "Bearer secret"
    assert calls[-1][1]["accept"] == "application/json, text/event-stream"
    assert calls[-1][1]["mcp-protocol-version"] == "2025-06-18"


def test_list_tools_converts_json_rpc_errors_to_502():
    def handler(request):
        body = json.loads(request.content)
        if body["method"] == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18"}},
            )
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(400, json={"jsonrpc": "2.0", "id": body["id"], "error": {"message": "denied"}})

    with pytest.raises(MCPImportError, match="denied") as exc:
        list_tools(
            "http://localhost:8765/mcp",
            client_factory=lambda **kw: httpx.Client(**{**kw, "transport": httpx.MockTransport(handler)}),
        )
    assert exc.value.status_code == 502


def test_non_loopback_private_url_is_refused():
    with pytest.raises(ValueError, match="unsafe|private|address"):
        list_tools("http://10.0.0.1:8000/mcp")


def test_shortlist_route_accepts_tools_and_returns_ranked_results(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    app = create_app(catalog_fn=lambda **_: ([], "hub"))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/tools/shortlist",
            json={
                "tools": [
                    {"name": "send_email", "description": "send email"},
                    {"name": "get_weather", "description": "weather by city"},
                ],
                "query": "what is the weather",
                "k": 1,
            },
        )
    assert response.status_code == 200
    assert [tool["name"] for tool in response.json()["tools"]] == ["get_weather"]


def test_import_route_returns_json_rpc_error_as_bad_gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))

    def fail(*_args):
        raise MCPImportError("server rejected tools/list")

    monkeypatch.setattr("basal_ui.mcp_import.list_tools", fail)
    app = create_app(catalog_fn=lambda **_: ([], "hub"))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/mcp/tools", json={"url": "http://localhost:8000/mcp"})
    assert response.status_code == 502
    assert response.json()["detail"] == "server rejected tools/list"


def test_import_route_reads_basals_own_mcp_tools_when_extra_is_available(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    import socket
    import threading
    import time

    import uvicorn

    from basal_ui.gateway import build_app
    from tests.test_ui_app import make_fleet

    fleet = make_fleet(tmp_path)
    mcp_app = build_app(fleet, fleet.stats, mcp=True)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(mcp_app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        tools = list_tools(f"http://127.0.0.1:{port}/mcp")
        names = {tool["name"] for tool in tools}
        assert {"decide", "decide_many"} <= names
        if engine.feature("mcp_models"):
            assert "list_models" in names
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        fleet.shutdown()
