import httpx

from basal_ui import memory
from basal_ui.runtimes import sglang


class Response:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


def test_sglang_confirms_model_and_preserves_unavailable_byte_fields(monkeypatch):
    calls = []
    monkeypatch.setenv("BASAL_UI_SGLANG_URL", "http://127.0.0.1:30123/")
    monkeypatch.setattr(
        sglang.httpx,
        "get",
        lambda url, **kwargs: calls.append((url, kwargs)) or Response({"model_path": "Qwen/Qwen3-8B"}),
    )
    monkeypatch.setattr(
        memory,
        "process_rss",
        lambda pattern: 0 if pattern == r"\bsglang\.launch_server\b" else None,
    )

    observation = sglang.observe()
    assert calls == [("http://127.0.0.1:30123/get_model_info", {"timeout": 0.75})]
    assert observation == {
        "runtime": "sglang",
        "label": "SGLang",
        "status": "ok",
        "accounting": memory.rss_accounting(),
        "process_bytes": 0,
        "models": [
            {
                "id": "Qwen/Qwen3-8B",
                "bytes": None,
                "pool": None,
                "context_bytes": None,
                "cache_bytes": None,
                "cold_cache_bytes": None,
            }
        ],
        "note": "Byte-valued SGLang model memory is not available. Process RSS includes runtime overhead.",
    }


def test_process_fallback_is_identifiable_when_model_info_is_unavailable(monkeypatch):
    monkeypatch.delenv("BASAL_UI_SGLANG_URL", raising=False)

    def get(url, **kwargs):
        assert url == "http://127.0.0.1:30000/get_model_info"
        assert kwargs == {"timeout": 0.75}
        raise httpx.ConnectError("server unavailable")

    monkeypatch.setattr(sglang.httpx, "get", get)
    monkeypatch.setattr(memory, "process_rss", lambda pattern: 1234)

    observation = sglang.observe()

    assert observation["runtime"] == "sglang"
    assert observation["status"] == "unavailable"
    assert observation["process_bytes"] == 1234
    assert observation["models"] == []
    assert "includes runtime overhead" in observation["note"]


def test_absent_runtime_returns_none_and_non_loopback_url_is_never_contacted(monkeypatch):
    monkeypatch.setenv("BASAL_UI_SGLANG_URL", "http://example.com:30000")
    monkeypatch.setattr(sglang.httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(memory, "process_rss", lambda pattern: None)

    assert sglang.observe() is None


def test_invalid_model_info_does_not_claim_a_loaded_model(monkeypatch):
    monkeypatch.setenv("BASAL_UI_SGLANG_URL", "http://localhost:30000")
    monkeypatch.setattr(sglang.httpx, "get", lambda *args, **kwargs: Response({"model_path": ""}))
    monkeypatch.setattr(memory, "process_rss", lambda pattern: None)

    assert sglang.observe() is None


def test_sglang_provider_is_registered():
    assert sglang.observe in memory.PROVIDERS
