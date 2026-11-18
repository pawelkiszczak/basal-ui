import asyncio
import hashlib
import json
import socket
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from basal_ui import engine, settings
from basal_ui import gateway as gateway_module
from basal_ui.auth import ApiKeys
from basal_ui.fleet import Fleet, Runner
from basal_ui.gateway import Gateway, build_app
from basal_ui.hardware import Hardware
from basal_ui.history import History
from basal_ui.stats import Stats

HW = Hardware("cpu", "cpu", 1024**4, None, None, frozenset())


@pytest.mark.parametrize("status,code", [(503, "OVERLOADED"), (504, "TIMEOUT"), (413, "PAYLOAD_TOO_LARGE")])
@pytest.mark.parametrize("path", ["/v1/systemone", "/v1/basal", "/v1/decisions", "/decisions"])
def test_child_refusal_preserves_code_and_retry_after(tmp_path, child, status, code, path):
    child.status, child.reply, child.headers = status, {"error": "refused", "code": code}, {"Retry-After": "7"}
    fleet = Fleet(
        models_dir_fn=lambda: tmp_path,
        platform_fn=lambda: "cpu",
        settings_mod=settings,
        hardware_fn=lambda: HW,
        stats=Stats(tmp_path / "stats.json"),
        history=History(tmp_path / "history.jsonl"),
    )
    runner = Runner()
    runner.state, runner.model, runner.base_url = "ready", "a/one", child.url
    fleet.runners[runner.model] = runner
    body = (
        {"model": "a/one", "input": "s", "questions": [{"type": "predicate", "instructions": "valid?"}]}
        if "decisions" in path
        else {"model": "a/one", "state": "s", "questions": {"q": {"type": "noul"}}}
    )
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        reply = client.post(path, json=body)
    assert reply.status_code == status
    assert reply.headers["Retry-After"] == "7"
    assert (reply.json()["error"]["code"] if "decisions" in path else reply.json()["code"]) == code


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """A real fleet with two ready models whose basal-serve echoes the body it receives."""
    fleet = Fleet(
        models_dir_fn=lambda: tmp_path,
        platform_fn=lambda: "cpu",
        settings_mod=settings,
        hardware_fn=lambda: HW,
        stats=Stats(tmp_path / "stats.json"),
        history=History(tmp_path / "history.jsonl"),
    )
    for model_id in ("a/one", "b/two"):
        runner = Runner()
        runner.state, runner.model, runner.ready_at = "ready", model_id, 1700000000.0
        runner.models_entry = {"name": model_id, "media": []}
        fleet.runners[model_id] = runner
    calls = []

    async def adecide(self, client, body, path="/v1/systemone"):
        calls.append((self.model, path, body))
        if body.get("state") == "fail":
            response = httpx.Response(422, json={"error": "bad question", "code": "INVALID_REQUEST"})
        else:
            response = httpx.Response(
                200,
                json={
                    "model": self.model,
                    "answers": {"answer": {"type": "choice", "choice": "x", "confidence": 0.6}},
                    "usage": {"input_tokens": 3},
                },
            )
        response.extensions["basal.inference"] = True
        return response

    monkeypatch.setattr(Runner, "adecide", adecide)
    fleet.calls = calls
    return fleet


def test_model_is_required_and_must_be_loaded_in_both_error_envelopes(fleet):
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        missing = client.post("/v1/systemone", json={"state": "x", "questions": {}})
        assert missing.status_code == 400
        assert missing.json() == {"error": "model is required; loaded models: a/one, b/two", "code": "MODEL_REQUIRED"}
        unknown = client.post("/v1/decisions", json={"model": "nope", "input": "x", "questions": []})
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == "MODEL_NOT_FOUND"
        assert unknown.json()["error"]["message"] == "model 'nope' is not loaded; loaded models: a/one, b/two"
        json_type = {"content-type": "application/json"}
        assert client.post("/decisions", content=b"{", headers=json_type).json()["error"]["code"] == "INVALID_JSON"
        assert client.post("/v1/basal", json=[1]).json()["code"] == "INVALID_JSON"
    assert fleet.calls == []


def test_gateway_rejects_untrusted_host_on_forwarding_routes(fleet):
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        response = client.post("/v1/systemone", json={"model": "a/one"}, headers={"Host": "evil.example"})
    assert response.status_code in (400, 403)


