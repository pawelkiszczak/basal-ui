from types import SimpleNamespace

from basal_ui import memory
from basal_ui.runtimes import vllm

MODELS = {
    "object": "list",
    "data": [
        {"id": "Qwen/Qwen3-8B", "object": "model", "max_model_len": 32768},
        {"id": "meta-llama/Llama-3.1-8B", "object": "model", "max_model_len": 8192},
    ],
}
METRICS = """# HELP vllm:gpu_cache_usage_perc GPU cache usage.
# TYPE vllm:gpu_cache_usage_perc gauge
vllm:gpu_cache_usage_perc{model_name="Qwen/Qwen3-8B"} 0.375
"""


def _response(payload=None, text=""):
    return SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: payload,
        text=text,
    )


def test_observe_confirms_vllm_models_and_keeps_fractional_cache_unknown(monkeypatch):
    calls = []
    responses = [_response(MODELS), _response(text=METRICS)]
    monkeypatch.setenv("BASAL_UI_VLLM_URL", "http://127.0.0.1:18000/")
    monkeypatch.setattr(
        vllm.httpx,
        "get",
        lambda url, **kwargs: calls.append((url, kwargs)) or responses.pop(0),
    )
    monkeypatch.setattr(memory, "process_rss", lambda pattern: 512 * 1024**2 if pattern == r"\bvllm\b" else None)
    monkeypatch.setattr(memory, "rss_accounting", lambda: "resident")

    result = vllm.observe()

    assert [call[0] for call in calls] == [
        "http://127.0.0.1:18000/v1/models",
        "http://127.0.0.1:18000/metrics",
    ]
    assert all(call[1]["timeout"] <= 1 for call in calls)
    assert result["runtime"] == "vllm"
    assert result["status"] == "ok"
    assert result["process_bytes"] == 512 * 1024**2
    assert [model["id"] for model in result["models"]] == ["Qwen/Qwen3-8B", "meta-llama/Llama-3.1-8B"]
    assert all(model["context_bytes"] is None and model["cache_bytes"] is None for model in result["models"])
    assert "37.5%" in result["note"]
    assert "runtime overhead" in result["note"]
    assert result["accounting"] == "resident"


def test_observe_keeps_zero_rss_distinct_and_unavailable_measurements_unknown(monkeypatch):
    responses = [_response(MODELS), _response(text="# unrelated metric 0\n")]
    monkeypatch.setattr(vllm.httpx, "get", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(memory, "process_rss", lambda _: 0)

    result = vllm.observe()

    assert result["status"] == "ok"
    assert result["process_bytes"] == 0
    assert all(model["bytes"] is None and model["cache_bytes"] is None for model in result["models"])
    assert "not reported in bytes" in result["note"]


def test_observe_uses_process_fallback_when_vllm_api_is_unreachable(monkeypatch):
    def get(*args, **kwargs):
        raise vllm.httpx.ConnectError("offline")

    monkeypatch.setattr(vllm.httpx, "get", get)
    monkeypatch.setattr(memory, "process_rss", lambda pattern: 1024 if pattern == r"\bvllm\b" else None)

    result = vllm.observe()

    assert result["status"] == "unreachable"
    assert result["process_bytes"] == 1024
    assert result["models"] == []
    assert "runtime overhead" in result["note"]


def test_process_rss_fallback_matches_vllm_process_commands(monkeypatch):
    out = (
        "  2048 /venv/bin/vllm serve model-id\n"
        "  1024 /venv/bin/python -m vllm.entrypoints.openai.api_server\n"
        "  512 /bin/zsh\n"
    )
    monkeypatch.setattr(memory.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=out))

    assert memory.process_rss(r"\bvllm\b") == 3072 * 1024


def test_observe_ignores_non_vllm_openai_models_endpoint(monkeypatch):
    monkeypatch.setattr(
        vllm.httpx,
        "get",
        lambda *args, **kwargs: _response({"object": "list", "data": [{"id": "basal-model"}]}),
    )
    monkeypatch.setattr(memory, "process_rss", lambda _: None)

    assert vllm.observe() is None


def test_observe_returns_none_when_runtime_is_not_present(monkeypatch):
    monkeypatch.setattr(
        vllm.httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(vllm.httpx.ConnectError("offline")),
    )
    monkeypatch.setattr(memory, "process_rss", lambda _: None)

    assert vllm.observe() is None


def test_observe_marks_unified_host_rss_as_excluding_gpu(monkeypatch):
    monkeypatch.setattr(vllm.httpx, "get", lambda *args, **kwargs: _response(MODELS))
    monkeypatch.setattr(memory, "process_rss", lambda _: None)
    monkeypatch.setattr(memory, "rss_accounting", lambda: "rss_excludes_gpu")

    result = vllm.observe()

    assert result["status"] == "ok"
    assert result["process_bytes"] is None
    assert result["accounting"] == "rss_excludes_gpu"


def test_observe_never_probes_a_non_loopback_configured_url(monkeypatch):
    monkeypatch.setenv("BASAL_UI_VLLM_URL", "http://192.0.2.10:8000")
    monkeypatch.setattr(
        vllm.httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network call")),
    )
    monkeypatch.setattr(memory, "process_rss", lambda _: None)

    assert vllm.observe() is None


def test_observe_reports_zero_legacy_kv_cache_fraction(monkeypatch):
    responses = [
        _response(MODELS),
        _response(text="vllm:kv_cache_usage_perc 0.0\n"),
    ]
    monkeypatch.setattr(vllm.httpx, "get", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(memory, "process_rss", lambda _: None)

    result = vllm.observe()

    assert result["status"] == "ok"
    assert "0.0%" in result["note"]
    assert all(model["cache_bytes"] is None for model in result["models"])
