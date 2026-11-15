import asyncio
import io
import socket
import sys

import pytest
from fastapi.testclient import TestClient

from basal_ui import fetch
from basal_ui.app import create_app
from tests.test_ui_app import HW, make_fleet


@pytest.mark.parametrize(
    "address,allowed",
    [
        ("8.8.8.8", True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("10.0.0.1", False),
        ("100.64.0.1", False),
        ("169.254.169.254", False),
        ("224.0.0.1", False),
        ("240.0.0.1", False),
        ("::1", False),
        ("ff02::1", False),
        ("::ffff:127.0.0.1", False),
        ("64:ff9b::7f00:1", False),
        ("2002:7f00:1::", False),
        ("2001:db8::1", False),
        ("fd00:ec2::254", False),
    ],
)
def test_public_address(address, allowed):
    assert fetch.public_address(address) is allowed


def test_dns_rejects_mixed_answers_and_bad_urls(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
        ],
    )
    for url in ("http://example.com", "http://user@example.com", "http://example.com:99999", "http://x/\n"):
        with pytest.raises(fetch.FetchError):
            fetch.checked_url(url)


def test_html_and_json_content():
    state, truncated = fetch._content(
        b"<title>Report</title><nav>noise</nav><h1>Heading</h1><p>Text</p><ul><li>Item</li></ul>"
        b"<table><tr><td>one</td><td>two</td></tr><tr><td>three</td><td>four</td></tr></table>"
        b"<script>hidden</script>",
        "text/html",
    )
    assert state == "Title: Report\nHeading\nText\nItem\n| one | two\n| three | four"
    assert not truncated
    assert fetch._content(b"null", "application/json") == (None, False)
    assert fetch._content(b'"hello"', "application/json") == ("hello", False)
    assert fetch._content(b'{"items":[1,true]}', "application/json")[0] == {"items": [1, True]}
    assert fetch._content(b"x" * (fetch.MAX_TEXT_CHARS + 1), "text/plain") == ("x" * fetch.MAX_TEXT_CHARS, True)
    for data in (b"NaN", b"1e999", b"{", b'"\\ud800"', b'"' + b"x" * fetch.MAX_TEXT_CHARS + b'"'):
        with pytest.raises(fetch.FetchError):
            fetch._content(data, "application/json")
    with pytest.raises(fetch.FetchError):
        fetch._content(b"pdf", "application/pdf")


def test_checked_socket_host_and_tls_hostname(monkeypatch):
    sent, connected, sni = [], [], []

    class FakeSocket:
        def __init__(self, family, kind):
            assert family == socket.AF_INET and kind == socket.SOCK_STREAM

        def settimeout(self, timeout):
            pass

        def connect(self, address):
            connected.append(address)

        def sendall(self, data):
            sent.append(data)

        def makefile(self, *args, **kwargs):
            return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 4\r\n\r\nnull")

        def close(self):
            pass

    class TLS:
        def wrap_socket(self, sock, server_hostname):
            sni.append(server_hostname)
            return sock

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port)),
        ],
    )
    monkeypatch.setattr(socket, "socket", FakeSocket)
    monkeypatch.setattr(fetch.ssl, "create_default_context", TLS)
    result = fetch.fetch("https://example.com/path")
    assert connected == [("8.8.8.8", 443)]
    assert sni == ["example.com"]
    assert b"Host: example.com\r\n" in b"".join(sent)
    assert result == {
        "url": "https://example.com/path",
        "final_url": "https://example.com/path",
        "content_type": "application/json",
        "bytes": 4,
        "truncated": False,
        "state": None,
    }


def test_redirect_is_revalidated_before_connect(monkeypatch):
    resolutions = []

    def resolve(host, port, **kwargs):
        resolutions.append(host)
        address = "127.0.0.1" if host == "internal.example" else "8.8.8.8"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    class Connection:
        def __init__(self, *args):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            class Response:
                status = 302

                def getheader(self, key, default=None):
                    return "//internal.example/private"

            return Response()

        def close(self):
            pass

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(fetch, "_Connection", Connection)
    with pytest.raises(fetch.FetchError, match="non-public"):
        fetch.fetch("http://example.com/")
    assert resolutions == ["example.com", "internal.example"]