def test_forwarding_counts_and_records_api_decisions(fleet):
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        ok = client.post(
            "/v1/systemone",
            json={"model": "b/two", "state": "s", "questions": {}},
            headers={"x-request-id": "client-1"},
        )
        assert ok.status_code == 200 and ok.json()["model"] == "b/two"
        assert ok.headers["x-request-id"] == "client-1"
        failed = client.post("/v1/basal", json={"model": "a/one", "state": "fail", "questions": {}})
        assert failed.status_code == 422 and failed.json()["error"] == "bad question"
        client.post("/v1/decisions", json={"model": "a/one", "input": "s", "questions": []})
    assert fleet.calls == [
        ("b/two", "/v1/systemone", {"state": "s", "questions": {}}),
        ("a/one", "/v1/systemone", {"state": "fail", "questions": {}}),
        ("a/one", "/v1/decisions", {"model": "a/one", "input": "s", "questions": []}),
    ]
    models = fleet.stats.snapshot()["models"]
    assert (models["b/two"]["requests"], models["a/one"]["requests"], models["a/one"]["errors"]) == (1, 2, 1)
    history = fleet.history.list()
    assert [(r["model"], r["source"], r["status"]) for r in history] == [
        ("a/one", "api", 200),
        ("a/one", "api", 422),
        ("b/two", "api", 200),
    ]
    assert fleet.history.get(history[-1]["id"])["request"] == {"model": "b/two", "state": "s", "questions": {}}
    traffic = fleet.stats.traffic_snapshot(15)
    by_endpoint = {item["endpoint"]: item for item in traffic["endpoints"]}
    assert traffic["requests"] == 3 and traffic["errors"] == 1
    assert {path: (entry["method"], entry["requests"], entry["errors"]) for path, entry in by_endpoint.items()} == {
        "/v1/systemone": ("POST", 1, 0),
        "/v1/basal": ("POST", 1, 1),
        "/v1/decisions": ("POST", 1, 0),
    }


def test_a_history_write_error_does_not_fail_the_api_answer(fleet, monkeypatch):
    def full_disk(record, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fleet.history, "append", full_disk)
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        ok = client.post("/v1/systemone", json={"model": "b/two", "state": "s", "questions": {}})
    assert ok.status_code == 200 and ok.json()["model"] == "b/two"
    assert "decision not recorded: [Errno 28] No space left on device" in fleet.logs["basal-ui"].text()


def test_a_browser_origin_outside_the_cors_origins_is_refused_before_the_model(fleet):
    body = {"model": "a/one", "state": "s", "questions": {}}
    evil = {"Origin": "http://evil.example"}
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        refused = client.post("/v1/systemone", json=body, headers=evil)
        assert refused.status_code == 403 and refused.json()["code"] == "ORIGIN_REFUSED"
        openai = client.post("/v1/decisions", json={"model": "a/one", "input": "s", "questions": []}, headers=evil)
        assert openai.status_code == 403 and openai.json()["error"]["code"] == "ORIGIN_REFUSED"
        assert client.get("/health", headers=evil).status_code == 200
        assert client.post("/v1/systemone", json=body).status_code == 200  # no Origin: not a browser
    assert len(fleet.calls) == 1
    allowed = build_app(fleet, fleet.stats, cors_origins=["http://evil.example"])
    with TestClient(allowed, base_url="http://127.0.0.1") as client:
        assert client.post("/v1/systemone", json={**body, "model": "nope"}, headers=evil).status_code == 404


def test_a_body_over_the_limit_is_refused_while_it_streams(fleet, monkeypatch):
    monkeypatch.setattr(gateway_module, "MAX_BODY_BYTES", 1024)

    def chunks():
        for _ in range(4):
            yield b" " * 512

    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        response = client.post("/v1/systemone", content=chunks(), headers={"content-type": "application/json"})
    assert response.status_code == 413 and response.json()["code"] == "REQUEST_TOO_LARGE"
    assert fleet.calls == []


def test_models_list_in_basal_and_openai_shapes(fleet):
    fleet.runners["b/two"].state = "starting"
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        listing = client.get("/v1/models").json()
        assert listing == {
            "object": "list",
            "models": [{"name": "a/one", "media": []}],
            "data": [{"id": "a/one", "object": "model", "created": 1700000000, "owned_by": "basal"}],
        }
        assert client.get("/health").json() == {"status": "ok", "models": ["a/one"]}


def test_keys_guard_every_route_and_stats_need_the_admin_key(fleet, tmp_path):
    keys_file = tmp_path / "keys.json"
    keys_file.write_text(
        json.dumps(
            {
                "keys": [
                    {"name": "user", "sha256": hashlib.sha256(b"user-secret").hexdigest()},
                    {"name": "basal-ui", "sha256": hashlib.sha256(b"admin-secret").hexdigest(), "admin": True},
                ]
            }
        )
    )
    fleet.stats.record("a/one", {"answers": {}}, 1.0)
    app = build_app(fleet, fleet.stats, ApiKeys(keys_file), cors_origins=["https://app.example"])
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/v1/systemone", json={"model": "a/one"}).status_code == 401
        assert client.get("/health").status_code == 200
        user = {"Authorization": "Bearer user-secret"}
        assert client.post("/v1/systemone", json={"model": "a/one", "state": "s"}, headers=user).status_code == 200
        assert client.get("/v1/stats", headers=user).status_code == 403
        assert client.get("/metrics", headers=user).status_code == 403
        assert client.get("/metrics").status_code == 401
        assert client.get("/metrics", headers={"X-API-Key": "admin-secret"}).status_code == 200
        stats = client.get("/v1/stats", headers={"X-API-Key": "admin-secret"})
        assert stats.status_code == 200 and stats.json()["models"]["a/one"]["requests"] == 2
        preflight = client.options(
            "/v1/systemone", headers={"Origin": "https://app.example", "Access-Control-Request-Method": "POST"}
        )
        assert preflight.headers["access-control-allow-origin"] == "https://app.example"


