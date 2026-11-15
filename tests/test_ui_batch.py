import json
import time

import pytest
from fastapi.testclient import TestClient

from basal_ui import settings
from basal_ui.app import create_app
from basal_ui.fleet import Fleet, Runner
from basal_ui.hardware import Hardware
from basal_ui.history import History
from basal_ui.stats import Stats

HW = Hardware("cpu", "cpu", 1024**4, None, None, frozenset())


@pytest.fixture
def client(tmp_path, monkeypatch, child):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    fleet = Fleet(
        models_dir_fn=lambda: tmp_path,
        platform_fn=lambda: "cpu",
        settings_mod=settings,
        hardware_fn=lambda: HW,
        stats=Stats(tmp_path / "s.json"),
        history=History(tmp_path / "h.jsonl"),
    )
    runner = Runner()
    runner.state, runner.model, runner.base_url = "ready", "mini", child.url
    runner.models_entry = {"early_exit": []}
    fleet.runners["mini"] = runner
    app = create_app(
        catalog_fn=lambda **_: ([], "test"),
        hardware_fn=lambda: HW,
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
    )
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def wait_batch(client, timeout=30):
    deadline = time.monotonic() + timeout
    while client.get("/api/status").json()["jobs"]["batch"]["running"] and time.monotonic() < deadline:
        time.sleep(0.05)
    return client.get("/api/status").json()["jobs"]["batch"]


def test_batch_answers_every_line_and_serves_the_rows_and_the_file(client, child, tmp_path):
    """basal-run runs as a subprocess against the model's child; its summary is the job's result."""
    source = tmp_path / "items.jsonl"
    source.write_text(
        "\n".join(
            json.dumps({"id": f"i{n}", "state": "s", "question": "q?", "options": ["a", "b"], "gold": g})
            for n, g in enumerate([0, 1, 0])
        )
        + "\n"
    )
    assert client.get("/api/batch/rows").status_code == 404
    started = client.post("/api/batch", json={"id": "mini", "input": str(source), "concurrency": 2})
    assert started.status_code == 202
    job = wait_batch(client)
    assert job["phase"] == "done" and (job["done"], job["total"]) == (3, 3), job
    result = job["result"]
    assert (result["items"], result["errors"], result["accuracy"]) == (3, 0, round(2 / 3, 4))
    assert result["output"] == str(tmp_path / "items.answers.jsonl")
    assert [path for path, _ in child.seen] == ["/v1/systemone"] * 3  # straight to the child
    rows = client.get("/api/batch/rows", params={"limit": 2}).json()
    assert rows["total"] == 3 and [r["id"] for r in rows["rows"]] == ["i0", "i1"]
    assert [r["correct"] for r in client.get("/api/batch/rows", params={"offset": 2}).json()["rows"]] == [True]
    download = client.get("/api/batch/download")
    assert download.status_code == 200 and len(download.text.splitlines()) == 3
    assert client.get("/api/stats").json()["models"] == {}  # not counted in the statistics


def test_batch_refuses_output_that_resolves_to_input(client, tmp_path):
    source = tmp_path / "same.jsonl"
    source.write_text('{"state":"s","question":"q","options":["a","b"]}\n')
    response = client.post("/api/batch", json={"id": "mini", "input": str(source), "output": str(source)})
    assert response.status_code == 400 and "must not be the input file" in response.json()["detail"]


def test_cancelling_a_batch_stops_basal_run_and_writes_no_output(client, child, tmp_path):
    child.gate.clear()  # the child holds every request until the test opens it
    source = tmp_path / "items.jsonl"
    source.write_text('{"state": "s", "question": "q?", "options": ["a", "b"]}\n')
    assert client.post("/api/batch", json={"id": "mini", "input": str(source)}).status_code == 202
    deadline = time.monotonic() + 30
    while not child.seen and time.monotonic() < deadline:  # basal-run is running and waiting for the answer
        time.sleep(0.05)
    assert client.delete("/api/batch").status_code == 200
    job = wait_batch(client)
    child.gate.set()
    assert job["phase"] == "cancelled"
    assert not (tmp_path / "items.answers.jsonl").exists()


