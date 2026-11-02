import socket

import httpx
import pytest
from fastapi.testclient import TestClient

from basal_ui import fetch as url_fetch
from tests.test_ui_app import app_for, make_fleet

HTTPX_CLIENT = httpx.Client


def _resolver(ip):
    return lambda host, port, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


@pytest.mark.parametrize(
    "url,ip",
    [
        ("http://127.0.0.1/", "127.0.0.1"),
        ("http://10.0.0.1/", "10.0.0.1"),
        ("http://169.254.169.254/", "169.254.169.254"),
        ("http://[::1]/", "::1"),
        ("http://[fd00:ec2::254]/", "fd00:ec2::254"),
        ("http://100.64.0.1/", "100.64.0.1"),
        ("http://metadata.google.internal/", "10.0.0.8"),
    ],
)
def test_guard_refuses_non_public_addresses(monkeypatch, url, ip):
    monkeypatch.setattr(url_fetch.socket, "getaddrinfo", _resolver(ip))
    with pytest.raises(url_fetch.UnsafeAddress):
        url_fetch.guard(url)


def test_guard_accepts_public_addresses(monkeypatch):
    monkeypatch.setattr(url_fetch.socket, "getaddrinfo", _resolver("93.184.216.34"))
    assert url_fetch.guard("https://example.com/path") == "example.com"


def test_connections_go_only_to_the_checked_addresses(monkeypatch):
    """DNS rebinding: the socket is opened to the address guard() accepted, never to a fresh lookup of the host."""
    opened = []
    monkeypatch.setattr(
        url_fetch.httpcore.SyncBackend, "connect_tcp", lambda self, host, port, *args: opened.append((host, port))
    )
    backend = url_fetch._PinnedBackend("example.com", ["93.184.216.34"])
    backend.connect_tcp("example.com", 443)
    assert opened == [("93.184.216.34", 443)]
    with pytest.raises(url_fetch.httpcore.ConnectError):
        backend.connect_tcp("internal.example", 443)


def _route_client(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    app, _ = app_for(tmp_path, [])
    return TestClient(app, base_url="http://127.0.0.1")


def _mock_client(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(url_fetch.httpx, "Client", lambda **kwargs: HTTPX_CLIENT(**{**kwargs, "transport": transport}))


def test_fetch_route_reduces_html(monkeypatch, tmp_path):
    monkeypatch.setattr(url_fetch.socket, "getaddrinfo", _resolver("93.184.216.34"))
    _mock_client(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=(
                "<html><title>Product</title><script>secret()</script><nav>links</nav>"
                "<h1>Page</h1><p>Body text</p></html>"
            ),
        ),
    )
    with _route_client(tmp_path, monkeypatch) as client:
        response = client.post("/api/fetch", json={"url": "https://example.com/item"})
    assert response.status_code == 200
    result = response.json()
    assert result["state"] == "Title: Product\nPage\nBody text"
    assert "secret" not in result["state"] and "links" not in result["state"]
    assert result["final_url"] == "https://example.com/item"


def test_fetch_route_preserves_json(monkeypatch, tmp_path):
    monkeypatch.setattr(url_fetch.socket, "getaddrinfo", _resolver("93.184.216.34"))
    _mock_client(
        monkeypatch,
        lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json={"ok": True}),
    )
    with _route_client(tmp_path, monkeypatch) as client:
        response = client.post("/api/fetch", json={"url": "https://example.com/data"})
    assert response.status_code == 200
    assert response.json()["state"] == {"ok": True}


def test_import_presets_from_url_merges_existing_presets(monkeypatch, tmp_path):
    monkeypatch.setattr(url_fetch.socket, "getaddrinfo", _resolver("93.184.216.34"))
    _mock_client(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/json; charset=utf-8"},
            json={
                "load": {"imported": {"mode": "eager"}},
                "request": {"shared": {"questions": {"decision": {"type": "choice", "options": ["yes", "no"]}}}},
            },
        ),
    )
    with _route_client(tmp_path, monkeypatch) as client:
        client.put("/api/presets", json={"kind": "load", "name": "existing", "value": {"mode": "mps"}})
        response = client.post("/api/presets/import-url", json={"url": "https://example.com/presets.json"})
    assert response.status_code == 200
    assert response.json() == {
        "load": {"existing": {"mode": "mps"}, "imported": {"mode": "eager"}},
        "request": {"shared": {"questions": {"decision": {"type": "choice", "options": ["yes", "no"]}}}},
    }