@pytest.mark.parametrize("mcp_models", [True, False])
def test_mcp_tools_route_by_model_argument_or_the_first_loaded_model(fleet, monkeypatch, mcp_models):
    """Without `model` the API server's MCP answers with the first loaded model (2.4.1 and 2.5.0); with engine 2.5.0
    the tools take `model` and list_models names the loaded ids."""
    pytest.importorskip("mcp")
    if mcp_models and not engine.feature("mcp_models"):
        pytest.skip("the installed engine's MCP tools take no model argument (2.4.1)")
    monkeypatch.setattr(gateway_module, "feature", lambda name: mcp_models if name == "mcp_models" else False)
    settings.update_memory({"idle_unload_minutes": 1})
    fleet.refresh_idle_settings()
    runner = fleet.runners["a/one"]
    runner.base_url, runner.health_polling, runner.last_used = "http://localhost", True, time.monotonic() - 120
    headers = {"Accept": "application/json, text/event-stream", "Host": "127.0.0.1:8793"}

    def rpc(client, method, params):
        response = client.post(
            "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        )
        assert response.status_code == 200, response.text
        return response.json()["result"]

    with TestClient(build_app(fleet, fleet.stats, mcp=True), base_url="http://127.0.0.1") as client:
        answer = rpc(client, "tools/call", {"name": "decide", "arguments": {"state": "s", "question": "?"}})
        assert answer["structuredContent"]["model"] == "a/one"
        tools = {t["name"]: t for t in rpc(client, "tools/list", {})["tools"]}
        if mcp_models:
            assert "model" in tools["decide"]["inputSchema"]["properties"]
            assert "model" in tools["decide_many"]["inputSchema"]["properties"]
            listed = rpc(client, "tools/call", {"name": "list_models", "arguments": {}})
            assert listed["structuredContent"] == {"models": ["a/one", "b/two"]}
            second = rpc(
                client, "tools/call", {"name": "decide", "arguments": {"state": "s", "question": "?", "model": "b/two"}}
            )
            assert second["structuredContent"]["model"] == "b/two"
            missing = rpc(
                client, "tools/call", {"name": "decide", "arguments": {"state": "s", "question": "?", "model": "c/x"}}
            )
            assert missing["isError"] and "model 'c/x' is not loaded" in missing["content"][0]["text"]
        else:
            assert "list_models" not in tools and "model" not in tools["decide"]["inputSchema"]["properties"]
    expected = [("a/one", "mcp"), ("b/two", "mcp")] if mcp_models else [("a/one", "mcp")]
    assert sorted((r["model"], r["source"]) for r in fleet.history.list()) == expected
    traffic = fleet.stats.traffic_snapshot(15)
    assert [(row["endpoint"], row["method"]) for row in traffic["endpoints"]] == [("/mcp", "CALL")]
    monkeypatch.setattr(
        "basal_ui.fleet.httpx.get",
        lambda *args, **kwargs: httpx.Response(
            200,
            json={"status": "ok", "running": 0, "queued": 0},
            request=httpx.Request("GET", "http://localhost/health"),
        ),
    )
    fleet.unload_idle(runner.last_used + 59)
    assert fleet.runners["a/one"] is runner
    fleet.unload_idle(runner.last_used + 60)
    assert "a/one" not in fleet.runners


def test_pre_inference_validation_stays_in_lifetime_stats_but_not_traffic(fleet):
    response = fleet.decide(
        "a/one",
        {"model": "a/one", "input": "x", "questions": [], "unsupported": True},
        path="/v1/decisions",
        source="api",
    )
    assert response.status_code == 400
    assert fleet.stats.snapshot()["models"]["a/one"]["errors"] == 1
    assert fleet.stats.traffic_snapshot(15)["requests"] == 0


def test_decisions_are_translated_for_a_stock_child_and_recorded_as_sent(tmp_path, monkeypatch):
    fleet = Fleet(
        models_dir_fn=lambda: tmp_path,
        platform_fn=lambda: "cpu",
        settings_mod=settings,
        hardware_fn=lambda: HW,
        stats=Stats(tmp_path / "stats.json"),
        history=History(tmp_path / "history.jsonl"),
    )
    runner = Runner()
    runner.state, runner.model, runner.base_url = "ready", "a/one", "http://child"
    fleet.runners["a/one"] = runner
    sent = []

    def child(request):
        sent.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "model": "a/one",
                "answers": {
                    "paid": {"type": "noul", "noul": 0.9, "confidence": 0.9},
                    "kind": {
                        "type": "choice",
                        "choice": "invoice",
                        "probabilities": {"invoice": 0.8, "receipt": 0.2},
                        "confidence": 0.8,
                    },
                },
                "usage": {"input_tokens": 7},
            },
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        gateway_module.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(child), **kw)
    )
    request = {
        "model": "a/one",
        "input": "Invoice 12, paid.",
        "questions": [
            {"type": "predicate", "name": "paid", "instructions": "Is it paid?"},
            {
                "type": "choice",
                "name": "kind",
                "instructions": "Document kind?",
                "choices": [{"value": "invoice"}, {"value": "receipt"}],
            },
        ],
    }
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        reply = client.post("/v1/decisions", json=request)
    assert reply.status_code == 200
    assert sent == [
        (
            "/v1/systemone",
            {
                "state": "Invoice 12, paid.",
                "questions": {
                    "paid": {"type": "noul", "instructions": "Is it paid?"},
                    "kind": {
                        "type": "choice",
                        "instructions": "Document kind?",
                        "criteria": {"invoice": None, "receipt": None},
                    },
                },
            },
        )
    ]
    data = reply.json()
    assert data["object"] == "decision" and data["model"] == "a/one" and data["usage"]["input_tokens"] == 7
    assert data["answers"] == [
        {"type": "predicate", "name": "paid", "probability": 0.9},
        {
            "type": "choice",
            "name": "kind",
            "choice": "invoice",
            "confidence": 0.8,
            "probabilities": [{"value": "invoice", "probability": 0.8}, {"value": "receipt", "probability": 0.2}],
        },
    ]
    record = fleet.history.get(fleet.history.list()[0]["id"])
    assert record["request"] == request and record["response"]["object"] == "decision"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_gateway_serves_on_the_configured_port_and_reports_restarts(fleet):
    gateway = Gateway(fleet, settings, fleet.stats)
    assert gateway.start() is None  # serving disabled
    port = free_port()
    settings.update_serve({"enabled": True, "port": port})
    try:
        status = gateway.start()
        assert (status["running"], status["url"], status["models"]) == (
            True,
            f"http://127.0.0.1:{port}",
            ["a/one", "b/two"],
        )
        assert status["restart_required"] is False
        assert status["mcp_models"] is engine.feature("mcp_models")  # what the Playground's MCP example relies on
        assert httpx.get(f"http://127.0.0.1:{port}/health").json()["models"] == ["a/one", "b/two"]
        settings.update_serve({"require_key": True})
        assert gateway.status()["restart_required"] is True
        status = gateway.restart()
        assert status["require_key"] is True and status["restart_required"] is False
        assert httpx.post(f"http://127.0.0.1:{port}/v1/systemone", json={}).status_code == 401
        internal = {"Authorization": f"Bearer {fleet.internal_secret}"}
        assert httpx.get(f"http://127.0.0.1:{port}/v1/stats", headers=internal).status_code == 200
        assert "basal-ui" in gateway.key_usage()
    finally:
        gateway.stop()
    assert gateway.status()["running"] is False and gateway.status()["restart_required"] is True


