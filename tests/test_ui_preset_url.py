import json
import socket

import pytest
from fastapi.testclient import TestClient

from basal_ui import fetch, settings
from basal_ui.app import create_app
from tests.test_ui_app import HW, make_fleet


class Response:
    status = 200
    length = None
    body = b"{}"
    content_type = "application/json"
    location = None

    def getheader(self, name, default=None):
        return {"Content-Type": self.content_type, "Location": self.location}.get(name, default)

    def read(self, size):
        return self.body[:size]


class Connection:
    response = Response()

    def __init__(self, *args):
        pass

    def request(self, *args, **kwargs):
        pass

    def getresponse(self):
        return self.response

    def close(self):
        pass


def test_transport_restrictions(monkeypatch):
    monkeypatch.setattr(
        fetch.socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80))]
    )
    monkeypatch.setattr(fetch, "_Connection", Connection)
    response = Connection.response = Response()
    response.body = json.dumps({"request": {"large": {"questions": {"q": {"text": "x" * 220000}}}}}).encode()
    assert (
        len(fetch.fetch("http://example.com", presets=True)["state"]["request"]["large"]["questions"]["q"]["text"])
        == 220000
    )
    for body, content_type, code in [
        (b"{}", "text/plain", "fetch_unsupported_type"),
        (b"x" * (1024 * 1024 + 1), "application/json", "fetch_too_large"),
        (b"{", "application/json", "fetch_invalid_json"),
    ]:
        response.body, response.content_type = body, content_type
        with pytest.raises(fetch.FetchError) as error:
            fetch.fetch("http://example.com", presets=True)
        assert error.value.code == code
    monkeypatch.setattr(
        fetch.socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
        ],
    )
    with pytest.raises(fetch.FetchError, match="non-public"):
        fetch.fetch("http://example.com", presets=True)


def test_route_merge_and_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))

    class Manager:
        pack = {}

        async def run(self, url, request):
            if url == "private":
                raise fetch.FetchError("Refused", "fetch_address_refused", 400)
            return {"state": self.pack}

        async def close(self):
            pass

    manager = Manager()
    fleet = make_fleet(tmp_path)
    app = create_app(catalog_fn=lambda **k: ([], []), hardware_fn=lambda: HW, fleet=fleet, preset_fetch_manager=manager)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.put("/api/presets", json={"kind": "load", "name": "old", "value": {"mode": "eager"}})
        manager.pack = {
            "load": {"old": None, "new": {"mode": "eager"}},
            "request": {"shared": {"model": "remove", "questions": {"q": {"type": "choice"}}, "state": {"n": 1}}},
        }
        result = client.post("/api/presets/import-url", json={"url": "https://example.com"}).json()
        assert result["count"] == 3
        assert (
            result["presets"]
            == settings.presets()
            == {
                "load": {"new": {"mode": "eager"}},
                "request": {"shared": {"questions": {"q": {"type": "choice"}}, "state": {"n": 1}}},
            }
        )
        before = settings.presets()
        assert client.post("/api/presets/import-url", json={"url": "private"}).status_code == 400
        manager.pack = {"load": []}
        assert client.post("/api/presets/import-url", json={"url": "valid"}).json()["code"] == "presets_invalid_pack"
        assert settings.presets() == before
        manager.pack = {"load": {"newer": {"mode": "eager"}, ".bad": {}}}
        assert client.post("/api/presets/import-url", json={"url": "valid"}).json()["code"] == "presets_partial_import"
        assert "newer" in settings.presets()["load"]


def test_real_worker_refuses_loopback_before_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    fleet = make_fleet(tmp_path)
    app = create_app(catalog_fn=lambda **_: ([], "synthetic"), hardware_fn=lambda: HW, fleet=fleet)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        before = client.get("/api/presets").json()
        response = client.post("/api/presets/import-url", json={"url": "http://127.0.0.1/presets.json"})
        assert response.status_code == 400
        assert response.json()["code"] == "fetch_address_refused"
        assert client.get("/api/presets").json() == before
