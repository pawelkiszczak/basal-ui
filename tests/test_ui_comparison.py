"""External Decision servers through the app and Fleet boundaries."""

import asyncio
import json
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from basal_ui import engine, settings
from basal_ui.app import create_app
from basal_ui.fleet import ComparisonRunner, Fleet, Runner
from basal_ui.gateway import build_app
from basal_ui.hardware import Hardware
from basal_ui.history import History
from basal_ui.stats import Stats

HW = Hardware("cpu", "cpu", 1024**4, None, None, frozenset())
BODY = {
    "state": "synthetic",
    "questions": {"q": {"type": "choice", "instructions": "Pick", "criteria": {"0": "a", "1": "b"}}},
}


def wait_for(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.01)
    pytest.fail("condition did not become true")


@pytest.fixture
def app_client(tmp_path, monkeypatch, child):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    fleet = Fleet(
        lambda: tmp_path,
        lambda: "cpu",
        settings,
        lambda: HW,
        stats=Stats(tmp_path / "stats.json"),
        history=History(tmp_path / "history.jsonl"),
    )
    primary = Runner()
    primary.state, primary.model, primary.base_url, primary.models_entry = (
        "ready",
        "basal",
        child.url,
        {"name": "basal"},
    )
    fleet.runners["basal"] = primary
    child.models = [{"id": "provider/model", "kind": "basal", "max_options": 2, "memory_bytes": 12345}]
    app = create_app(
        catalog_fn=lambda **_: ([], "test"),
        hardware_fn=lambda: HW,
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, fleet


def attach(client, child, key="secret-for-test", url=None):
    response = client.post("/api/decision-servers", json={"name": "Local", "url": url or child.url, "key": key})
    assert response.status_code == 201, response.text
    server = response.json()["servers"][0]
    model = wait_for(
        lambda: next(
            (
                m
                for m in client.get("/api/status").json()["models"]
                if m.get("kind") == "comparison" and m["state"] == "ready"
            ),
            None,
        )
    )
    return server, model


def test_attach_decide_accounting_gateway_and_detach(app_client, child, tmp_path):
    client, fleet = app_client
    server, model = attach(client, child)
    model_id = model["id"]
    assert model_id.startswith("comparison:")
    assert model["entry"]["kind"] == "comparison" and model["reported_memory_bytes"] == 12345
    assert model["est_memory_bytes"] == 0
    assert "secret-for-test" not in client.get("/api/status").text + client.get("/api/decision-servers").text
    secret = tmp_path / "settings.decision-secrets.json"
    assert os.stat(secret).st_mode & 0o777 == 0o600
    child.reply["answers"]["q"].update(calibration="validated", calibrated=True, automated={"0.01": True})
    response = client.post("/api/decide", json={**BODY, "model": model_id})
    assert response.status_code == 200
    answer = response.json()["answers"]["q"]
    assert answer["calibrated"] is False and "calibration" not in answer and "automated" not in answer
    assert child.seen[-1][1]["model"] == "provider/model"
    assert child.headers[-1]["Authorization"] == "Bearer secret-for-test"
    stats = client.get("/api/stats").json()["models"][model_id]
    assert stats["requests"] == 1 and stats["questions"] == 1
    traffic = client.get("/api/traffic").json()
    assert traffic["requests"] == 1
    records = fleet.history.list()
    assert records[0]["model"] == model_id and records[0]["server"] == "Local"
    assert "secret-for-test" not in json.dumps(records)
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as gateway:
        assert model_id not in [x["id"] for x in gateway.get("/v1/models").json()["data"]]
        assert model_id not in gateway.get("/health").json()["models"]
        assert gateway.post("/v1/systemone", json={**BODY, "model": model_id}).status_code == 404
    assert client.post("/api/unload", json={"id": model_id}).status_code == 409
    assert client.post("/api/benchmark", json={"id": model_id}).status_code == 400
    assert client.delete(f"/api/decision-servers/{server['id']}").status_code == 200
    assert settings.decision_servers() == [] and settings.decision_secret(server["id"]) is None
    assert model_id not in fleet.runners
    assert fleet.stats.snapshot()["models"][model_id]["requests"] == 1


def test_consent_and_refusals_precede_upstream(app_client, child):
    client, _ = app_client
    rejected = client.post("/api/decision-servers", json={"name": "X", "url": "https://remote.example"})
    assert rejected.status_code == 400 and rejected.json()["detail"]["code"] == "CONSENT_REQUIRED"
    server, model = attach(client, child)
    model_id = model["id"]
    for body in [
        {**BODY, "facts": True},
        {**BODY, "questions": {"q": {**BODY["questions"]["q"], "evidence": True}}},
        {**BODY, "questions": {"q": {"type": "multi", "criteria": ["a", "b"]}}},
        {**BODY, "questions": {"q": {**BODY["questions"]["q"], "criteria": ["a", "b", "c"]}}},
        {**BODY, "media": [{"type": "audio", "data": "YQ=="}]},
        {**BODY, "media": [{"type": "image", "data": "YQ=="}]},
    ]:
        response = client.post("/api/decide", json={**body, "model": model_id})
        assert response.status_code == 400 and response.json()["code"] == "UNSUPPORTED_FOR_COMPARISON", response.text
        if len(body["questions"]["q"].get("criteria", [])) == 3:
            assert "limit is 2" in response.json()["error"]
    assert child.seen == []
    child.reply_status = 401
    assert client.post("/api/decide", json={**BODY, "model": model_id}).status_code == 401
    child.reply_status = 200
    assert client.post("/api/decide", json={**BODY, "model": "basal"}).status_code == 200
    assert client.delete(f"/api/decision-servers/{server['id']}").status_code == 200


def test_batch_real_subprocess_preflight_and_accounting(app_client, child, tmp_path):
    client, fleet = app_client
    _, model = attach(client, child)
    model_id = model["id"]
    source = tmp_path / "items.jsonl"
    item = {"state": "synthetic", "question": "Pick", "options": ["a", "b"], "gold": 0}
    source.write_text(json.dumps(item) + "\n\n" + json.dumps({**item, "facts": True}) + "\n")
    refused = client.post("/api/batch", json={"id": model_id, "input": str(source)})
    assert refused.status_code == 400 and "line 3" in refused.text and "facts" in refused.text
    assert child.seen == []
    source.write_text(json.dumps({**BODY, "gold": 0}) + "\n")
    refused = client.post("/api/batch", json={"id": model_id, "input": str(source)})
    assert refused.status_code == 400 and "does not grade gold" in refused.text and child.seen == []
    source.write_text(json.dumps(item) + "\n" + json.dumps({**item, "gold": 1}) + "\n")
    assert client.post("/api/batch", json={"id": model_id, "input": str(source)}).status_code == 202
    job = wait_for(
        lambda: j if (j := client.get("/api/status").json()["jobs"].get("batch")) and not j["running"] else None, 30
    )
    assert job["phase"] == "done", job
    assert job["result"]["accuracy"] == 0.5
    log = fleet.logs["job:batch"].text()
    assert "basal.run" in log and "--url" in log and "secret-for-test" not in log
    assert len(child.seen) == 2 and all(x[1]["model"] == "provider/model" for x in child.seen)
    assert all(h["Authorization"] == "Bearer secret-for-test" for h in child.headers)
    assert fleet.stats.snapshot()["models"][model_id]["requests"] == 2
    assert fleet.stats.traffic_snapshot(15)["requests"] == 2
    assert [(r["source"], r["server"]) for r in fleet.history.list()] == [("batch", "Local")] * 2


def test_hosted_decisions_translation_and_failure(app_client, child):
    client, _ = app_client
    child.models_status = 404
    child.reply = {
        "model": "Local",
        "answers": [
            {
                "type": "choice",
                "name": "q",
                "choice": "0",
                "confidence": 0.8,
                "probabilities": [{"value": "0", "probability": 0.8}, {"value": "1", "probability": 0.2}],
            }
        ],
        "usage": {},
    }
    _, model = attach(client, child, url=child.url + "/v1/decisions")
    response = client.post("/api/decide", json={**BODY, "model": model["id"]})
    assert response.status_code == 200 and response.json()["answers"]["q"]["probabilities"]["0"] == 0.8
    path, payload = child.seen[-1]
    assert path == "/v1/decisions" and payload["model"] == "Local" and payload["input"] == "synthetic"
    count = len(child.seen)
    invalid = client.post("/api/decide", json={**BODY, "model": model["id"], "lang": "pl"})
    assert invalid.status_code == 400 and len(child.seen) == count


def test_detach_and_shutdown_cannot_resurrect_during_discovery(app_client, child):
    client, fleet = app_client
    child.discovery_started.clear()
    child.discovery_gate.clear()
    response = client.post("/api/decision-servers", json={"name": "Held", "url": child.url})
    server_id = response.json()["servers"][0]["id"]
    assert child.discovery_started.wait(3)
    assert client.delete(f"/api/decision-servers/{server_id}").status_code == 200
    child.discovery_gate.set()
    wait_for(lambda: not any(t.name == f"basal-ui-decision-server-{server_id}" for t in threading.enumerate()))
    assert not any(m.get("kind") == "comparison" for m in client.get("/api/status").json()["models"])
    child.discovery_started.clear()
    child.discovery_gate.clear()
    response = client.post("/api/decision-servers", json={"name": "Held2", "url": child.url})
    server_id = response.json()["servers"][0]["id"]
    assert child.discovery_started.wait(3)
    fleet.shutdown()
    child.discovery_gate.set()
    wait_for(lambda: not any(t.name == f"basal-ui-decision-server-{server_id}" for t in threading.enumerate()))
    assert fleet.decision_servers == {} and not fleet.runners


def test_slow_comparison_preparation_does_not_block_event_loop(monkeypatch):
    runner = ComparisonRunner(
        {"id": "x", "name": "X", "base_url": "http://unused", "decisions_url": None}, {"id": "m"}, None
    )
    started, release = threading.Event(), threading.Event()

    def blocked(_body):
        started.set()
        release.wait(3)
        return None, {}, {}

    monkeypatch.setattr(runner, "_prepare", blocked)

    class Client:
        async def post(self, *args, **kwargs):
            import httpx

            return httpx.Response(500)

    async def run():
        task = asyncio.create_task(runner.adecide(Client(), BODY))
        assert await asyncio.to_thread(started.wait, 3)
        release.set()
        assert (await task).status_code == 500

    asyncio.run(run())


def test_recovery_refreshes_capabilities_and_preserves_identity(app_client, child):
    client, fleet = app_client
    server, model = attach(client, child)
    child.health_status = 503
    with fleet.lock:
        fleet.decision_servers[server["id"]]["next_poll"] = 0
    wait_for(
        lambda: next(
            (x for x in client.get("/api/status").json()["decision_servers"] if x["state"] == "unavailable"), None
        )
    )
    assert client.post("/api/decide", json={**BODY, "model": model["id"]}).status_code == 409
    child.health_status = 200
    child.models = [
        {"id": "provider/model", "kind": "basal", "name": "forged", "modalities": ["image"], "max_options": 3}
    ]
    with fleet.lock:
        fleet.decision_servers[server["id"]]["next_poll"] = 0
    refreshed = wait_for(
        lambda: next(
            (x for x in client.get("/api/status").json()["models"] if x["id"] == model["id"] and x["state"] == "ready"),
            None,
        )
    )
    assert refreshed["entry"]["kind"] == "comparison" and refreshed["entry"]["name"] == model["id"]
    assert refreshed["media"] == ["image"]
    image = {**BODY, "model": model["id"], "media": [{"type": "image", "data": "YQ=="}]}
    assert client.post("/api/decide", json=image).status_code == 200
    assert child.seen[-1][1]["media"][0]["type"] == "image"


def test_detach_keeps_an_inflight_decision_and_server_provenance(app_client, child):
    client, fleet = app_client
    server, model = attach(client, child)
    child.gate.clear()
    result = []
    thread = threading.Thread(
        target=lambda: result.append(client.post("/api/decide", json={**BODY, "model": model["id"]}))
    )
    thread.start()
    wait_for(lambda: bool(child.seen))
    assert client.delete(f"/api/decision-servers/{server['id']}").status_code == 200
    child.gate.set()
    thread.join(5)
    assert not thread.is_alive() and result[0].status_code == 200
    assert fleet.history.list()[0]["server"] == "Local"
    assert not fleet.leases


def test_secret_writes_roll_back_on_settings_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    write = settings._write_private

    def fail_settings(path, text):
        if path == tmp_path / "settings.json":
            raise OSError("simulated settings failure")
        write(path, text)

    monkeypatch.setattr(settings, "_write_private", fail_settings)
    with pytest.raises(OSError):
        settings.add_decision_server("X", "http://127.0.0.1:8000", False, "secret")
    assert (
        settings.decision_servers() == []
        and json.loads((tmp_path / "settings.decision-secrets.json").read_text()) == {}
    )
    monkeypatch.setattr(settings, "_write_private", write)
    record = settings.add_decision_server("X", "http://127.0.0.1:8000", False, "secret")
    monkeypatch.setattr(settings, "_write_private", fail_settings)
    with pytest.raises(OSError):
        settings.remove_decision_server(record["id"])
    assert settings.decision_servers() == [record] and settings.decision_secret(record["id"]) == "secret"


def test_timeout_is_recorded_and_does_not_prevent_primary(app_client, child, monkeypatch):
    import httpx

    client, fleet = app_client
    _, model = attach(client, child)
    post = httpx.post

    def timeout(url, **kwargs):
        if kwargs.get("headers", {}).get("Authorization"):
            raise httpx.ReadTimeout("synthetic timeout")
        return post(url, **kwargs)

    monkeypatch.setattr(httpx, "post", timeout)
    assert client.post("/api/decide", json={**BODY, "model": model["id"]}).status_code == 502
    assert client.post("/api/decide", json={**BODY, "model": "basal"}).status_code == 200
    assert fleet.stats.snapshot()["models"][model["id"]]["errors"] == 1
    assert fleet.history.list(model=model["id"])[0]["server"] == "Local"


def test_hosted_hidden_batch_choices_keep_original_keys(app_client, child, tmp_path):
    client, _ = app_client
    child.models_status = 404
    child.reply = {
        "model": "Local",
        "answers": [
            {
                "type": "choice",
                "name": "q",
                "choice": "a",
                "confidence": 0.8,
                "probabilities": [{"value": "a", "probability": 0.8}, {"value": "b", "probability": 0.2}],
            }
        ],
        "usage": {},
    }
    _, model = attach(client, child, url=child.url + "/v1/decisions")
    source = tmp_path / "hosted.jsonl"
    source.write_text(json.dumps({"state": "synthetic", "question": "Pick", "options": ["a", "b"], "gold": 0}) + "\n")
    assert client.post("/api/batch", json={"id": model["id"], "input": str(source)}).status_code == 202
    job = wait_for(
        lambda: j if (j := client.get("/api/status").json()["jobs"].get("batch")) and not j["running"] else None, 30
    )
    assert job["phase"] == "done" and job["result"]["accuracy"] == 1.0, job
    assert child.seen[-1][1]["questions"][0]["choices"] == [{"value": "a"}, {"value": "b"}]


def test_hosted_image_and_visible_keys_preserved(app_client, child):
    import base64

    client, _ = app_client
    child.models = [{"id": "provider/model", "modalities": ["image"]}]
    child.reply = {
        "model": "provider/model",
        "answers": [
            {
                "type": "choice",
                "name": "q",
                "choice": "0",
                "confidence": 0.8,
                "probabilities": [{"value": "0", "probability": 0.8}, {"value": "1", "probability": 0.2}],
            }
        ],
        "usage": {},
    }
    _, model = attach(client, child, url=child.url + "/v1/decisions")
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nsynthetic").decode()
    request = {
        **BODY,
        "model": model["id"],
        "detail": "default",
        "media": [{"type": "image", "data": png}],
        "questions": {"q": {**BODY["questions"]["q"], "option_keys": "show"}},
    }
    assert client.post("/api/decide", json=request).status_code == 200
    sent = child.seen[-1][1]
    assert sent["model"] == "provider/model"
    assert sent["questions"][0]["choices"] == [{"value": "0", "description": "a"}, {"value": "1", "description": "b"}]
    assert sent["input"][0]["content"][1]["image_url"] == f"data:image/png;base64,{png}"
    count = len(child.seen)
    assert client.post("/api/decide", json={**request, "detail": "high"}).status_code == 400
    assert len(child.seen) == count


def test_upstream_echo_of_key_is_redacted(app_client, child):
    client, fleet = app_client
    child.models[0]["note"] = "secret-for-test"
    _, model = attach(client, child)
    assert "secret-for-test" not in client.get("/api/status").text
    child.reply_status = 401
    child.reply = {"error": "bad secret-for-test"}
    response = client.post("/api/decide", json={**BODY, "model": model["id"]})
    assert response.status_code == 401 and "secret-for-test" not in response.text
    assert "secret-for-test" not in json.dumps(fleet.history.list())


def test_mcp_filters_comparison_for_listing_default_and_explicit_selection(app_client, child):
    pytest.importorskip("mcp")
    client, fleet = app_client
    _, model = attach(client, child)
    child.reply = {"model": "basal", "answers": {"answer": {"type": "noul", "noul": 0.8}}}
    with fleet.lock:
        comparison = fleet.runners.pop(model["id"])
        fleet.runners = {model["id"]: comparison, **fleet.runners}
    headers = {"Accept": "application/json, text/event-stream", "Host": "127.0.0.1:8793"}

    def rpc(gateway, name, arguments):
        response = gateway.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        )
        assert response.status_code == 200, response.text
        return response.json()["result"]

    with TestClient(build_app(fleet, fleet.stats, mcp=True), base_url="http://127.0.0.1") as gateway:
        if engine.feature("mcp_models"):
            listed = rpc(gateway, "list_models", {})
            assert listed["structuredContent"]["models"] == ["basal"]
        result = rpc(gateway, "decide", {"state": "synthetic", "question": "Pick"})
        assert not result.get("isError")
        assert child.headers[-1].get("Authorization") is None
        if engine.feature("mcp_models"):
            rejected = rpc(gateway, "decide", {"state": "synthetic", "question": "Pick", "model": model["id"]})
            assert rejected["isError"] and "not loaded" in rejected["content"][0]["text"]