def test_port_in_use_is_reported_and_lan_url_is_used_for_all_interfaces(fleet, monkeypatch):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    settings.update_serve({"enabled": True, "port": port})
    try:
        status = Gateway(fleet, settings, fleet.stats).start()
        assert (status["running"], status["error"]) == (False, f"port {port} on 127.0.0.1 is in use")
        assert f"error API server: port {port} on 127.0.0.1 is in use" in [
            line["text"] for line in fleet.logs["basal-ui"].since()
        ]
    finally:
        listener.close()
    calls = []
    monkeypatch.setattr("basal_ui.fleet._lan_ip", lambda: calls.append(1) or "192.0.2.10")
    gateway = Gateway(fleet, settings, fleet.stats)
    gateway.snapshot = {"host": "0.0.0.0", "port": 8123, "require_key": False, "cors_origins": [], "mcp": False}
    assert gateway.status()["url"] == "http://192.0.2.10:8123"
    gateway.status()
    assert calls == [1]


def test_metrics_combines_stats_and_ready_children(fleet, child):
    model = 'a/"one\\line\nnext'
    runner = fleet.runners.pop("a/one")
    runner.model, runner.base_url = model, child.url
    fleet.runners[model] = runner
    fleet.runners["b/two"].base_url = child.url
    child.metrics_reply = (
        200,
        "text/plain; version=0.0.4",
        '# HELP engine_hits Hits\n# TYPE engine_hits counter\nengine_hits{kind="a}b"} 3\n'
        "# HELP engine_duration Duration\n# TYPE engine_duration histogram\n"
        'engine_duration_bucket{le="1"} 2\nengine_duration_bucket{le="+Inf"} 3\n'
        "engine_duration_sum 2.5\nengine_duration_count 3\nengine_active 1\n",
    )
    fleet.stats.record(
        model,
        {"answers": {"q": {"type": "choice"}}, "usage": {"input_tokens": 7}},
        250,
        endpoint='/v1/"test',
        method="POST",
    )
    fleet.stats.record("b/two", {"answers": {}, "usage": {"input_tokens": 4}}, 500, endpoint="/v1/other")
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        response = client.get("/metrics")
        assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
        lines = response.text.splitlines()
        for key in ("requests", "questions", "errors", "input_tokens"):
            assert lines.count(f"# TYPE basal_ui_{key}_total counter") == 1
        for key in ("requests", "errors"):
            assert lines.count(f"# TYPE basal_ui_traffic_{key}_total gauge") == 1
        label = r'model="a/\"one\\line\nnext"'
        assert f"basal_ui_requests_total{{{label}}} 1" in lines
        assert f"basal_ui_questions_total{{{label}}} 1" in lines
        assert f"basal_ui_errors_total{{{label}}} 0" in lines
        assert f"basal_ui_input_tokens_total{{{label}}} 7" in lines
        assert 'basal_ui_requests_total{model="b/two"} 1' in lines
        assert 'basal_ui_input_tokens_total{model="b/two"} 4' in lines
        assert f'basal_ui_latency_seconds{{{label},quantile="0.95"}} 0.25' in lines
        assert f"basal_ui_latency_seconds_avg{{{label}}} 0.25" in lines
        assert r'basal_ui_traffic_requests_total{endpoint="/v1/\"test",method="POST"} 1' in lines
        assert 'basal_ui_traffic_requests_total{endpoint="/v1/other",method="POST"} 1' in lines
        assert "basal_ui_models_loaded 2" in lines
        assert f'engine_hits{{kind="a}}b",{label}}} 3' in lines
        assert 'engine_active{model="b/two"} 1' in lines
        assert lines.count("# HELP engine_hits Hits") == 1
        assert lines.count("# TYPE engine_hits counter") == 1
        for model_label in (label, 'model="b/two"'):
            assert f'engine_hits{{kind="a}}b",{model_label}}} 3' in lines
            assert f'engine_duration_bucket{{le="1",{model_label}}} 2' in lines
            assert f'engine_duration_bucket{{le="+Inf",{model_label}}} 3' in lines
            assert f"engine_duration_sum{{{model_label}}} 2.5" in lines
            assert f"engine_duration_count{{{model_label}}} 3" in lines

        families = {
            **{f"basal_ui_{key}_total": ("counter",) for key in ("requests", "questions", "errors", "input_tokens")},
            **{f"basal_ui_traffic_{key}_total": ("gauge",) for key in ("requests", "errors")},
            "engine_hits": ("counter", "HELP"),
            "engine_duration": ("histogram", "HELP"),
        }
        for name, (kind, *help_) in families.items():
            metadata = [f"# TYPE {name} {kind}"]
            if help_:
                metadata.append(f"# HELP {name} {'Hits' if name == 'engine_hits' else 'Duration'}")
            assert all(lines.count(line) == 1 for line in metadata)
            sample_names = (name, *(f"{name}_{suffix}" for suffix in ("bucket", "sum", "count")))
            indices = [
                i
                for i, line in enumerate(lines)
                if line in metadata or any(line.startswith(f"{sample}{{") for sample in sample_names)
            ]
            assert len(indices) > len(metadata)
            assert indices == list(range(indices[0], indices[-1] + 1))
            assert all(lines.index(line) < indices[len(metadata)] for line in metadata)
        child.metrics_reply = (404, "text/plain", "missing")
        assert "engine_active" not in client.get("/metrics").text
        child.metrics_reply = (200, "application/json", '{"bad": 1}')
        assert "engine_active" not in client.get("/metrics").text


