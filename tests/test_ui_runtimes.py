import httpx

from basal_ui import memory
from basal_ui.runtimes import sglang


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
