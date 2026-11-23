import httpx
import pytest

from basal_ui import memory
from basal_ui.runtimes import _probe, omlx


@pytest.fixture(autouse=True)
def fake_json_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(omlx, "_settings_path", lambda: tmp_path / "missing-settings.json")

    def get_json(url, deadline, started):
        response = httpx.get(url, timeout=deadline)
        response.raise_for_status()
        return response.json()

    monkeypatch.setattr(_probe, "get_json", get_json)


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://127.0.0.1:8000/admin/api/stats")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("API unavailable", request=request, response=response)

    def json(self):
        return self.payload


def _stats(models, cache_models=()):
    return {
        "active_models": {"models": models},
        "runtime_cache": {"models": list(cache_models)},
    }


def test_origin_reads_configured_loopback_host_and_port(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"server":{"host":"::1","port":18082}}')
    monkeypatch.setattr(omlx, "_settings_path", lambda: path)

    assert omlx._origin() == "http://[::1]:18082"


def test_omlx_skips_rank_local_cache_tiers(monkeypatch):
    payload = _stats(
        [{"id": "distributed", "is_loading": False, "actual_size": 1024}],
        [
            {
                "id": "distributed",
                "cache_tier": "rank-prompt-snapshot",
                "hot_cache_size_bytes": 0,
                "total_size_bytes": 0,
            }
        ],
    )
    monkeypatch.setattr(_probe, "get_json", lambda *args: payload)
    monkeypatch.setattr(memory, "process_rss", lambda pattern: 4096)

    model = omlx.observe()["models"][0]

    assert model["cache_bytes"] is None
    assert model["cold_cache_bytes"] is None


def test_omlx_connection_refusal_does_not_probe_health(monkeypatch):
    calls = []

    def get_json(url, *args):
        calls.append(url)
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(_probe, "get_json", get_json)
    monkeypatch.setattr(memory, "process_rss", lambda pattern: 4096)

    result = omlx.observe()

    assert calls == ["http://127.0.0.1:8000/admin/api/stats"]
    assert result["status"] == "unreachable"


def test_omlx_rejects_non_loopback_bind_without_network_probe(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"server":{"host":"0.0.0.0","port":8000}}')
    monkeypatch.setattr(omlx, "_settings_path", lambda: path)
    monkeypatch.setattr(_probe, "get_json", lambda *args: pytest.fail("must not probe non-loopback"))
    monkeypatch.setattr(memory, "process_rss", lambda pattern: 4096)

    result = omlx.observe()

    assert result["status"] == "unavailable"
    assert result["process_bytes"] == 4096


def test_omlx_reports_loaded_models_and_measured_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(omlx, "_origin", lambda: "http://127.0.0.1:8000")
    monkeypatch.setattr(
        omlx.httpx,
        "get",
        lambda url, **kwargs: (
            calls.append((url, kwargs))
            or Response(
                _stats(
                    [
                        {"id": "small-model", "is_loading": False, "actual_size": 1234},
                        {"id": "loading", "is_loading": True},
                    ],
                    [{"id": "small-model", "hot_cache_size_bytes": 0, "total_size_bytes": 4096}],
                )
            )
        ),
    )
    monkeypatch.setattr(memory, "process_rss", lambda pattern: 8192)

    result = omlx.observe()

    assert calls == [("http://127.0.0.1:8000/admin/api/stats", {"timeout": 0.75})]
    assert result["status"] == "ok"
    assert result["process_bytes"] == 8192
    assert result["models"] == [
        {
            "id": "small-model",
            "bytes": 1234,
            "size_estimated": False,
            "pool": "unified",
            "context_bytes": None,
            "cache_bytes": 0,
            "cold_cache_bytes": 4096,
        },
        {"id": "loading", "state": "loading"},
    ]


def test_omlx_empty_models_remain_measured_zero_cache(monkeypatch):
    monkeypatch.setattr(omlx.httpx, "get", lambda *args, **kwargs: Response(_stats([])))
    monkeypatch.setattr(memory, "process_rss", lambda pattern: 0)

    result = omlx.observe()

    assert result["status"] == "ok"
    assert result["process_bytes"] == 0
    assert result["models"] == []