def test_configured_admin_secret_authenticates_real_gateway_and_is_not_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    secret = "operator-admin-secret"
    monkeypatch.setenv("BASAL_UI_ADMIN_KEY", secret)
    stats = Stats(tmp_path / "stats.json")
    fleet = Fleet(lambda: tmp_path, lambda: "cpu", settings, lambda: HW, stats=stats)
    _, user_secret = settings.create_key("operator")
    port = free_port()
    settings.update_serve({"enabled": True, "port": port, "require_key": True})
    gateway = Gateway(fleet, settings, stats)
    try:
        assert gateway.start()["running"]
        url = f"http://127.0.0.1:{port}/metrics"
        assert httpx.get(url).status_code == 401
        assert httpx.get(url, headers={"Authorization": f"Bearer {user_secret}"}).status_code == 403
        response = httpx.get(url, headers={"Authorization": f"Bearer {secret}"})
        assert response.status_code == 200
        assert "basal_ui_models_loaded 0" in response.text
        assert secret not in (tmp_path / "settings.json").read_text()
        key_file = settings._keys_path()
        assert secret not in key_file.read_text()
        assert hashlib.sha256(secret.encode()).hexdigest() in key_file.read_text()
    finally:
        gateway.stop()
        settings.remove_keys_file()
        stats.close()


