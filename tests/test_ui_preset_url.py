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
    pack = {"request": {"large": {"questions": {"q": {"text": "x" * 220000}}, "state": [1, 0.25, True, None]}}}
    response.body = json.dumps(pack).encode()
    assert fetch.fetch("http://example.com", presets=True)["state"] == pack
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


def test_url_pack_boundary_parity_and_persistence(tmp_path, monkeypatch):
    config = tmp_path / "settings.json"
    monkeypatch.setenv("BASAL_UI_CONFIG", str(config))
    monkeypatch.setattr(
        fetch.socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80))]
    )
    monkeypatch.setattr(fetch, "_Connection", Connection)
    response = Connection.response = Response()

    class Manager:
        async def run(self, url, request):
            return fetch.fetch(url, presets=True)

        async def close(self):
            pass

    fleet = make_fleet(tmp_path)
    app = create_app(
        catalog_fn=lambda **k: ([], "synthetic"), hardware_fn=lambda: HW, fleet=fleet, preset_fetch_manager=Manager()
    )
    pack = {
        "load": {"existing": {"mode": "eager"}, "delete": None},
        "request": {
            "existing": {
                "model": "ignore",
                "questions": {"q": {"type": "choice"}},
                "state": {"text": "z" * 220001, "typed": [1, 0.25, True, None]},
            }
        },
    }
    encoded = json.dumps(pack, separators=(",", ":")).encode()
    response.body = encoded + b" " * (1024 * 1024 - len(encoded))
    assert len(response.body) == 1024 * 1024
    initial = [
        ("load", "existing", {"mode": "fast-exit"}),
        ("load", "delete", {"mode": "eager"}),
        ("load", "unrelated", {"mode": "eager"}),
        ("request", "existing", {"questions": {"old": {"type": "choice"}}}),
        ("request", "unrelated", {"questions": {"keep": {"type": "choice"}}}),
    ]
    with TestClient(app, base_url="http://127.0.0.1") as client:

        def put(kind, name, value):
            result = client.put("/api/presets", json={"kind": kind, "name": name, "value": value})
            assert result.status_code == 200
            return result.json()

        for entry in initial:
            put(*entry)
        for kind in ("load", "request"):
            for name, value in pack[kind].items():
                file_result = put(kind, name, value)
        config.unlink()
        for entry in initial:
            put(*entry)
        result = client.post("/api/presets/import-url", json={"url": "http://example.com/pack"})
        assert result.status_code == 200 and result.json()["count"] == 3
        assert result.json()["presets"] == file_result == settings.presets()
        saved = settings.load()["presets"]
        assert saved == file_result
        assert saved["request"]["existing"] == {k: v for k, v in pack["request"]["existing"].items() if k != "model"}
        assert [type(v) for v in saved["request"]["existing"]["state"]["typed"]] == [int, float, bool, type(None)]
        assert saved["load"]["unrelated"] == {"mode": "eager"} and "delete" not in saved["load"]
        assert saved["request"]["unrelated"]["questions"] == {"keep": {"type": "choice"}}
        assert not fleet.runners
        response.body += b" "
        assert len(response.body) == 1024 * 1024 + 1
        refused = client.post("/api/presets/import-url", json={"url": "http://example.com/pack"})
        assert refused.status_code == 413 and refused.json()["code"] == "fetch_too_large"
        assert settings.load()["presets"] == saved


def test_url_pack_reports_partial_persistence_error(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    original = settings.set_preset

    def fail_second(kind, name, value):
        if name == "second":
            raise OSError("secret path")
        return original(kind, name, value)

    monkeypatch.setattr(settings, "set_preset", fail_second)

    class Manager:
        async def run(self, url, request):
            return {"state": {"load": {"first": {"mode": "eager"}, "second": {"mode": "eager"}}}}

        async def close(self):
            pass

    fleet = make_fleet(tmp_path)
    app = create_app(
        catalog_fn=lambda **k: ([], "synthetic"), hardware_fn=lambda: HW, fleet=fleet, preset_fetch_manager=Manager()
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        result = client.post("/api/presets/import-url", json={"url": "http://example.com"})
        assert result.status_code == 500
        assert result.json()["code"] == "presets_partial_import"
        assert "secret path" not in result.text
        assert settings.presets()["load"] == {"first": {"mode": "eager"}}
