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
    def get(url, *_args):
        return {"version": "0.5.0"} if url.endswith("/api/version") else payload

    return get


def _refused(*args, **kwargs):
    raise httpx.ConnectError("refused")


def _timeout(*args, **kwargs):
    raise httpx.ReadTimeout("timed out")


def _rss(monkeypatch, value, accounting="resident"):
    monkeypatch.setattr(memory, "rss_accounting", lambda: accounting)
    monkeypatch.setattr(ollama, "_server_pids", lambda: [123] if value is not None else [])
    monkeypatch.setattr(ollama._probe, "process_tree_rss", lambda *_args: value)


def test_ollama_rejects_nonlocal_host_when_runtime_is_absent(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://example.org:11434")
    monkeypatch.setattr(ollama._probe, "get_json", lambda *args, **kwargs: pytest.fail("network request was attempted"))
    _rss(monkeypatch, None)

    assert ollama.observe() is None


def test_ollama_models_use_total_size_and_process_rss_independently(monkeypatch):
    calls = []
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:18084")
    _rss(monkeypatch, 150)

    def get(url, *_args):
        calls.append(url)
        return (
            {"version": "0.5.0"}
            if url.endswith("/api/version")
            else {
                "models": [
                    {"name": "mixed", "size": 100, "size_vram": 40},
                    {"name": "gpu", "size": 60, "size_vram": 60},
                    {"name": "ram", "size": 40, "size_vram": 0},
                ]
            }
        )

    monkeypatch.setattr(ollama._probe, "get_json", get)

    result = ollama.observe()

    assert calls == ["http://127.0.0.1:18084/api/version", "http://127.0.0.1:18084/api/ps"]
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
        ollama._probe, "get_json", _get({"models": [{"model": "llama3", "size": 100, "size_vram": 80}]})
    )

    result = ollama.observe()

    assert result["accounting"] == "rss_excludes_gpu"
    assert result["process_bytes"] == 200
    assert result["models"][0]["bytes"] == 100
    assert result["models"][0]["pool"] == "unified"


def test_ollama_empty_response_keeps_process_rss_measurement(monkeypatch):
    _rss(monkeypatch, 75)
    monkeypatch.setattr(ollama._probe, "get_json", _get({"models": []}))

    result = ollama.observe()

    assert result["status"] == "ok"
    assert result["process_bytes"] == 75
    assert result["models"] == []


def test_ollama_connection_refused_uses_process_rss_fallback(monkeypatch):
    _rss(monkeypatch, 123)
    monkeypatch.setattr(ollama._probe, "get_json", _refused)

    result = ollama.observe()

    assert result["status"] == "unreachable"
    assert result["accounting"] == "resident"
    assert result["process_bytes"] == 123
    assert "Process RSS includes runtime overhead" in result["note"]
    assert result["models"] == []


def test_ollama_timeout_uses_process_rss_and_host_accounting(monkeypatch):
    _rss(monkeypatch, 123, "rss_excludes_gpu")
    monkeypatch.setattr(ollama._probe, "get_json", _timeout)

    result = ollama.observe()

    assert result["status"] == "unreachable"
    assert result["accounting"] == "rss_excludes_gpu"
    assert result["process_bytes"] == 123


def test_ollama_invalid_api_response_is_unavailable_without_rss(monkeypatch):
    _rss(monkeypatch, None)
    monkeypatch.setattr(ollama._probe, "get_json", _get({"not_models": []}))

    result = ollama.observe()

    assert result["status"] == "unavailable"
    assert result["process_bytes"] is None


def test_ollama_is_absent_when_api_and_process_are_absent(monkeypatch):
    _rss(monkeypatch, None)
    monkeypatch.setattr(ollama._probe, "get_json", _refused)

    assert ollama.observe() is None


def test_missing_ollama_measurements_remain_unknown(monkeypatch):
    _rss(monkeypatch, 0)
    monkeypatch.setattr(ollama._probe, "get_json", _get({"models": [{"name": "qwen:7b"}]}))

    result = ollama.observe()

    assert result["status"] == "ok"
    assert result["process_bytes"] == 0
    assert result["models"][0]["bytes"] is None
    assert result["models"][0]["context_bytes"] is None


def test_ollama_provider_is_registered():
    assert "basal_ui.runtimes.ollama.observe" in memory.PROVIDERS


def test_ollama_host_normalization_and_invalid_url_rss(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "localhost")
    assert ollama._url() == "http://localhost:11434"
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0")
    assert ollama._url() == "http://127.0.0.1:11434"
    monkeypatch.setenv("OLLAMA_HOST", "[::]")
    assert ollama._url() == "http://127.0.0.1:11434"
    monkeypatch.setenv("OLLAMA_HOST", "localhost:bad")
    _rss(monkeypatch, 17)
    monkeypatch.setattr(ollama._probe, "get_json", lambda *_args: pytest.fail("invalid URL must skip probing"))

    result = ollama.observe()

    assert result["process_bytes"] == 17
    assert result["status"] == "unavailable"


def test_ollama_skips_models_without_string_identity(monkeypatch):
    _rss(monkeypatch, 12)
    monkeypatch.setattr(
        ollama._probe,
        "get_json",
        _get({"models": [{"name": 7}, {"name": ""}, {"model": "valid", "size": 42}]}),
    )

    result = ollama.observe()

    assert [model["id"] for model in result["models"]] == ["valid"]


def test_ollama_process_matching_avoids_cli_and_argument_false_positives(monkeypatch):
    output = "\n".join(
        (
            "100 ollama /usr/bin/ollama serve",
            "101 ollama /usr/bin/ollama runner",
            "102 ollama_llama_server /usr/bin/ollama_llama_server --model x",
            "103 zsh /bin/zsh cat /tmp/.ollama",
            "104 python /usr/bin/python /tmp/ollama-client",
        )
    )
    monkeypatch.setattr(ollama.subprocess, "run", lambda *_args, **_kwargs: type("Proc", (), {"stdout": output})())

    assert ollama._server_pids() == [100]


def test_ollama_rss_excludes_fleet_runner_pids(monkeypatch):
    monkeypatch.setattr(ollama, "_server_pids", lambda: [200])
    seen = []
    monkeypatch.setattr(ollama._probe, "process_tree_rss", lambda pid, excluded: seen.append(excluded) or 300)
    fleet = type("FleetStub", (), {"memory_snapshot": lambda self: [{"pid": 321}]})()

    assert ollama._rss(fleet) == 300
    assert seen == [{321}]
