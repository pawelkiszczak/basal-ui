"""Focused tests for the MCP importer and shortlist."""

import json

import pytest

from basal_ui import mcp_import
from basal_ui.fetch import FetchError, checked_url


def test_shortlist_preserves_forms_and_ties():
    tools = [
        {"name": "clock", "description": "time", "inputSchema": {}},
        {"type": "function", "function": {"name": "weather", "description": "weather forecast", "parameters": {}}},
        {"name": "other", "description": "time", "inputSchema": {}},
    ]
    assert mcp_import.shortlist(tools, "what is the weather", 3) == [tools[1], tools[0], tools[2]]
    assert mcp_import.shortlist(tools, "", 2) == tools[:2]
    with pytest.raises(ValueError):
        mcp_import.shortlist(tools, "", True)


def test_explicit_loopback_and_dns_rebinding(monkeypatch):
    import socket

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 80))]
    )
    assert checked_url("http://localhost/mcp", allow_loopback=True)[1] == "localhost"
    with pytest.raises(FetchError):
        checked_url("http://example.com/mcp", allow_loopback=True)
    with pytest.raises(FetchError):
        checked_url("http://localhost/mcp")


def test_protocol_pagination_and_session(monkeypatch):
    import io
    import socket

    calls = []
    replies = [
        {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}},
        None,
        {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "one", "inputSchema": {}}], "nextCursor": "next"}},
        {"jsonrpc": "2.0", "id": 3, "result": {"tools": [{"name": "two", "inputSchema": {}}]}},
        None,
    ]

    class Response:
        status = 200

        def __init__(self, item):
            self.data = io.BytesIO(json.dumps(item).encode())

        def getheader(self, name, default=None):
            return {"Content-Type": "application/json", "Mcp-Session-Id": "session"}.get(name, default)

        def read(self, size):
            return self.data.read(size)

        def close(self):
            pass

    class Connection:
        def __init__(self, *args):
            pass

        def request(self, method, path, body=None, headers=None):
            calls.append((method, json.loads(body) if body else None, dict(headers)))

        def getresponse(self):
            return Response(replies.pop(0))

        def close(self):
            pass

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 80))]
    )
    monkeypatch.setattr(mcp_import, "_Connection", Connection)
    result = mcp_import.list_tools({"url": "http://localhost/mcp", "token": "secret"})
    assert [item["name"] for item in result["tools"]] == ["one", "two"]
    assert [call[1]["method"] for call in calls[:-1]] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/list",
    ]
    assert calls[2][2]["Mcp-Session-Id"] == "session"
    assert calls[-1][0] == "DELETE"