def test_metrics_skip_composites_and_stale_child(fleet, child):
    class Composite:
        state = "ready"
        base_url = child.url
        models_entry = {"kind": "comparison"}

    fleet.runners["comparison"] = Composite()
    cascade = Composite()
    cascade.models_entry = {"kind": "cascade"}
    fleet.runners["cascade"] = cascade
    runner = fleet.runners["a/one"]
    runner.base_url = child.url
    entered, release = threading.Event(), threading.Event()
    child.metrics_entered, child.metrics_gate = entered, release
    child.metrics_reply = (200, "text/plain", "engine_value 1\n")
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        result = []
        thread = threading.Thread(target=lambda: result.append(client.get("/metrics")))
        thread.start()
        try:
            assert entered.wait(5)
            with fleet.lock:
                fleet.runners["a/one"] = Runner()
            release.set()
            thread.join(5)
            assert not thread.is_alive()
            assert "basal_ui_models_loaded 4" in result[0].text
            assert "engine_value" not in result[0].text
            assert child.metrics_seen == 1
            assert not child.seen
        finally:
            release.set()
            thread.join(5)


def test_metrics_scrapes_siblings_concurrently(fleet, child, monkeypatch):
    entered = threading.Event()
    runner = fleet.runners["a/one"]
    runner.base_url = child.url
    fleet.runners["b/two"].base_url = "http://127.0.0.1:1"
    child.metrics_reply = (200, "text/plain", "engine_value 1\n")
    child.metrics_entered = entered
    before = fleet.stats.snapshot()
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        response = client.get("/metrics")
        assert entered.is_set()
        assert 'engine_value{model="a/one"} 1' in response.text
        assert client.get("/metrics").status_code == 200
    assert fleet.stats.snapshot() == before
    assert not child.seen


def test_metrics_timeout_does_not_hide_healthy_sibling(fleet, child):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"healthy 1\n")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    gate, entered = threading.Event(), threading.Event()
    child.metrics_gate, child.metrics_entered = gate, entered
    child.metrics_reply = (200, "text/plain", "slow 1\n")
    fleet.runners["a/one"].base_url = child.url
    fleet.runners["b/two"].base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
            response = client.get("/metrics")
            assert entered.is_set()
            assert 'healthy{model="b/two"} 1' in response.text
            assert "slow{" not in response.text
    finally:
        gate.set()
        server.shutdown()
        server.server_close()
        thread.join()


def test_metrics_snapshot_does_not_block_gateway_event_loop(fleet, monkeypatch):
    entered, release, health_done = threading.Event(), threading.Event(), threading.Event()
    snapshot = fleet.stats.snapshot

    def blocked_snapshot():
        entered.set()
        assert release.wait(5)
        return snapshot()

    monkeypatch.setattr(fleet.stats, "snapshot", blocked_snapshot)
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        metrics = threading.Thread(target=lambda: client.get("/metrics"))
        health = threading.Thread(target=lambda: (client.get("/health"), health_done.set()))
        metrics.start()
        try:
            assert entered.wait(5)
            health.start()
            assert health_done.wait(2)
        finally:
            release.set()
            metrics.join(5)
            if health.ident:
                health.join(5)
        assert not metrics.is_alive() and not health.is_alive()


def test_read_timeout_and_disconnect_accounting_with_delayed_child(tmp_path, child, monkeypatch):
    import time

    from basal_ui import fleet as fleet_module

    monkeypatch.setattr(fleet_module, "READ_TIMEOUT_S", 0.1)
    fleet = Fleet(
        lambda: tmp_path,
        lambda: "cpu",
        settings,
        lambda: HW,
        stats=Stats(tmp_path / "stats.json"),
        history=History(tmp_path / "history.jsonl"),
    )
    runner = Runner()
    runner.state, runner.model, runner.base_url = "ready", "a/one", child.url
    fleet.runners["a/one"] = runner
    child.gate.clear()
    body = {"model": "a/one", "state": "synthetic", "questions": {"q": {"type": "noul"}}}
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        result = client.post("/v1/systemone", json=body)
        assert result.status_code == 504
        assert result.json() == {"error": "basal-serve did not respond within 0.1 seconds", "code": "UPSTREAM_TIMEOUT"}
        result = client.post(
            "/v1/decisions",
            json={
                "model": "a/one",
                "input": "synthetic",
                "questions": [{"type": "predicate", "instructions": "Is it?"}],
            },
        )
        assert result.status_code == 504 and result.json()["error"]["code"] == "UPSTREAM_TIMEOUT"
    records = fleet.history.list()
    assert [record["status"] for record in records] == [504, 504]
    assert fleet.history.get(records[0]["id"])["response"] == result.json()
    assert fleet.stats.snapshot()["models"]["a/one"]["errors"] == 2
    assert fleet.stats.traffic_snapshot(15)["errors"] == 2

    monkeypatch.setattr(fleet_module, "READ_TIMEOUT_S", 10)
    settings.update_serve({"enabled": True, "port": free_port()})
    gateway = Gateway(fleet, settings, fleet.stats)
    try:
        gateway.start()
        with socket.create_connection(("127.0.0.1", gateway.snapshot["port"])) as sock:
            payload = json.dumps(body).encode()
            sock.sendall(
                b"POST /v1/systemone HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\n\r\n"
                + payload
            )
            deadline = time.monotonic() + 3
            while len(child.seen) < 3 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert len(child.seen) == 3
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with fleet.lock:
                released = not fleet.leases and not fleet.inflight
            if len(fleet.history.list()) == 3 and released:
                break
            time.sleep(0.01)
        assert fleet.history.list()[0]["status"] == 499
        assert released
        assert fleet.stats.snapshot()["models"]["a/one"]["requests"] == 3
        assert fleet.stats.snapshot()["models"]["a/one"]["errors"] == 3
        traffic = fleet.stats.traffic_snapshot(15)
        assert traffic["requests"] == traffic["errors"] == 3
    finally:
        child.gate.set()
        gateway.stop()


