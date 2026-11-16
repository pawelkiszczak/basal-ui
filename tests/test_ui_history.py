import pytest

from basal_ui.history import History, make_record, replay_fields


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))


def record(model="m", source="api", state="state", answers=None):
    response = {
        "answers": answers or {"q": {"type": "choice", "choice": "yes", "confidence": 0.9}},
        "usage": {"input_tokens": 7},
    }
    return make_record(model, source, {"model": model, "state": state, "questions": {"q": {}}}, 200, response, 3.0)


def test_list_filters_search_and_pages_newest_first(tmp_path):
    history = History(tmp_path / "h.jsonl")
    records = [
        record("a", "api", "Umowa najmu"),
        record("b", "playground", "invoice overdue"),
        record("a", "mcp", "Faktura VAT"),
        record("b", "api", "umowa o pracę"),
    ]
    for r in records:
        history.append(r)
    ids = [r["id"] for r in records]
    assert [r["id"] for r in history.list()] == ids[::-1]
    assert "request" not in history.list()[0] and history.get(ids[0])["request"]["state"] == "Umowa najmu"
    assert [r["id"] for r in history.list(model="a")] == [ids[2], ids[0]]
    assert [r["id"] for r in history.list(source="api")] == [ids[3], ids[0]]
    assert [r["id"] for r in history.list(q="UMOWA")] == [ids[3], ids[0]]
    assert [r["id"] for r in history.list(limit=2)] == [ids[3], ids[2]]
    assert [r["id"] for r in history.list(before=ids[2])] == [ids[1], ids[0]]
    assert [r["id"] for r in history.list(after=ids[1])] == [ids[3], ids[2]]
    assert [r["id"] for r in history.list(after=ids[1], order="asc")] == [ids[2], ids[3]]
    assert history.models() == ["a", "b"]
    first = history.list()[-1]
    assert (first["questions"], first["input_tokens"], first["answers"]) == (
        1,
        7,
        {"q": {"type": "choice", "best": "yes", "confidence": 0.9}},
    )


def test_oldest_first_poll_pages_drain_a_burst_with_equal_timestamps(tmp_path):
    history = History(tmp_path / "h.jsonl", keep=200)
    records = [record(state=f"s{i}") for i in range(150)]
    for item in records:
        item["ts"] = 1
        history.append(item)
    cursor, received = records[0]["id"], []
    while True:
        page = history.list(after=cursor, limit=100, order="asc")
        received.extend(page)
        if len(page) < 100:
            break
        cursor = page[-1]["id"]
    assert [item["id"] for item in received] == [item["id"] for item in records[1:]]


def test_a_failed_write_leaves_memory_unchanged(tmp_path):
    path = tmp_path / "h.jsonl"
    history = History(path)
    history.append(record(state="kept"))
    before = history.list()
    path.unlink()
    path.mkdir()  # the file cannot be opened for writing any more
    with pytest.raises(OSError):
        history.append(record(state="lost"))
    assert history.list() == before


def test_clear_write_failure_preserves_memory_and_disk(tmp_path, monkeypatch):
    from basal_ui import settings

    path = tmp_path / "h.jsonl"
    history = History(path)
    history.append(record(state="kept"))
    before = path.read_text()
    memory = history.list()
    monkeypatch.setattr(settings, "_write_private", lambda *_args: (_ for _ in ()).throw(OSError("read-only")))

    with pytest.raises(OSError, match="read-only"):
        history.clear()

    assert history.list() == memory
    assert history.get(memory[0]["id"]) is not None
    assert path.read_text() == before
    assert History(path).list() == memory


def test_media_redaction_preserves_structured_data_and_redacts_media_contexts():
    ordinary = "data: ordinary text " + "x" * 80
    native = {
        "state": {"data": ordinary, "image_url": ordinary},
        "questions": {"q": {"criteria": {"data": ordinary, "image_url": ordinary}}},
    }
    native_record = make_record("m", "playground", native, 200, {}, 1.0)
    assert native_record["request"] == native
    assert replay_fields(native_record["request"]) == {"replay_request": native, "replay_error": None}

    media_data = "A" * 80
    data_url = "data:audio/wav;base64," + media_data
    actual = {
        "state": {"data": "state text"},
        "media": [{"type": "image", "data": media_data}],
        "images": [data_url],
        "audio": [data_url],
    }
    redacted = make_record("m", "playground", actual, 200, {}, 1.0)["request"]
    assert redacted["state"] == actual["state"]
    assert redacted["media"][0]["data"] == f"<{len(media_data)} bytes>"
    assert redacted["images"] == [f"<{len(data_url)} bytes>"]
    assert redacted["audio"] == [f"<{len(data_url)} bytes>"]
    assert "original media attachments" in replay_fields(redacted)["replay_error"]

    decisions = {
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_image", "image_url": "data:image/png;base64," + media_data},
                    {
                        "type": "input_audio",
                        "audio_url": data_url,
                        "data": media_data,
                        "content": media_data,
                    },
                    {"type": "input_text", "text": ordinary},
                ],
            }
        ]
    }
    decision_record = make_record("m", "api", decisions, 200, {}, 1.0)["request"]
    parts = decision_record["input"][0]["content"]
    assert parts[0]["image_url"] == f"<{len('data:image/png;base64,' + media_data)} bytes>"
    assert parts[1]["audio_url"] == f"<{len(data_url)} bytes>"
    assert parts[1]["data"] == f"<{len(media_data)} bytes>"
    assert parts[1]["content"] == f"<{len(media_data)} bytes>"
    assert parts[2]["text"] == ordinary
    assert "original media attachments" in replay_fields(decision_record)["replay_error"]


