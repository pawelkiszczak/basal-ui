import httpx
import pytest

from basal_ui import memory
from basal_ui.runtimes import ollama


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _get(payload):
    return lambda *args, **kwargs: Response(payload)


def _refused(*args, **kwargs):
    raise httpx.ConnectError("refused")


def _timeout(*args, **kwargs):
    raise httpx.ReadTimeout("timed out")


def _rss(monkeypatch, value, accounting="resident"):
    monkeypatch.setattr(memory, "process_rss", lambda pattern: value)
    monkeypatch.setattr(memory, "rss_accounting", lambda: accounting)


def test_ollama_rejects_nonlocal_host_when_runtime_is_absent(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://example.org:11434")
    monkeypatch.setattr(ollama.httpx, "get", lambda *args, **kwargs: pytest.fail("network request was attempted"))
    _rss(monkeypatch, None)

    assert ollama.observe() is None


def test_ollama_models_use_total_size_and_process_rss_independently(monkeypatch):
    calls = []
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:18084")
    _rss(monkeypatch, 150)

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response(
            {
                "models": [
                    {"name": "mixed", "size": 100, "size_vram": 40},
                    {"name": "gpu", "size": 60, "size_vram": 60},
                    {"name": "ram", "size": 40, "size_vram": 0},
                ]
            }
        )

    monkeypatch.setattr(ollama.httpx, "get", get)

    result = ollama.observe()

    assert calls == [("http://127.0.0.1:18084/api/ps", {"timeout": 0.75})]
    assert result["status"] == "ok"
    assert result["accounting"] == "resident"
    assert result["process_bytes"] == 150
    assert [(model["id"], model["bytes"], model["pool"]) for model in result["models"]] == [
        ("mixed", 100, None),
        ("gpu", 60, "gpu"),
        ("ram", 40, "ram"),
    ]
    assert result["models"][0]["context_bytes"] is None


def test_ollama_unified_pool_uses_rss_accounting_once(monkeypatch):
    _rss(monkeypatch, 200, "rss_excludes_gpu")
    monkeypatch.setattr(
        ollama.httpx,
        "get",
        _get({"models": [{"model": "llama3", "size": 100, "size_vram": 80}]}),
    )

    result = ollama.observe()

    assert result["accounting"] == "rss_excludes_gpu"
    assert result["process_bytes"] == 200
    assert result["models"][0]["bytes"] == 100
    assert result["models"][0]["pool"] == "unified"


def test_ollama_empty_response_keeps_process_rss_measurement(monkeypatch):
    _rss(monkeypatch, 75)
    monkeypatch.setattr(ollama.httpx, "get", _get({"models": []}))

    result = ollama.observe()

    assert result["status"] == "empty"
    assert result["process_bytes"] == 75
    assert result["models"] == []


def test_ollama_connection_refused_uses_process_rss_fallback(monkeypatch):
    _rss(monkeypatch, 123)
    monkeypatch.setattr(ollama.httpx, "get", _refused)

    result = ollama.observe()

    assert result["status"] == "unreachable"
    assert result["accounting"] == "resident"
    assert result["process_bytes"] == 123
    assert "Process RSS includes runtime overhead" in result["note"]
    assert result["models"] == []


def test_ollama_timeout_uses_process_rss_and_host_accounting(monkeypatch):
    _rss(monkeypatch, 123, "rss_excludes_gpu")
    monkeypatch.setattr(ollama.httpx, "get", _timeout)

    result = ollama.observe()

    assert result["status"] == "unreachable"
    assert result["accounting"] == "rss_excludes_gpu"
    assert result["process_bytes"] == 123


def test_ollama_invalid_api_response_is_unavailable_without_rss(monkeypatch):
    _rss(monkeypatch, None)
    monkeypatch.setattr(ollama.httpx, "get", _get({"not_models": []}))

    result = ollama.observe()

    assert result["status"] == "unavailable"
    assert result["process_bytes"] is None


def test_ollama_is_absent_when_api_and_process_are_absent(monkeypatch):
    _rss(monkeypatch, None)
    monkeypatch.setattr(ollama.httpx, "get", _refused)

    assert ollama.observe() is None


def test_missing_ollama_measurements_remain_unknown(monkeypatch):
    _rss(monkeypatch, 0)
    monkeypatch.setattr(ollama.httpx, "get", _get({"models": [{"name": "qwen:7b"}]}))

    result = ollama.observe()

    assert result["status"] == "ok"
    assert result["process_bytes"] == 0
    assert result["models"][0]["bytes"] is None
    assert result["models"][0]["context_bytes"] is None


def test_ollama_provider_is_registered():
    assert ollama.observe in memory.PROVIDERS