def test_forwarding_timeout_covers_active_engine_deadline():
    runner = Runner()
    assert runner.read_timeout() == 600
    runner.options = {"request_timeout": 900}
    assert runner.read_timeout() == 930


def test_benchmark_retains_client_timeout(tmp_path):
    from basal_ui.fleet import upstream_error

    sent = []

    def child(request):
        sent.append(request.extensions["timeout"])
        raise httpx.ReadTimeout("synthetic read timeout")

    fleet = Fleet(lambda: tmp_path, lambda: "cpu", settings, lambda: HW)
    runner = Runner()
    runner.state, runner.model, runner.base_url = "ready", "a/one", "http://child"
    fleet.runners["a/one"] = runner

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(child), timeout=300) as client:
            body = {"state": "synthetic", "questions": {"q": {"type": "noul"}}}
            for source, seconds in (("benchmark", 300), ("api", 600)):
                with pytest.raises(httpx.ReadTimeout) as caught:
                    await fleet.adecide("a/one", body, source=source, client=client)
                status, message, code = upstream_error(caught.value)
                assert (status, code) == (504, "UPSTREAM_TIMEOUT")
                assert f"within {seconds} seconds" in message

    asyncio.run(run())
    assert sent[0] == dict.fromkeys(("connect", "read", "write", "pool"), 300)
    assert sent[1] == {"connect": 120, "read": 600, "write": 120, "pool": 120}


def test_disconnect_during_upload_is_not_recorded(fleet):
    messages = iter(
        [
            {"type": "http.request", "body": b'{"model":', "more_body": True},
            {"type": "http.disconnect"},
        ]
    )
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/systemone",
        "raw_path": b"/v1/systemone",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"127.0.0.1"), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 80),
    }
    asyncio.run(build_app(fleet, fleet.stats)(scope, receive, send))
    assert sent[0]["status"] == 499
    assert fleet.calls == [] and fleet.history.list() == []
    assert fleet.stats.snapshot()["models"] == {} and fleet.stats.traffic_snapshot(15)["requests"] == 0


def test_transport_failures_and_sync_timeout_are_recorded(fleet, monkeypatch):
    async def offline(self, client, body, path="/v1/systemone"):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(Runner, "adecide", offline)
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        response = client.post("/v1/decisions", json={"model": "a/one", "input": "x", "questions": []})
    assert response.status_code == 502 and response.json()["error"]["code"] == "UPSTREAM_ERROR"
    assert fleet.history.get(fleet.history.list()[0]["id"])["response"] == response.json()

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("")

    monkeypatch.setattr(httpx, "post", timeout)
    runner = fleet.runners["a/one"]
    runner.base_url = "http://child"
    with pytest.raises(httpx.ReadTimeout):
        fleet.decide("a/one", {"state": "x", "questions": {"q": {"type": "noul"}}})
    record = fleet.history.get(fleet.history.list()[0]["id"])
    assert record["status"] == 504 and record["response"]["code"] == "UPSTREAM_TIMEOUT"

    from basal_ui.app import create_app

    app = create_app(
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
        catalog_fn=lambda **_: ([], "test"),
        hardware_fn=lambda: HW,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/decide", json={"model": "a/one", "state": "x", "questions": {"q": {"type": "noul"}}}
        )
    assert response.status_code == 504
    assert response.json() == {
        "detail": record["response"]["error"],
        "code": "UPSTREAM_TIMEOUT",
        "params": {"seconds": 600},
    }


def test_mcp_timeout_reports_gateway_error_and_records_it(fleet, monkeypatch):
    pytest.importorskip("mcp")

    async def timeout(self, client, body, path="/v1/systemone"):
        raise httpx.ReadTimeout("")

    monkeypatch.setattr(Runner, "adecide", timeout)
    headers = {"Accept": "application/json, text/event-stream", "Host": "127.0.0.1:8793"}
    with TestClient(build_app(fleet, fleet.stats, mcp=True), base_url="http://127.0.0.1") as client:
        response = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "decide", "arguments": {"state": "synthetic", "question": "?"}},
            },
        ).json()["result"]
    assert response["isError"] and "504 UPSTREAM_TIMEOUT" in response["content"][0]["text"]
    record = fleet.history.get(fleet.history.list()[0]["id"])
    assert record["status"] == 504 and record["source"] == "mcp"
    assert record["response"]["code"] == "UPSTREAM_TIMEOUT"