@pytest.mark.parametrize(
    ("part", "payload_key", "expected"),
    [
        (
            {"type": "input_audio", "input_audio": {"data": "QUJD", "format": "wav"}},
            "input_audio",
            {"data": "<4 bytes>", "format": "wav"},
        ),
        (
            {"type": "input_image", "image_url": {"url": "data:image/png;base64,QUJD", "detail": "high"}},
            "image_url",
            {"url": "<26 bytes>", "detail": "high"},
        ),
    ],
)
def test_nested_decisions_media_payloads_are_redacted_and_cannot_be_replayed(part, payload_key, expected):
    body = {
        "input": [{"role": "user", "content": [part]}],
        "questions": [{"type": "predicate", "instructions": "Is this private?"}],
    }
    recorded = make_record("m", "api", body, 200, {}, 1)["request"]
    assert recorded["input"][0]["content"][0][payload_key] == expected
    replay = replay_fields(recorded)
    assert replay["replay_request"] is None
    assert "original media attachments" in replay["replay_error"]


def test_media_bytes_are_replaced_by_their_size_and_states_are_previewed():
    body = {
        "state": {"text": "x" * 300},
        "images": [{"data": "QUJD", "mime": "image/png"}],
        "questions": {"q": {"type": "noul"}},
    }
    r = make_record("m", "playground", body, 422, {"error": "bad"}, 1.0)
    assert r["request"]["images"] == [{"data": "<4 bytes>", "mime": "image/png"}]
    url = "data:image/png;base64," + "A" * 100
    openai = make_record("m", "api", {"input": [{"type": "input_image", "image_url": url}]}, 200, {}, 1.0)
    assert openai["request"]["input"][0]["image_url"] == f"<{len(url)} bytes>"
    assert body["images"][0]["data"] == "QUJD"  # the caller's body is untouched
    assert len(r["state_preview"]) == 160 and r["state_preview"].startswith('{"text": "xxx')
    assert (r["status"], r["answers"], r["response"]) == (422, {}, {"error": "bad"})


def test_decisions_api_history_has_a_playground_replay_without_changing_recorded_request():
    request = {
        "model": "m",
        "input": "A state",
        "questions": [
            {"type": "choice", "name": "q", "instructions": "Choose", "choices": [{"value": "yes"}, {"value": "no"}]}
        ],
        "store": True,
    }
    record_ = make_record("m", "api", request, 200, {}, 1.0)
    fields = replay_fields(record_["request"])
    assert record_["request"] == request
    assert fields == {
        "replay_request": {
            "state": "A state",
            "questions": {"q": {"type": "choice", "instructions": "Choose", "criteria": {"yes": None, "no": None}}},
            "model": "m",
        },
        "replay_error": None,
    }


def test_redacted_media_blocks_history_replay_with_explanation():
    request = {
        "model": "m",
        "input": [
            {"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64," + "A" * 100}]}
        ],
        "questions": [{"type": "predicate", "instructions": "Is this a cat?"}],
    }
    record_ = make_record("m", "api", request, 200, {}, 1.0)
    fields = replay_fields(record_["request"])
    assert fields["replay_request"] is None
    assert "original media attachments" in fields["replay_error"]
    text = replay_fields({"state": "The case says <120 bytes> but has no attachment.", "questions": {}})
    assert text["replay_request"]["state"].endswith("<120 bytes> but has no attachment.")
    assert text["replay_error"] is None


def test_records_flag_deferred_answers_and_truncated_states():
    r = make_record(
        "m",
        "api",
        {"state": "s", "questions": {"q": {"type": "act"}}},
        200,
        {"answers": {"q": {"type": "act", "deferred": True}}, "usage": {"truncated": {"dropped_tokens": 3}}},
        1.0,
    )
    assert r["deferred"] is True and r["truncated"] is True
    plain = record("a", "api", "s")
    assert plain["deferred"] is False and plain["truncated"] is False


