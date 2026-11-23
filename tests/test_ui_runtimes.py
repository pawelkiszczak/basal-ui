import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest

from basal_ui import memory
from basal_ui.runtimes import _probe, lmstudio


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