def test_management_routes_use_injected_importer(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from basal_ui.app import create_app
    from tests.test_ui_app import HW, make_fleet

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))

    class Importer:
        closed = False

        async def run(self, body, request):
            assert body == {"url": "http://localhost/mcp"}
            return {"tools": [{"name": "weather", "inputSchema": {}}]}

        async def close(self):
            self.closed = True

    importer = Importer()
    fleet = make_fleet(tmp_path)
    app = create_app(
        catalog_fn=lambda **_: ([], "hub"),
        hardware_fn=lambda: HW,
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
        mcp_importer=importer,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert (
            client.post("/api/mcp/tools", json={"url": "http://localhost/mcp"}).json()["tools"][0]["name"] == "weather"
        )
        result = client.post("/api/tools/shortlist", json={"tools": [{"name": "weather"}], "query": "weather"})
        assert result.json() == {"tools": [{"name": "weather"}]}
    assert importer.closed


def test_question_provenance_is_removed_only_from_question_roots():
    from basal_ui.fleet import strip_mcp_metadata

    source = {"url": "http://localhost/mcp", "selected": ["weather"]}
    body = {
        "state": {"_mcp_source": source},
        "questions": {
            "q": {
                "type": "tool",
                "_mcp_source": source,
                "tools": [{"name": "weather", "inputSchema": {"_mcp_source": source}}],
            }
        },
    }
    clean = strip_mcp_metadata(body)
    assert "_mcp_source" not in clean["questions"]["q"]
    assert clean["state"]["_mcp_source"] == source
    assert clean["questions"]["q"]["tools"][0]["inputSchema"]["_mcp_source"] == source
    assert body["questions"]["q"]["_mcp_source"] == source


def test_actual_gateway_catalogue_import(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from basal_ui import settings
    from basal_ui.app import create_app
    from basal_ui.gateway import build_app
    from tests.test_ui_app import HW, make_fleet
    from tests.test_ui_gateway import free_port

    pytest.importorskip("mcp")
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    port = free_port()
    settings.update_serve({"enabled": True, "port": port, "mcp": True, "require_key": False})
    fleet = make_fleet(tmp_path)
    with TestClient(build_app(fleet, fleet.stats, mcp=True), base_url="http://127.0.0.1") as client:
        advertised = client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream", "Host": "127.0.0.1:8793"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        expected = advertised.json()["result"]["tools"]
    app = create_app(
        catalog_fn=lambda **_: ([], "hub"),
        hardware_fn=lambda: HW,
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/mcp/tools", json={"url": f"http://127.0.0.1:{port}/mcp"})
        assert response.status_code == 200, response.text
        assert response.json()["tools"] == [
            {k: tool[k] for k in ("name", "description", "inputSchema") if k in tool} for tool in expected
        ]
    assert fleet.history.list() == []


def test_decision_and_preset_keep_separate_provenance(tmp_path, monkeypatch, child):
    from fastapi.testclient import TestClient

    from basal_ui.app import create_app
    from basal_ui.fleet import Runner
    from tests.test_ui_app import HW, make_fleet

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    fleet = make_fleet(tmp_path)
    runner = Runner()
    runner.state, runner.model, runner.base_url = "ready", "mini", child.url
    fleet.runners["mini"] = runner
    app = create_app(
        catalog_fn=lambda **_: ([], "hub"),
        hardware_fn=lambda: HW,
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
    )
    metadata = {"url": "http://localhost/mcp", "selected": ["weather"]}
    question = {
        "type": "tool",
        "_mcp_source": metadata,
        "tools": [{"name": "weather", "inputSchema": {"_mcp_source": "keep"}}],
    }
    body = {"model": "mini", "state": {"_mcp_source": "keep"}, "questions": {"q": question}}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.put("/api/presets", json={"kind": "request", "name": "saved", "value": body}).status_code == 200
        assert client.post("/api/decide", json=body).status_code == 200
        sent = child.seen[-1][1]
        assert "_mcp_source" not in sent["questions"]["q"]
        assert sent["state"]["_mcp_source"] == "keep"
        assert sent["questions"]["q"]["tools"][0]["inputSchema"]["_mcp_source"] == "keep"
        assert client.get("/api/presets").json()["request"]["saved"]["questions"]["q"]["_mcp_source"] == metadata
    assert body["questions"]["q"]["_mcp_source"] == metadata


def test_stream_events_and_cursor_loop(monkeypatch):
    import io
    import socket

    class Response:
        status = 200

        def __init__(self, body):
            self.stream = io.BytesIO(body)

        def getheader(self, name, default=None):
            return {"Content-Type": "text/event-stream"}.get(name, default)

        def readline(self, size):
            return self.stream.readline(size)

        def close(self):
            pass

    class Connection:
        def __init__(self, *args):
            pass

        def request(self, method, path, body=None, headers=None):
            self.message = json.loads(body)

        def getresponse(self):
            method, ident = self.message["method"], self.message.get("id")
            if method == "notifications/initialized":
                return Response(b"")
            result = (
                {"protocolVersion": "2025-03-26"} if method == "initialize" else {"tools": [], "nextCursor": "repeat"}
            )
            payload = json.dumps({"id": ident, "result": result}).encode()[1:]
            # A server-originated request is ignored, then multiline data makes one response event.
            return Response(
                b': keepalive\n\ndata: {"jsonrpc":"2.0","method":"sampling/createMessage","id":99}\n\ndata: '
                + b'{"jsonrpc":"2.0",\ndata: '
                + payload
                + b"\n\n"
            )

        def close(self):
            pass

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 80))]
    )
    monkeypatch.setattr(mcp_import, "_Connection", Connection)
    with pytest.raises(FetchError, match="repeated cursor"):
        mcp_import.list_tools({"url": "http://localhost/mcp"})