def test_persistence_rotation_and_clear(tmp_path):
    path = tmp_path / "h.jsonl"
    history = History(path, keep=3)
    for i in range(510):
        history.append(record(state=f"s{i}"))
    assert len(path.read_text().splitlines()) == 9  # the 504th append rewrote the file with the last 3, then 6 more
    assert [r["state_preview"] for r in History(path, keep=3).list()] == ["s509", "s508", "s507"]
    assert path.stat().st_mode & 0o777 == 0o600
    with path.open("a") as f:
        f.write("{broken\n")
    assert len(History(path, keep=3).list()) == 3
    assert history.clear() == 3
    assert history.list() == [] and History(path).list() == []


def test_audio_policy_replay_reload_eviction_and_clear(tmp_path, monkeypatch):
    from basal_ui import history_audio, settings

    monkeypatch.setattr(history_audio, "BUDGET", 45)
    path = tmp_path / "h.jsonl"
    first, second = History(path), History(path)
    settings.set_preset("request", "saved", {"audio": ["saved audio"]})
    body = {"state": {"data": "business", "audio": "ordinary"}, "audio": ["data:audio/wav;base64,QUJD"]}

    def append(history, source):
        row = make_record("m", source, body, 200, {}, 1)
        history.append(row, body=body, keep_audio=settings.keep_history_audio)
        return row["id"]

    old = append(first, "api")
    assert first.detail(old, True)["replay_request"] is None
    settings.set_keep_history_audio(True)
    retained = append(first, "api")
    detail = second.detail(retained)
    assert detail["replay_requires_audio"] and "QUJD" not in str(detail) and "_audio" not in detail
    settings.set_keep_history_audio(False)
    assert History(path).detail(retained, True)["replay_request"] == body
    assert append(second, "api") != retained
    assert first.detail(retained, True)["replay_request"] == body
    settings.set_keep_history_audio(True)
    newest = append(second, "api")
    assert first.detail(retained, True)["replay_error_code"] == "history_audio_unavailable"
    assert first.detail(newest, True)["replay_request"] == body
    assert len(path.read_text().splitlines()) == 4
    assert "QUJD" not in path.read_text()
    directory = path.with_name(path.name + ".audio")
    assert directory.stat().st_mode & 0o777 == 0o700
    assert all(f.stat().st_mode & 0o777 == 0o600 for f in directory.iterdir())
    second.clear()
    assert list(directory.iterdir()) == []
    assert settings.presets()["request"]["saved"] == {"audio": ["saved audio"]}
    assert first.detail(newest, True) is None


def test_audio_mixed_media_and_corrupt_or_hostile_references(tmp_path):
    import json

    path = tmp_path / "h.jsonl"
    history = History(path)
    body = {"state": {"data": "business"}, "media": [{"type": "audio", "data": "QUJD"}]}
    row = make_record("m", "api", body, 200, {}, 1)
    history.append(row, body=body, keep_audio=True)
    directory = path.with_name(path.name + ".audio")
    blob = directory / row["_audio"]["name"]
    blob.write_text("broken")
    assert history.detail(row["id"], True)["replay_error_code"] == "history_audio_unavailable"
    outside = tmp_path / "outside"
    outside.write_text("secret")
    blob.unlink()
    blob.symlink_to(outside)
    assert history.detail(row["id"], True)["replay_error_code"] == "history_audio_unavailable"
    disk = json.loads(path.read_text())
    disk["_audio"]["name"] = "../outside"
    path.write_text(json.dumps(disk) + "\n")
    assert "secret" not in str(history.detail(row["id"], True))
    history.clear()
    assert outside.read_text() == "secret"
    mixed = {**body, "images": ["data:image/png;base64,QUJD"]}
    row = make_record("m", "api", mixed, 200, {}, 1)
    history.append(row, body=mixed, keep_audio=True)
    assert history.detail(row["id"], True)["replay_request"] is None


def test_audio_write_failure_keeps_rows_and_does_not_claim_new_clip(tmp_path, monkeypatch):
    from basal_ui import settings

    path = tmp_path / "h.jsonl"
    history = History(path)
    body = {"audio": ["QUJD"]}
    row = make_record("m", "api", body, 200, {}, 1)
    history.append(row, body=body, keep_audio=True)
    before = path.read_text()
    new = make_record("m", "api", body, 200, {}, 1)
    monkeypatch.setattr(history, "_append_record", lambda *_: (_ for _ in ()).throw(OSError("failed")))
    with pytest.raises(OSError):
        history.append(new, body=body, keep_audio=True)
    assert path.read_text() == before and "_audio" not in new
    assert len(list(path.with_name(path.name + ".audio").iterdir())) == 1
    monkeypatch.setattr(settings, "_write_private", lambda *_: (_ for _ in ()).throw(OSError("failed")))
    with pytest.raises(OSError):
        history.clear()
    assert history.detail(row["id"], True)["replay_request"] == body