def test_link_local_does_not_require_consent(app_client, monkeypatch):
    import httpx

    client, _ = app_client
    monkeypatch.setattr(
        httpx, "get", lambda url, **kwargs: httpx.Response(200, json={"data": []}, request=httpx.Request("GET", url))
    )
    response = client.post("/api/decision-servers", json={"name": "Link local", "url": "http://169.254.1.2:9000"})
    assert response.status_code == 201
    server_id = response.json()["servers"][0]["id"]
    assert client.delete(f"/api/decision-servers/{server_id}").status_code == 200


def test_servers_and_keys_restore_after_restart(app_client, child, tmp_path):
    client, _ = app_client
    saved, model = attach(client, child)
    fleet = Fleet(
        lambda: tmp_path,
        lambda: "cpu",
        settings,
        lambda: HW,
        stats=Stats(tmp_path / "second-stats.json"),
        history=History(tmp_path / "second-history.jsonl"),
    )
    app = create_app(
        catalog_fn=lambda **_: ([], "test"),
        hardware_fn=lambda: HW,
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
    )
    with TestClient(app, base_url="http://127.0.0.1") as restarted:
        ready = wait_for(
            lambda: next(
                (
                    m
                    for m in restarted.get("/api/status").json()["models"]
                    if m["id"] == model["id"] and m["state"] == "ready"
                ),
                None,
            )
        )
        assert ready["entry"]["server"] == saved["id"]
        assert restarted.post("/api/decide", json={**BODY, "model": model["id"]}).status_code == 200
        assert child.headers[-1]["Authorization"] == "Bearer secret-for-test"