def test_import_presets_from_url_refuses_non_json(monkeypatch, tmp_path):
    monkeypatch.setattr(url_fetch.socket, "getaddrinfo", _resolver("93.184.216.34"))
    _mock_client(monkeypatch, lambda request: httpx.Response(200, headers={"content-type": "text/plain"}, text="{}"))
    with _route_client(tmp_path, monkeypatch) as client:
        response = client.post("/api/presets/import-url", json={"url": "https://example.com/presets"})
    assert response.status_code == 415
    assert response.json()["code"] == "fetch_unsupported_type"


def test_import_presets_from_url_refuses_oversized_json(monkeypatch, tmp_path):
    monkeypatch.setattr(url_fetch.socket, "getaddrinfo", _resolver("93.184.216.34"))
    _mock_client(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b" " * (1024 * 1024 + 1),
        ),
    )
    with _route_client(tmp_path, monkeypatch) as client:
        response = client.post("/api/presets/import-url", json={"url": "https://example.com/presets"})
    assert response.status_code == 413
    assert response.json()["code"] == "fetch_too_large"


def test_import_presets_from_url_refuses_private_addresses(monkeypatch, tmp_path):
    monkeypatch.setattr(url_fetch.socket, "getaddrinfo", _resolver("10.0.0.1"))
    with _route_client(tmp_path, monkeypatch) as client:
        response = client.post("/api/presets/import-url", json={"url": "https://example.com/presets"})
    assert response.status_code == 400
    assert response.json()["code"] == "fetch_address_refused"


def test_fetch_route_refuses_oversized_response(monkeypatch, tmp_path):
    monkeypatch.setattr(url_fetch.socket, "getaddrinfo", _resolver("93.184.216.34"))
    _mock_client(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"x" * (url_fetch.MAX_BYTES + 1),
        ),
    )
    with _route_client(tmp_path, monkeypatch) as client:
        response = client.post("/api/fetch", json={"url": "https://example.com/large"})
    assert response.status_code == 413
    assert response.json()["code"] == "fetch_too_large"


def test_fetch_route_rechecks_redirect_destination(monkeypatch, tmp_path):
    calls = []

    def resolve(host, port, **kwargs):
        return _resolver("10.0.0.1" if host == "internal.example" else "93.184.216.34")(host, port, **kwargs)

    def respond(request):
        calls.append(request.url.host)
        return httpx.Response(302, headers={"location": "http://internal.example/private"})

    monkeypatch.setattr(url_fetch.socket, "getaddrinfo", resolve)
    _mock_client(monkeypatch, respond)
    with _route_client(tmp_path, monkeypatch) as client:
        response = client.post("/api/fetch", json={"url": "https://example.com/redirect"})
    assert response.status_code == 400
    assert response.json()["code"] == "fetch_address_refused"
    assert calls == ["example.com"]


def test_decide_records_source_url_without_forwarding_it_to_the_model(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    app, fleet = app_for(tmp_path, [])
    monkeypatch.setattr(fleet, "loaded_ids", lambda: ["mini"])
    received = {}

    def decide(model_id, body, source, source_url):
        received.update(model=model_id, body=body, source=source, source_url=source_url)
        return httpx.Response(200, json={"answers": {}}, extensions={"basal.inference": True})

    monkeypatch.setattr(fleet, "decide", decide)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/decide",
            json={"model": "mini", "state": "text", "questions": {}, "source_url": "https://example.com/page"},
        )
    assert response.status_code == 200
    assert received == {
        "model": "mini",
        "body": {"state": "text", "questions": {}},
        "source": "playground",
        "source_url": "https://example.com/page",
    }


def test_source_url_is_kept_in_history_request(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    fleet = make_fleet(tmp_path)
    fleet._record(
        "mini",
        {"state": "text", "questions": {}},
        "/v1/systemone",
        "playground",
        200,
        {"answers": {}},
        1,
        source_url="https://example.com/page",
    )
    record = fleet.history.records[0]
    assert record["request"]["source_url"] == "https://example.com/page"
