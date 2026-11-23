from types import SimpleNamespace

import httpx

from basal_ui import memory
from basal_ui.runtimes import _probe, sglang, vllm

MODELS = {
    "object": "list",
    "data": [
        {"id": "Qwen/Qwen3-8B", "object": "model", "max_model_len": 32768},
        {"id": "meta-llama/Llama-3.1-8B", "object": "model", "max_model_len": 8192},
    ],
}


def _setup(monkeypatch, models=MODELS, metrics="", version=None):
    version = version or {"version": "0.11"}
    calls = []

    def get_json(url, deadline, started):
        calls.append(url)
        if url.endswith("/v1/models"):
            return models
        return version

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, url, **kwargs):
            calls.append(url)
            return SimpleNamespace(raise_for_status=lambda: None, text=metrics)

    monkeypatch.setattr(_probe, "get_json", get_json)
    monkeypatch.setattr(vllm.httpx, "Client", Client)
    monkeypatch.setattr(_probe, "timeout_for", lambda deadline, started: 0.5)
    monkeypatch.setattr(vllm, "_process_bytes", lambda: 512 * 1024**2)
    monkeypatch.setattr(memory, "rss_accounting", lambda: "resident")
    monkeypatch.setenv("BASAL_UI_VLLM_URL", "http://127.0.0.1:18000/")
    return calls


def test_observe_reads_models_and_per_engine_cache_without_bytes(monkeypatch):
    metrics = (
        'vllm:gpu_cache_usage_perc{engine="0",model_name="Qwen"} 0.375\n'
        'vllm:gpu_cache_usage_perc{engine="1",model_name="Qwen"} 0.625\n'
    )
    calls = _setup(monkeypatch, metrics=metrics)

    result = vllm.observe()

    assert calls == [
        "http://127.0.0.1:18000/v1/models",
        "http://127.0.0.1:18000/version",
        "http://127.0.0.1:18000/metrics",
    ]
    assert result["status"] == "ok"
    assert result["process_bytes"] == 512 * 1024**2
    assert [model["id"] for model in result["models"]] == ["Qwen/Qwen3-8B", "meta-llama/Llama-3.1-8B"]
    assert result["cache"] == [
        {"engine": "0", "usage_percent": 37.5},
        {"engine": "1", "usage_percent": 62.5},
    ]
    assert all(model["context_bytes"] is None and model["cache_bytes"] is None for model in result["models"])
    assert "engine 0 37.5%" in result["note"]
    assert "engine 1 62.5%" in result["note"]


def test_adapter_without_max_length_does_not_hide_base_models(monkeypatch):
    models = {"data": [{"id": "base", "max_model_len": 4096}, {"id": "adapter", "parent": "base"}]}
    _setup(monkeypatch, models=models)

    result = vllm.observe()

    assert [model["id"] for model in result["models"]] == ["base", "adapter"]


def test_sglang_openai_compatible_models_are_not_vllm(monkeypatch):
    _setup(monkeypatch, version={"sglang_version": "0.5.0"}, metrics="# no vllm metrics\n")
    monkeypatch.setattr(vllm, "_process_bytes", lambda: None)

    assert vllm.observe() is None


def test_authenticated_models_endpoint_still_collects_metrics(monkeypatch):
    calls = []

    def get_json(url, deadline, started):
        calls.append(url)
        if url.endswith("/v1/models"):
            request = vllm.httpx.Request("GET", url)
            response = vllm.httpx.Response(401, request=request)
            raise vllm.httpx.HTTPStatusError("Unauthorized", request=request, response=response)
        raise ValueError("not vLLM version response")

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, url, **kwargs):
            calls.append(url)
            return SimpleNamespace(raise_for_status=lambda: None, text="vllm:other_metric 1\n")

    monkeypatch.setattr(_probe, "get_json", get_json)
    monkeypatch.setattr(vllm.httpx, "Client", Client)
    monkeypatch.setattr(_probe, "timeout_for", lambda deadline, started: 0.5)
    monkeypatch.setattr(vllm, "_process_bytes", lambda: 100)
    monkeypatch.setattr(memory, "rss_accounting", lambda: "resident")
    monkeypatch.setenv("BASAL_UI_VLLM_URL", "http://127.0.0.1:8000")

    result = vllm.observe()

    assert result["status"] == "unavailable"
    assert result["models"] == []
    assert result["process_bytes"] == 100
    assert calls[-1] == "http://127.0.0.1:8000/metrics"