@pytest.mark.parametrize(
    "body, status, message",
    [
        ({"id": "mini", "input": "/nonexistent/x.jsonl"}, 400, "cannot read"),
        ({"id": "mini", "input": "BAD"}, 400, "line 1"),
        ({"id": "other", "input": "GOOD"}, 404, "not loaded"),
        ({"id": "mini", "input": "GOOD", "early_exit": "0.99"}, 400, "no early exits"),
        ({"id": "mini", "input": "GOOD", "output": "/nonexistent/dir/out.jsonl"}, 400, "cannot write"),
    ],
)
def test_batch_refuses_bad_input(client, tmp_path, body, status, message):
    good, bad = tmp_path / "good.jsonl", tmp_path / "bad.jsonl"
    good.write_text('{"state": "s", "question": "q?", "options": ["a", "b"]}\n')
    bad.write_text("not json\n")
    body = {**body, "input": {"GOOD": str(good), "BAD": str(bad)}.get(body["input"], body["input"])}
    response = client.post("/api/batch", json=body)
    assert response.status_code == status and message in response.json()["detail"]


def test_batch_strips_question_metadata_without_rounding_or_touching_input(tmp_path, monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace

    from basal_ui import batch

    source = tmp_path / "source.jsonl"
    original = (
        '{"state":{"value":0.123456789012345678,"_mcp_source":"business"},'
        '"questions":{"q":{"type":"tool","_mcp_source":{"url":"local"},'
        '"tools":[{"name":"weather","inputSchema":{"_mcp_source":"schema"}}]}}}\r\n'
        "not-json\r\n"
    )
    source.write_bytes(original.encode())
    received = {}

    def run(job, module, args):
        path = Path(args[args.index("--input") + 1])
        received["path"] = path
        received["bytes"] = path.read_bytes()
        received["lines"] = received["bytes"].decode().splitlines()
        return {"items": 2}

    from pathlib import Path

    monkeypatch.setattr(batch, "run_tool", run)
    job = SimpleNamespace(progress=lambda *args: None, check=lambda: None)
    fleet = SimpleNamespace(ready=lambda _: SimpleNamespace(base_url="http://127.0.0.1:1"))
    result = batch.run_batch(job, fleet, "model", source, tmp_path / "out.jsonl")
    row = json.loads(received["lines"][0], parse_float=Decimal)
    assert row["state"]["value"] == Decimal("0.123456789012345678")
    assert row["state"]["_mcp_source"] == "business"
    assert "_mcp_source" not in row["questions"]["q"]
    assert row["questions"]["q"]["tools"][0]["inputSchema"]["_mcp_source"] == "schema"
    assert received["lines"][1] == "not-json" and source.read_bytes() == original.encode()
    assert received["bytes"].endswith(b"\r\nnot-json\r\n")
    assert result["input"] == str(source) and not received["path"].exists()


def test_batch_removes_sanitized_input_when_runner_fails(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from basal_ui import batch

    source = tmp_path / "source.jsonl"
    source.write_text('{"questions":{"q":{"_mcp_source":{"url":"local"}}}}\n')
    owned = []

    def fail(job, module, args):
        owned.append(Path(args[args.index("--input") + 1]))
        assert owned[0].exists()
        raise RuntimeError("cancelled")

    monkeypatch.setattr(batch, "run_tool", fail)
    with pytest.raises(RuntimeError, match="cancelled"):
        batch.run_batch(
            SimpleNamespace(progress=lambda *args: None, check=lambda: None),
            SimpleNamespace(ready=lambda _: SimpleNamespace(base_url="http://127.0.0.1:1")),
            "model",
            source,
            tmp_path / "out.jsonl",
        )
    assert not owned[0].exists() and source.exists()


def test_ordinary_batch_runs_native_when_temp_copy_unavailable(client, child, tmp_path, monkeypatch):
    from basal_ui import batch

    source = tmp_path / "ordinary.jsonl"
    source.write_text('{"state":"s","question":"q","options":["a","b"]}\n')

    def no_temp(*args, **kwargs):
        raise OSError("temporary storage unavailable")

    monkeypatch.setattr(batch.tempfile, "TemporaryDirectory", no_temp)
    assert client.post("/api/batch", json={"id": "mini", "input": str(source)}).status_code == 202
    job = wait_batch(client)
    assert job["phase"] == "done", job
    assert len(child.seen) == 1 and job["result"]["input"] == str(source)


@pytest.mark.parametrize("checkpoint", [1, 4, 6])
def test_batch_cancellation_at_scan_copy_and_spawn_boundary(tmp_path, monkeypatch, checkpoint):
    import threading
    from types import SimpleNamespace

    from basal_ui import batch
    from basal_ui.jobs import Cancelled

    source = tmp_path / "source.jsonl"
    source.write_text('{"questions":{"q":{"_mcp_source":{"url":"local"}}}}\n{}\n')
    entered, release = threading.Event(), threading.Event()
    spawned, errors = [], []

    class Job:
        calls = 0

        def progress(self, *args):
            pass

        def check(self):
            self.calls += 1
            if self.calls == checkpoint:
                entered.set()
                assert release.wait(5)
                raise Cancelled()

    monkeypatch.setattr(batch.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(batch, "run_tool", lambda *args: spawned.append(True))

    def execute():
        try:
            batch.run_batch(
                Job(),
                SimpleNamespace(ready=lambda _: SimpleNamespace(base_url="http://127.0.0.1:1")),
                "m",
                source,
                tmp_path / "out",
            )
        except Cancelled:
            errors.append("cancelled")

    worker = threading.Thread(target=execute)
    worker.start()
    assert entered.wait(5)
    release.set()
    worker.join(5)
    assert not worker.is_alive() and errors == ["cancelled"] and not spawned
    assert not list(tmp_path.glob("basal-ui-batch-*"))


def test_batch_deep_valid_row_reaches_native_input_without_provenance(tmp_path, monkeypatch):
    from decimal import Decimal
    from pathlib import Path
    from types import SimpleNamespace

    from basal_ui import batch

    state = {"_mcp_source": "business", "value": "leaf"}
    for _ in range(600):
        state = {"child": state}
    body = {
        "state": state,
        "questions": {
            "q": {
                "_mcp_source": {"url": "local"},
                "tools": [{"name": "weather", "inputSchema": {"_mcp_source": "schema"}}],
            }
        },
    }
    row = json.dumps(body).replace('"value": "leaf"', '"value": 0.123456789012345678') + "\n"
    source = tmp_path / "deep.jsonl"
    source.write_text(row)
    observed = []

    def consumer(job, module, args):
        observed.append(json.loads(Path(args[1]).read_text(), parse_float=Decimal))
        return {"items": 1}

    monkeypatch.setattr(batch, "run_tool", consumer)
    batch.run_batch(
        SimpleNamespace(progress=lambda *args: None, check=lambda: None),
        SimpleNamespace(ready=lambda _: SimpleNamespace(base_url="http://127.0.0.1:1")),
        "m",
        source,
        tmp_path / "out",
    )
    sent = observed[0]
    leaf = sent["state"]
    for _ in range(600):
        leaf = leaf["child"]
    assert leaf == {"_mcp_source": "business", "value": Decimal("0.123456789012345678")}
    assert "_mcp_source" not in sent["questions"]["q"]
    assert sent["questions"]["q"]["tools"][0]["inputSchema"]["_mcp_source"] == "schema"
    assert source.read_text() == row


def test_deep_batch_reaches_fake_child_via_native_cli(client, child, tmp_path):
    state = {"_mcp_source": "business"}
    for _ in range(600):
        state = {"child": state}
    body = {
        "state": state,
        "questions": {
            "q": {
                "type": "tool",
                "_mcp_source": {"url": "local"},
                "tools": [{"name": "weather", "inputSchema": {"_mcp_source": "schema"}}],
            }
        },
    }
    original = json.dumps(body) + "\n"
    source = tmp_path / "deep-native.jsonl"
    source.write_text(original)
    assert client.post("/api/batch", json={"id": "mini", "input": str(source)}).status_code == 202
    job = wait_batch(client)
    assert job["phase"] == "done", job
    assert len(child.seen) == 1
    sent = child.seen[0][1]
    assert "_mcp_source" not in sent["questions"]["q"]
    assert sent["questions"]["q"]["tools"][0]["inputSchema"]["_mcp_source"] == "schema"
    leaf = sent["state"]
    for _ in range(600):
        leaf = leaf["child"]
    assert leaf["_mcp_source"] == "business" and source.read_text() == original
