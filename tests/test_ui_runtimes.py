import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest

from basal_ui import memory
from basal_ui.runtimes import _probe, lmstudio, ollama, sglang, vllm

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


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch, tmp_path):
    monkeypatch.setattr(lmstudio, "CONFIG_PATH", tmp_path / "http-server-config.json")


def _set_process(monkeypatch, size=42):
    monkeypatch.setattr(memory, "rss_accounting", lambda: "rss_excludes_gpu")
    monkeypatch.setattr(lmstudio, "_root_pid", lambda: 100)
    monkeypatch.setattr(_probe, "process_tree_rss", lambda pid: size)


def _set_api(monkeypatch, payload):
    monkeypatch.setattr(_probe, "get_json", lambda url, deadline, started: payload)


def test_provider_can_import_before_memory():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import basal_ui.runtimes.lmstudio; import basal_ui.memory; "
            "assert 'basal_ui.runtimes.lmstudio.observe' in basal_ui.memory.PROVIDERS",
        ],
        check=True,
    )


def test_loaded_instances_keep_distinct_ids_and_unknown_pool(monkeypatch):
    _set_process(monkeypatch, 8_000)
    _set_api(
        monkeypatch,
        {
            "models": [
                {
                    "key": "publisher/local-model",
                    "loaded_instances": [{"instance_id": "one"}, {"identifier": "two"}],
                }
            ]
        },
    )

    result = lmstudio.observe()

    assert result["status"] == "ok"
    assert result["process_bytes"] == 8_000
    assert [model["id"] for model in result["models"]] == ["one", "two"]
    assert all(model["name"] == "publisher/local-model" for model in result["models"])
    assert all(model["pool"] is None for model in result["models"])


def test_api_port_uses_configured_loopback_and_probes_without_process(monkeypatch):
    _set_process(monkeypatch, None)
    lmstudio.CONFIG_PATH.write_text('{"port": 1235, "networkInterface": "0.0.0.0"}', encoding="utf-8")
    requests = []

    def get_json(url, deadline, started):
        requests.append((url, deadline))
        return {"models": []}

    monkeypatch.setattr(_probe, "get_json", get_json)
    result = lmstudio.observe()

    assert requests == [("http://127.0.0.1:1235/api/v1/models", 1.0)]
    assert result["process_bytes"] is None
    assert result["status"] == "ok"
    assert result["models"] == []


def test_transport_failure_is_unreachable_but_http_failure_is_unavailable(monkeypatch):
    _set_process(monkeypatch, 3_000)

    def fail(*args):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(_probe, "get_json", fail)
    assert lmstudio.observe()["status"] == "unreachable"

    def unauthorized(*args):
        raise httpx.HTTPStatusError("unauthorized", request=None, response=None)

    monkeypatch.setattr(_probe, "get_json", unauthorized)
    result = lmstudio.observe()
    assert result["status"] == "unavailable"
    assert result["process_bytes"] == 3_000


def test_unreadable_models_do_not_claim_empty(monkeypatch):
    _set_process(monkeypatch)
    _set_api(monkeypatch, {"models": [{"key": None, "loaded_instances": "bad"}]})

    assert lmstudio.observe()["status"] == "unavailable"


def test_process_root_matches_names_not_command_arguments(monkeypatch):
    output = (
        "  512 /Applications/LM Studio.app/Contents/MacOS/LM Studio\\n"
        "1024 /Users/example/.lmstudio/.internal/utils/node\\n"
        "  256 /bin/zsh\\n"
        "2048 /usr/local/bin/lmstudio-worker\\n"
    )
    monkeypatch.setattr(lmstudio.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=output))

    assert lmstudio._root_pid() == 512


def test_failed_process_lookup_does_not_suppress_api_probe(monkeypatch):
    monkeypatch.setattr(lmstudio, "_root_pid", lambda: None)
    _set_api(monkeypatch, {"models": []})

    assert lmstudio.observe()["status"] == "ok"