def test_invalid_url_skips_network_but_keeps_process_measurement(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setenv("BASAL_UI_VLLM_URL", "http://127.0.0.1:8000?#")
    monkeypatch.setattr(vllm, "_process_bytes", lambda: 100)
    monkeypatch.setattr(_probe, "get_json", lambda *args: (_ for _ in ()).throw(AssertionError("network")))

    result = vllm.observe()

    assert result["status"] == "unavailable"
    assert result["process_bytes"] == 100


def test_invalid_json_is_unavailable_but_transport_failure_is_unreachable(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setattr(vllm, "_process_bytes", lambda: 100)
    monkeypatch.setattr(_probe, "get_json", lambda *args: (_ for _ in ()).throw(ValueError("bad JSON")))

    assert vllm.observe()["status"] == "unavailable"

    monkeypatch.setattr(
        _probe,
        "get_json",
        lambda *args: (_ for _ in ()).throw(vllm.httpx.ConnectError("offline")),
    )
    assert vllm.observe()["status"] == "unreachable"


def test_process_tree_rss_excludes_fleet_workers_and_matches_external_titles(monkeypatch):
    output = (
        "100 1 basal-serve /venv/bin/basal-serve --mode vllm\n"
        "101 100 VLLM::EngineCore VLLM::EngineCore\n"
        "110 1 python /venv/bin/python -m basal.server --mode vllm\n"
        "111 110 VLLM::EngineCore VLLM::EngineCore\n"
        "200 1 vllm /venv/bin/vllm serve model\n"
        "201 200 VLLM::EngineCore VLLM::EngineCore\n"
        "300 1 zsh /bin/zsh -c 'vllm serve'\n"
        "400 1 VLLM::EngineCore VLLM::EngineCore\n"
    )
    monkeypatch.setattr(vllm.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=output))
    measured = []
    monkeypatch.setattr(_probe, "process_tree_rss", lambda pid, excluded: measured.append((pid, excluded)) or 100)

    assert vllm._process_bytes() == 200
    assert [pid for pid, _ in measured] == [200, 400]
    assert measured[0][1] == {100, 101, 110, 111}


def test_provider_import_before_memory_does_not_cycle():
    import subprocess
    import sys

    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import basal_ui.runtimes.vllm; assert 'basal_ui.memory' not in sys.modules; "
            "import basal_ui.memory",
        ],
        check=True,
    )


def test_sglang_confirms_model_and_preserves_unavailable_byte_fields(monkeypatch):
    calls = []
    monkeypatch.setenv("BASAL_UI_SGLANG_URL", "http://127.0.0.1:30123/")
    monkeypatch.setattr(
        sglang._probe,
        "get_json",
        lambda url, deadline, started: calls.append((url, deadline)) or {"model_path": "Qwen/Qwen3-8B"},
    )
    monkeypatch.setattr(sglang, "_process_bytes", lambda excluded: 0)

    observation = sglang.observe()

    assert calls[0][0] == "http://127.0.0.1:30123/model_info"
    assert calls[0][1] == sglang._PROBE_DEADLINE
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


def test_model_info_falls_back_to_legacy_endpoint_only_on_404(monkeypatch):
    monkeypatch.setenv("BASAL_UI_SGLANG_URL", "http://localhost:30000")
    calls = []

    def get_json(url, deadline, started):
        calls.append(url)
        if url.endswith("/model_info"):
            response = httpx.Response(404, request=httpx.Request("GET", url))
            raise httpx.HTTPStatusError("not found", request=response.request, response=response)
        return {"model_path": "model"}

    monkeypatch.setattr(sglang._probe, "get_json", get_json)
    monkeypatch.setattr(sglang, "_process_bytes", lambda excluded: 1234)

    assert sglang.observe()["models"][0]["id"] == "model"
    assert calls == ["http://localhost:30000/model_info", "http://localhost:30000/get_model_info"]