def test_worker_deadline_disconnect_and_shutdown_reap(monkeypatch):
    original = asyncio.create_subprocess_exec
    children = []

    async def spawn(*args, **kwargs):
        child = await original(sys.executable, "-c", "import time; time.sleep(30)", **kwargs)
        children.append(child)
        return child

    class Request:
        def __init__(self, disconnected=False):
            self.disconnected = disconnected

        async def is_disconnected(self):
            return self.disconnected

    async def exercise():
        manager = fetch.FetchManager()
        with pytest.raises(fetch.FetchError) as error:
            await manager.run("http://example.com", Request())
        assert error.value.code == "fetch_timeout" and children[-1].returncode is not None
        with pytest.raises(fetch.FetchError) as error:
            await manager.run("http://example.com", Request(True))
        assert error.value.code == "fetch_cancelled" and children[-1].returncode is not None
        task = asyncio.create_task(manager.run("http://example.com", Request()))
        while len(children) < 3:
            await asyncio.sleep(0.001)
        await manager.close()
        assert task.done() and children[-1].returncode is not None and not manager.tasks

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(fetch, "TIMEOUT_S", 0.15)
    asyncio.run(exercise())


def test_local_route_and_shutdown_use_injected_manager(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))

    class Manager:
        closed = False

        async def run(self, url, request):
            if url == "bad":
                raise fetch.FetchError("Refused", "fetch_address_refused", 400)
            return {"state": {"value": 1}, "url": url}

        async def close(self):
            self.closed = True

    manager = Manager()
    fleet = make_fleet(tmp_path)
    app = create_app(
        catalog_fn=lambda **_: ([], "hub"),
        hardware_fn=lambda: HW,
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
        fetch_manager=manager,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/fetch", json={"url": "https://example.com"}).json()["state"] == {"value": 1}
        response = client.post("/api/fetch", json={"url": "bad"})
        assert response.status_code == 400 and response.json()["code"] == "fetch_address_refused"
    assert manager.closed


def test_response_limit_and_content_encoding(monkeypatch):
    monkeypatch.setattr(fetch, "checked_url", lambda url: (fetch.urlsplit(url), "example.com", 80, []))

    class Response:
        status = 200
        encoding = "identity"
        body = b"x" * (fetch.MAX_BYTES + 1)

        def getheader(self, name, default=None):
            return {"Content-Encoding": self.encoding, "Content-Type": "text/plain"}.get(name, default)

        def read(self, limit):
            return self.body[:limit]

    class Connection:
        def __init__(self, *args):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(fetch, "_Connection", Connection)
    with pytest.raises(fetch.FetchError) as error:
        fetch.fetch("http://example.com")
    assert error.value.code == "fetch_too_large"
    Response.encoding = "gzip"
    with pytest.raises(fetch.FetchError) as error:
        fetch.fetch("http://example.com")
    assert error.value.code == "fetch_unsupported_encoding"


def test_decision_history_records_source_but_child_receives_only_native_body(tmp_path, monkeypatch, child):
    from basal_ui.fleet import Runner
    from basal_ui.history import History

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    fleet = make_fleet(tmp_path)
    runner = Runner()
    runner.state, runner.model, runner.base_url = "ready", "mini", child.url
    runner.models_entry = {"early_exit": []}
    fleet.runners["mini"] = runner
    app = create_app(
        catalog_fn=lambda **_: ([], "hub"),
        hardware_fn=lambda: HW,
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
    )
    request = {
        "model": "mini",
        "source_url": "https://example.com/final",
        "state": {"source_url": "inside state", "items": [1, None]},
        "questions": {"q": {"type": "choice", "criteria": ["a", "b"]}},
    }
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/decide", json=request).status_code == 200
        record_id = client.get("/api/history").json()["items"][0]["id"]
        record = client.get(f"/api/history/{record_id}").json()
        assert record["request"] == request
        assert record["replay_request"] == request
        assert client.post("/api/decide", json={**request, "source_url": 42}).status_code == 400
    assert child.seen == [("/v1/systemone", {"state": request["state"], "questions": request["questions"]})]
    assert History(fleet.history.path).get(record_id)["request"] == request