def test_disconnect_while_success_is_recorded_does_not_record_499(fleet, monkeypatch):
    import threading
    import time

    entered, release = threading.Event(), threading.Event()
    real_record = fleet._record

    def blocked_record(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return real_record(*args, **kwargs)

    monkeypatch.setattr(fleet, "_record", blocked_record)
    settings.update_serve({"enabled": True, "port": free_port()})
    gateway = Gateway(fleet, settings, fleet.stats)
    try:
        gateway.start()
        with socket.create_connection(("127.0.0.1", gateway.snapshot["port"])) as sock:
            payload = json.dumps({"model": "a/one", "state": "synthetic", "questions": {"q": {}}}).encode()
            sock.sendall(
                b"POST /v1/systemone HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\n\r\n"
                + payload
            )
            assert entered.wait(3)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with fleet.lock:
                released = not fleet.leases and not fleet.inflight
            if released:
                break
            time.sleep(0.01)
        assert released
        release.set()
        deadline = time.monotonic() + 3
        while not fleet.history.list() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert [record["status"] for record in fleet.history.list()] == [200]
        assert fleet.stats.snapshot()["models"]["a/one"]["errors"] == 0
    finally:
        release.set()
        gateway.stop()


def test_async_gateway_retains_audio_only_when_enabled(fleet):
    body = {"model": "b/two", "state": {"data": "ordinary"}, "questions": {}, "audio": ["data:audio/wav;base64,QUJD"]}
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        assert client.post("/v1/systemone", json=body).status_code == 200
        settings.set_keep_history_audio(True)
        assert client.post("/v1/systemone", json=body).status_code == 200
        settings.set_keep_history_audio(False)
        assert client.post("/v1/systemone", json=body).status_code == 200
    rows = fleet.history.list(order="asc")
    assert fleet.history.detail(rows[0]["id"], True)["replay_request"] is None
    expected = {k: v for k, v in body.items() if k != "audio"}
    expected["media"] = [{"type": "audio", "data": "QUJD"}]
    assert fleet.history.detail(rows[1]["id"], True)["replay_request"] == expected
    assert fleet.history.detail(rows[2]["id"], True)["replay_request"] is None
    assert "QUJD" not in str(fleet.history.detail(rows[1]["id"]))


@pytest.mark.parametrize("audio_storage", [False, True])
def test_invalid_history_utf8_does_not_fail_async_gateway_decision(audio_storage, fleet):
    if audio_storage:
        history_path = fleet.history.path
        history_path.with_name(history_path.name + ".audio").mkdir(mode=0o700)
    fleet.history.path.write_bytes(b"\xff")
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        reply = client.post("/v1/systemone", json={"model": "b/two", "state": "s", "questions": {}})
    assert reply.status_code == 200
    if audio_storage:
        assert fleet.history.path.read_bytes() == b"\xff"
        assert "decision not recorded" in fleet.logs["basal-ui"].text()
    else:
        assert fleet.history.path.read_bytes().splitlines()[0] == b"\xff"
        assert len(fleet.history.list()) == 1
        assert "decision not recorded" not in (fleet.logs.get("basal-ui").text() if "basal-ui" in fleet.logs else "")


def test_gateway_audio_alias_replay_is_canonical_and_preserves_falsey_state(tmp_path, monkeypatch):
    fleet = Fleet(
        models_dir_fn=lambda: tmp_path,
        platform_fn=lambda: "cpu",
        settings_mod=settings,
        hardware_fn=lambda: HW,
        history=History(tmp_path / "history.jsonl"),
    )
    runner = Runner()
    runner.state, runner.model, runner.base_url = "ready", "a/one", "http://child"
    fleet.runners["a/one"] = runner
    sent = []

    def child(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"answers": {}, "usage": {}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        gateway_module.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(child), **kw)
    )
    settings.set_keep_history_audio(True)
    body = {
        "model": "a/one",
        "state": "",
        "questions": {"q": {"type": "noul"}},
        "audio": ["data:audio/wav;base64,QUJD"],
    }
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        assert client.post("/v1/systemone", json=body).status_code == 200
    detail = fleet.history.detail(fleet.history.list()[0]["id"], True)
    assert detail["replay_request"] == {
        "model": "a/one",
        "state": "",
        "questions": body["questions"],
        "media": [{"type": "audio", "data": "QUJD"}],
    }
    assert sent[0]["media"] == [{"type": "audio", "data": "QUJD"}]
    assert "QUJD" not in str(detail["request"])


def test_audio_policy_read_failure_keeps_successful_gateway_decision(fleet, monkeypatch):
    settings.set_keep_history_audio(True)
    monkeypatch.setattr(
        settings, "_audio_policy_data", lambda: (_ for _ in ()).throw(settings.HistoryAudioPolicyError())
    )
    with TestClient(build_app(fleet, fleet.stats), base_url="http://127.0.0.1") as client:
        reply = client.post("/v1/systemone", json={"model": "b/two", "state": "s", "questions": {}})
    assert reply.status_code == 200
    assert fleet.history.list() == []
    assert "decision not recorded" in fleet.logs["basal-ui"].text()