def test_invalid_urls_skip_only_probe_and_still_measure_process(monkeypatch):
    for url in ("http://example.com:30000", "http://localhost:not-a-port"):
        monkeypatch.setenv("BASAL_UI_SGLANG_URL", url)
        monkeypatch.setattr(
            sglang._probe,
            "get_json",
            lambda *args: (_ for _ in ()).throw(AssertionError("invalid URL was probed")),
        )
        monkeypatch.setattr(sglang, "_process_bytes", lambda excluded: 1234)

        observation = sglang.observe()

        assert observation["status"] == "unavailable"
        assert observation["process_bytes"] == 1234
        assert "URL is invalid" in observation["note"]


def test_process_fallback_is_identifiable_when_model_info_is_unavailable(monkeypatch):
    monkeypatch.delenv("BASAL_UI_SGLANG_URL", raising=False)
    monkeypatch.setattr(
        sglang._probe, "get_json", lambda *args: (_ for _ in ()).throw(httpx.ConnectError("server unavailable"))
    )
    monkeypatch.setattr(sglang, "_process_bytes", lambda excluded: 1234)

    observation = sglang.observe()
    assert observation["runtime"] == "sglang"
    assert observation["status"] == "unreachable"
    assert observation["process_bytes"] == 1234
    assert observation["models"] == []
    assert "includes runtime overhead" in observation["note"]


def test_sglang_root_matches_launchers_and_known_worker_titles(monkeypatch):
    output = (
        "10 1 unrelated python /tmp/sglang.launch_server-helper\n"
        "20 1 python /venv/bin/python -m sglang.launch_server --port 30000\n"
        "30 20 sglang::scheduler sglang::scheduler\n"
        "40 1 sglang sglang serve --port 30001\n"
    )
    monkeypatch.setattr(sglang.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"stdout": output})())
    assert sglang._root_pid() == 20

    worker_only = "30 1 sglang::detokenizer sglang::detokenizer\n"
    monkeypatch.setattr(sglang.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"stdout": worker_only})())
    assert sglang._root_pid() == 30


def test_process_tree_receives_fleet_owned_pids(monkeypatch):
    monkeypatch.setattr(sglang, "_root_pid", lambda: 100)
    calls = []
    monkeypatch.setattr(sglang._probe, "process_tree_rss", lambda root, excluded: calls.append((root, excluded)) or 5)

    assert sglang._process_bytes({200}) == 5
    assert calls == [(100, {200})]


def test_sglang_provider_is_registered_lazily():
    assert "basal_ui.runtimes.sglang.observe" in memory.PROVIDERS


def test_sglang_module_can_be_imported_before_memory():
    import subprocess
    import sys

    subprocess.run(
        [
            sys.executable,
            "-c",
            "import basal_ui.runtimes.sglang; import basal_ui.memory; "
            "assert 'basal_ui.runtimes.sglang.observe' in basal_ui.memory.PROVIDERS",
        ],
        check=True,
    )


def test_memory_snapshot_excludes_fleet_runner_pids_from_sglang_tree(monkeypatch):
    fleet = type("Fleet", (), {"memory_snapshot": lambda self: [{"pid": 42}]})()
    monkeypatch.setattr(
        memory,
        "_host",
        lambda _: {
            "ram_total": 100,
            "ram_used": 50,
            "unified": False,
            "gpu": [],
            "accounting": "resident",
        },
    )
    monkeypatch.setattr(sglang._probe, "get_json", lambda *args: (_ for _ in ()).throw(httpx.ConnectError("down")))
    monkeypatch.setattr(sglang, "_root_pid", lambda: 100)
    calls = []
    monkeypatch.setattr(
        sglang._probe,
        "process_tree_rss",
        lambda root, excluded: calls.append((root, excluded)) or 10,
    )

    result = memory.snapshot(fleet, ["basal_ui.runtimes.sglang.observe"], hardware_fn=lambda: None)

    assert calls == [(100, {42})]
    assert result["runtimes"][1]["process_bytes"] == 10