def test_decisions_audio_round_trips_without_changing_business_data(tmp_path):
    body = {
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_audio", "input_audio": {"data": "QUJD", "format": "wav"}},
                    {"type": "input_text", "text": "ordinary"},
                ],
            }
        ],
        "questions": [{"type": "predicate", "instructions": "yes?"}],
    }
    history = History(tmp_path / "history.jsonl")
    row = make_record("m", "api", body, 200, {}, 1)
    history.append(row, body=body, keep_audio=True)
    detail = history.detail(row["id"], True)
    assert detail["replay_request"] == replay_fields(body)["replay_request"]
    assert "QUJD" not in str(detail["request"])
    assert "QUJD" not in row["state_preview"]


def test_audio_eviction_remains_truthful_after_metadata_failure(tmp_path, monkeypatch):
    from basal_ui import history_audio

    monkeypatch.setattr(history_audio, "BUDGET", 14)
    history = History(tmp_path / "history.jsonl")
    body = {"audio": ["QUJDQUJD"]}
    old = make_record("m", "api", body, 200, {}, 1)
    history.append(old, body=body, keep_audio=True)
    new = make_record("m", "api", body, 200, {}, 1)
    monkeypatch.setattr(history, "_append_record", lambda *_: (_ for _ in ()).throw(OSError("failed")))
    with pytest.raises(OSError):
        history.append(new, body=body, keep_audio=True)
    assert history.detail(old["id"], True)["replay_error_code"] == "history_audio_unavailable"
    assert history.detail(new["id"], True) is None


def test_clear_cleanup_failure_can_be_retried_without_losing_ownership(tmp_path, monkeypatch):
    from basal_ui import history_audio

    path = tmp_path / "history.jsonl"
    history = History(path)
    body = {"audio": ["QUJD"]}
    row = make_record("m", "api", body, 200, {}, 1)
    history.append(row, body=body, keep_audio=True)
    directory = path.with_name(path.name + ".audio")
    real_remove = history_audio.remove
    monkeypatch.setattr(history_audio, "remove", lambda *_: (_ for _ in ()).throw(OSError("unlink failed")))
    with pytest.raises(OSError):
        history.clear()
    assert History(path).detail(row["id"], True) is None
    assert len(list(directory.iterdir())) == 1
    monkeypatch.setattr(history_audio, "remove", real_remove)
    History(path).clear()
    assert list(directory.iterdir()) == []


def test_two_processes_share_audio_budget_and_records(tmp_path):
    import os
    import subprocess
    import sys

    path = tmp_path / "history.jsonl"
    script = """
import sys
from pathlib import Path
from basal_ui import history_audio
from basal_ui.history import History, make_record
history_audio.BUDGET = 14
body = {'audio': ['QUJDQUJD']}
history = History(Path(sys.argv[1]))
print('ready', flush=True)
sys.stdin.readline()
row = make_record('m', 'api', body, 200, {}, 1)
history.append(row, body=body, keep_audio=True)
"""
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=os.environ.copy(),
        )
        for _ in range(2)
    ]
    try:
        for worker in workers:
            assert worker.stdout.readline().strip() == "ready"
        for worker in workers:
            worker.stdin.write("go\n")
            worker.stdin.flush()
        for worker in workers:
            _, error = worker.communicate(timeout=15)
            assert worker.returncode == 0, error
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.communicate()
    history = History(path)
    rows = history.list()
    assert len(rows) == 2
    replays = [history.detail(row["id"], True) for row in rows]
    assert sum(item["replay_request"] is not None for item in replays) == 1
    assert sum(item.get("replay_error_code") == "history_audio_unavailable" for item in replays) == 1
    assert sum(p.stat().st_size for p in path.with_name(path.name + ".audio").iterdir()) <= 14


def test_existing_unowned_filename_is_not_deleted_on_collision(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from basal_ui import history_audio

    history = History(tmp_path / "history.jsonl")
    body = {"audio": ["QUJD"]}
    row = make_record("m", "api", body, 200, {}, 1)
    directory = history.path.with_name(history.path.name + ".audio")
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(history_audio.uuid, "uuid4", lambda: SimpleNamespace(hex="f" * 32))
    unrelated = directory / (row["id"] + "_" + "f" * 32)
    unrelated.write_text("do not delete")
    with pytest.raises(FileExistsError):
        history.append(row, body=body, keep_audio=True)
    assert unrelated.read_text() == "do not delete"
    assert history.list() == []