def test_precision_auth_and_address_guards(monkeypatch):
    import socket

    with pytest.raises(FetchError) as error:
        mcp_import.decode(b'{"number":9007199254740992}')
    assert error.value.code == "mcp_json_precision"
    with pytest.raises(FetchError) as error:
        mcp_import.list_tools({"url": "http://127.0.0.1/mcp", "token": "secret\r\nunsafe"})
    assert error.value.code == "mcp_invalid_token" and "secret" not in str(error.value)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 80)),
        ],
    )
    with pytest.raises(FetchError):
        mcp_import.list_tools({"url": "http://example.com/mcp"})
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 80))]
    )
    with pytest.raises(FetchError) as error:
        mcp_import.list_tools({"url": "http://example.com/mcp", "token": "secret"})
    assert error.value.code == "mcp_insecure_auth"


@pytest.mark.parametrize(
    "case, expected", [("page", "mcp_page_limit"), ("size", "mcp_too_large"), ("auth", "mcp_auth_error")]
)
def test_catalogue_limits_and_auth_errors(monkeypatch, case, expected):
    import socket

    class Response:
        status = 401 if case == "auth" else 200

        def __init__(self, message):
            self.message = message

        def getheader(self, name, default=None):
            return {"Content-Type": "application/json"}.get(name, default)

        def read(self, size):
            if case == "size" and self.message["method"] == "tools/list":
                return b"x" * size
            result = (
                {"protocolVersion": "2025-03-26"}
                if self.message["method"] == "initialize"
                else {"tools": [], "nextCursor": str(self.message["id"])}
            )
            return json.dumps({"jsonrpc": "2.0", "id": self.message["id"], "result": result}).encode()

    class Connection:
        def __init__(self, *args):
            pass

        def request(self, method, path, body=None, headers=None):
            self.message = json.loads(body)

        def getresponse(self):
            return Response(self.message)

        def close(self):
            pass

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 80))]
    )
    monkeypatch.setattr(mcp_import, "_Connection", Connection)
    with pytest.raises(FetchError) as error:
        mcp_import.list_tools({"url": "http://localhost/mcp"})
    assert error.value.code == expected


def test_manager_reaps_started_worker_on_disconnect(monkeypatch):
    import asyncio

    from basal_ui.fetch import FetchManager

    async def scenario():
        started = asyncio.Event()
        killed = asyncio.Event()

        class Process:
            returncode = None

            async def communicate(self, data=None):
                started.set()
                await killed.wait()
                return b"", b""

            def kill(self):
                self.returncode = -9
                killed.set()

        async def spawn(*args, **kwargs):
            return Process()

        class Request:
            async def is_disconnected(self):
                return started.is_set()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        manager = FetchManager("basal_ui.mcp_import")
        with pytest.raises(FetchError) as error:
            await manager.run({"url": "http://localhost/mcp"}, Request())
        assert error.value.code == "fetch_cancelled"
        assert killed.is_set() and not manager.tasks
        await manager.close()

    asyncio.run(scenario())


def test_manager_deadline_reaps_worker(monkeypatch):
    import asyncio

    from basal_ui import fetch

    async def scenario():
        started, killed = asyncio.Event(), asyncio.Event()

        class Process:
            returncode = None

            async def communicate(self, data=None):
                started.set()
                await killed.wait()
                return b"", b""

            def kill(self):
                self.returncode = -9
                killed.set()

        async def spawn(*args, **kwargs):
            return Process()

        class Request:
            async def is_disconnected(self):
                return False

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(fetch, "TIMEOUT_S", 0.2)
        manager = fetch.FetchManager("basal_ui.mcp_import")
        with pytest.raises(FetchError) as error:
            await manager.run({"url": "http://localhost/mcp"}, Request())
        assert error.value.code == "fetch_timeout"
        assert started.is_set() and killed.is_set() and not manager.tasks
        await manager.close()

    asyncio.run(scenario())
