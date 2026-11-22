from types import SimpleNamespace

import httpx
import pytest

from basal_ui import memory
from basal_ui.runtimes import lmstudio


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch, tmp_path):
    monkeypatch.setattr(lmstudio, "CONFIG_PATH", tmp_path / "http-server-config.json")


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _set_process(monkeypatch, size=42):
    monkeypatch.setattr(memory, "rss_accounting", lambda: "rss_excludes_gpu")
    monkeypatch.setattr(memory, "process_rss", lambda pattern: size)


@pytest.mark.parametrize("accounting", ["rss_excludes_gpu", "resident"])
def test_accounting_basis_tracks_memory_helper(monkeypatch, accounting):
    _set_process(monkeypatch)
    monkeypatch.setattr(memory, "rss_accounting", lambda: accounting)
    monkeypatch.setattr(
        lmstudio.httpx,
        "get",
        lambda *args, **kwargs: Response({"models": [{"key": "sample", "loaded_instances": [{}]}]}),
    )

    result = lmstudio.observe()

    assert result["accounting"] == accounting
    assert result["models"][0]["pool"] == ("unified" if accounting == "rss_excludes_gpu" else "ram")


def test_api_port_uses_config_but_stays_on_loopback(monkeypatch):
    _set_process(monkeypatch)
    lmstudio.CONFIG_PATH.write_text('{"port": 1235, "networkInterface": "0.0.0.0"}', encoding="utf-8")
    requests = []

    def get(url, timeout, trust_env):
        requests.append((url, timeout, trust_env))
        return Response({"models": []})

    monkeypatch.setattr(lmstudio.httpx, "get", get)

    lmstudio.observe()

    assert requests == [("http://127.0.0.1:1235/api/v1/models", 0.5, False)]


def test_invalid_port_config_uses_default_port(monkeypatch):
    _set_process(monkeypatch)
    lmstudio.CONFIG_PATH.write_text('{"port": true}', encoding="utf-8")
    requests = []
    monkeypatch.setattr(
        lmstudio.httpx,
        "get",
        lambda url, timeout, trust_env: requests.append(url) or Response({"models": []}),
    )

    lmstudio.observe()

    assert requests == [lmstudio.API_URL]


def test_loaded_model_uses_api_identity_and_process_rss(monkeypatch):
    _set_process(monkeypatch, 8_000)
    assert lmstudio.observe in memory.PROVIDERS
    requests = []

    def get(url, timeout, trust_env):
        requests.append((url, timeout, trust_env))
        return Response(
            {
                "models": [
                    {
                        "key": "publisher/local-model",
                        "loaded_instances": [{"id": "instance-1", "config": {"context_length": 4096}}],
                    },
                    {"key": "not-loaded", "loaded_instances": []},
                ]
            }
        )

    monkeypatch.setattr(lmstudio.httpx, "get", get)

    result = lmstudio.observe()

    assert requests == [(lmstudio.API_URL, 0.5, False)]
    assert result["runtime"] == "lmstudio"
    assert result["status"] == "ok"
    assert result["accounting"] == "rss_excludes_gpu"
    assert result["process_bytes"] == 8_000
    assert result["models"] == [
        {
            "id": "publisher/local-model",
            "bytes": None,
            "pool": "unified",
            "context_bytes": None,
            "cache_bytes": None,
            "cold_cache_bytes": None,
        }
    ]
    assert "Process RSS includes runtime overhead." in result["note"]


def test_empty_model_list_reports_measured_process_memory(monkeypatch):
    _set_process(monkeypatch, 0)
    monkeypatch.setattr(lmstudio.httpx, "get", lambda *args, **kwargs: Response({"models": []}))

    result = lmstudio.observe()

    assert result["status"] == "empty"
    assert result["process_bytes"] == 0
    assert result["models"] == []
    assert result["accounting"] == "rss_excludes_gpu"


def test_unreachable_api_keeps_process_rss_and_unknown_model_fields(monkeypatch):
    _set_process(monkeypatch, 3_000)

    def fail(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(lmstudio.httpx, "get", fail)

    result = lmstudio.observe()

    assert result["status"] == "unreachable"
    assert result["process_bytes"] == 3_000
    assert result["models"] == []
    assert result["accounting"] == "rss_excludes_gpu"
    assert "model identity, context bytes, and cache usage are unknown" in result["note"]
    assert "Process RSS includes runtime overhead." in result["note"]


def test_absent_process_omits_runtime_without_http_request(monkeypatch):
    _set_process(monkeypatch, None)
    monkeypatch.setattr(lmstudio.httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))

    assert lmstudio.observe() is None


def test_process_rss_matches_lm_studio_process_names(monkeypatch):
    output = (
        "  512 /Applications/LM Studio.app/Contents/MacOS/LM Studio --run-as-service\n"
        "1024 /Users/example/.lmstudio/.internal/utils/node\n"
        "  256 /bin/zsh\n"
    )
    monkeypatch.setattr(memory.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=output))

    assert memory.process_rss(lmstudio.PROCESS_PATTERN) == 1_536 * 1024
