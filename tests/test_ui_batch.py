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


def test_path_check_describes_a_jsonl_input_by_its_first_row(client, tmp_path):
    """The live hint under the Batch and Calibration path fields: lines, format, gold and a short preview."""
    source = tmp_path / "items.jsonl"
    rows = [{"id": n, "state": "s" * 500, "question": "q?", "options": ["a", "b"], "gold": 0} for n in range(3)]
    source.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n")
    d = client.post("/api/paths/check", json={"path": str(source), "kind": "jsonl"}).json()
    assert (d["ok"], d["code"], d["exists"], d["is_file"], d["lines"]) == (True, None, True, True, 3)
    first = d["first"]
    assert first["format"] == "simple" and first["labelled"] and first["fields"] == list(rows[0])
    assert len(first["preview"]) <= 301 and first["preview"].endswith("…")  # never more than a short preview
    request = tmp_path / "requests.jsonl"
    request.write_text(json.dumps({"state": "s", "questions": {"a": {"type": "noul", "gold": "yes"}, "b": {}}}) + "\n")
    first = client.post("/api/paths/check", json={"path": str(request), "kind": "jsonl"}).json()["first"]
    assert (first["format"], first["questions"], first["labelled"], first["types"]) == (
        "request",
        2,
        True,
        ["?", "noul"],
    )


def test_path_check_names_the_problem_and_never_shows_a_non_json_file(client, tmp_path):
    def code(path, kind="jsonl"):
        return client.post("/api/paths/check", json={"path": str(path), "kind": kind}).json()

    assert code(tmp_path / "missing.jsonl")["code"] == "missing"
    assert code(tmp_path)["code"] == "is_dir"
    secret = tmp_path / "notes.txt"
    secret.write_text("password = hunter2\n")
    d = code(secret)
    assert d["code"] == "not_json" and "first" not in d and "hunter2" not in json.dumps(d)
    (tmp_path / "empty.jsonl").write_text("\n")
    assert code(tmp_path / "empty.jsonl")["code"] == "empty"
    (tmp_path / "other.jsonl").write_text('{"auths": {"r": {"auth": "c2VjcmV0"}}}\n')
    d = code(tmp_path / "other.jsonl")
    assert d["code"] == "unknown_format" and "first" not in d and "c2VjcmV0" not in json.dumps(d)
    (tmp_path / "row.jsonl").write_text(
        json.dumps({"state": "s", "question": "q?", "options": [], "token": "t0k"}) + "\n"
    )
    first = code(tmp_path / "row.jsonl")["first"]
    assert "t0k" not in first["preview"] and "token" in first["fields"]  # the preview keeps a request's own fields
    (tmp_path / "cal.json").write_text(json.dumps({"temperature_per_prim": {}, "certified": {}}))
    d = code(tmp_path / "cal.json", "json")
    assert d["ok"] and d["fields"] == ["temperature_per_prim", "certified"]
    assert code(secret, "json")["code"] == "not_json"
    assert client.post("/api/paths/check", json={"path": "", "kind": "jsonl"}).status_code == 400
    assert client.post("/api/paths/check", json={"path": "x", "kind": "exe"}).status_code == 400


def test_path_check_of_an_output_says_whether_it_can_be_written(client, tmp_path):
    def check(path):
        return client.post("/api/paths/check", json={"path": str(path), "kind": "output"}).json()

    d = check(tmp_path / "out.jsonl")
    assert d["ok"] and d["writable"] and not d["exists"]
    assert check(tmp_path / "no" / "out.jsonl")["code"] == "dir_missing"
    assert check(tmp_path)["code"] == "is_dir"
    existing = tmp_path / "log.jsonl"
    existing.write_text("{}\n")
    d = client.post("/api/paths/check", json={"path": str(existing), "kind": "append"}).json()
    assert d["ok"] and d["exists"] and d["kind"] == "append"
    d = check("~/basal-ui-path-check.jsonl")
    assert not d["path"].startswith("~")  # expanded to the server's home


def test_path_check_reports_caps_text_and_paths_it_cannot_reach(client, tmp_path, monkeypatch):
    from basal_ui import paths

    def check(path, kind="jsonl"):
        response = client.post("/api/paths/check", json={"path": str(path), "kind": kind})
        assert response.status_code == 200
        return response.json()

    monkeypatch.setattr(paths, "LINE_CAP", 2)
    monkeypatch.setattr(paths, "JSON_CAP", 10)
    many = tmp_path / "many.jsonl"
    many.write_text("\n".join(json.dumps({"state": "s", "options": ["a"]}) for _ in range(5)))
    d = check(many)
    assert d["ok"] and d["lines"] == 2 and d["lines_capped"]
    (tmp_path / "big.json").write_text(json.dumps({"certified": "x" * 20}))
    assert check(tmp_path / "big.json", "json")["code"] == "too_large"
    (tmp_path / "bin.jsonl").write_bytes(b"\xff\xfe\x00{")
    assert check(tmp_path / "bin.jsonl")["code"] == "not_text"
    long = tmp_path / ("a" * 300)
    assert check(long)["code"] == "invalid" and not check(long)["ok"]  # ENAMETOOLONG, not an HTTP 500
    assert check(long / "x.jsonl", "output")["code"] == "invalid"

    def denied(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(paths.Path, "exists", denied)  # a directory on the way basal-ui cannot search
    assert check(tmp_path / "x.jsonl")["code"] == "unreadable"
    assert check(tmp_path / "x.jsonl", "append")["code"] == "not_writable"


def test_path_check_of_a_directory_says_whether_it_exists_or_will_be_created(client, tmp_path):
    def check(path):
        return client.post("/api/paths/check", json={"path": str(path), "kind": "dir"}).json()

    d = check(tmp_path)
    assert d["ok"] and d["exists"] and d["writable"]
    d = check(tmp_path / "new" / "models")
    assert d["ok"] and not d["exists"]  # settings.set_models_dir creates it
    (tmp_path / "file").write_text("x")
    assert check(tmp_path / "file")["code"] == "not_dir"
    assert check(tmp_path / "file" / "below")["code"] == "not_writable"