def test_omlx_api_failure_falls_back_to_rss_and_marks_details_unavailable(monkeypatch):
    monkeypatch.setattr(omlx.httpx, "get", lambda *args, **kwargs: Response({}, status_code=401))
    monkeypatch.setattr(memory, "process_rss", lambda pattern: 2048)

    result = omlx.observe()

    assert result["status"] == "unavailable"
    assert result["process_bytes"] == 2048
    assert result["models"] == []
    assert "runtime overhead" in result["note"]


def test_omlx_health_fallback_reports_loaded_count_and_memory_total(monkeypatch):
    calls = []
    health = {
        "status": "healthy",
        "engine_pool": {
            "model_count": 11,
            "loaded_count": 3,
            "current_model_memory": 77_995_381_990,
        },
    }

    def get(url, **kwargs):
        calls.append(url)
        return Response({}, status_code=401) if url.endswith("/admin/api/stats") else Response(health)

    monkeypatch.setattr(omlx, "_origin", lambda: "http://127.0.0.1:18082")
    monkeypatch.setattr(omlx.httpx, "get", get)
    observed_patterns = []
    monkeypatch.setattr(memory, "process_rss", lambda pattern: observed_patterns.append(pattern) or 8192)

    result = omlx.observe()

    assert calls == ["http://127.0.0.1:18082/admin/api/stats", "http://127.0.0.1:18082/health"]
    assert observed_patterns == [r"^(?:.*/)?omlx-server(?:\s|$)"]
    assert result == {
        "runtime": "omlx",
        "label": "oMLX",
        "status": "ok",
        "accounting": "rss_excludes_gpu",
        "process_bytes": 8192,
        "models": [
            {
                "id": "3 loaded models",
                "aggregate": 3,
                "bytes": 77_995_381_990,
                "size_estimated": True,
                "pool": "unified",
                "context_bytes": None,
                "cache_bytes": None,
                "cold_cache_bytes": None,
            }
        ],
        "note": omlx._HEALTH_NOTE,
    }


def test_omlx_health_endpoint_shape_does_not_identify_other_local_servers(monkeypatch):
    def get(url, **kwargs):
        if url.endswith("/admin/api/stats"):
            return Response({}, status_code=401)
        return Response({"status": "ok", "models": ["gateway-model"]})

    monkeypatch.setattr(omlx, "_origin", lambda: "http://127.0.0.1:8000")
    monkeypatch.setattr(omlx.httpx, "get", get)
    monkeypatch.setattr(memory, "process_rss", lambda pattern: None)

    assert omlx.observe() is None


def test_omlx_health_with_no_loaded_models_preserves_measured_zero(monkeypatch):
    health = {
        "status": "healthy",
        "engine_pool": {"model_count": 11, "loaded_count": 0, "current_model_memory": 0},
    }
    monkeypatch.setattr(omlx, "_origin", lambda: "http://127.0.0.1:8000")

    def get(url, **kwargs):
        return Response({}, status_code=401) if url.endswith("/admin/api/stats") else Response(health)

    monkeypatch.setattr(omlx.httpx, "get", get)
    monkeypatch.setattr(memory, "process_rss", lambda pattern: 0)

    result = omlx.observe()

    assert result["status"] == "ok"
    assert result["models"][0]["id"] == "0 loaded models"
    assert result["models"][0]["bytes"] == 0
    assert result["process_bytes"] == 0


def test_omlx_unknown_process_and_cache_measurements_stay_unknown(monkeypatch):
    monkeypatch.setattr(
        omlx.httpx,
        "get",
        lambda *args, **kwargs: Response(_stats([{"id": "model", "is_loading": False}])),
    )
    monkeypatch.setattr(memory, "process_rss", lambda pattern: None)

    result = omlx.observe()

    assert result["process_bytes"] is None
    assert result["models"][0]["cache_bytes"] is None
    assert result["models"][0]["cold_cache_bytes"] is None


def test_omlx_not_present_returns_none_when_api_and_process_are_absent(monkeypatch):
    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(omlx.httpx, "get", unavailable)
    monkeypatch.setattr(memory, "process_rss", lambda pattern: None)

    assert omlx.observe() is None
