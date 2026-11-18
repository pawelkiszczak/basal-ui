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
    assert r["deferred"] is True and r["truncated"] == {"dropped_tokens": 3}
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
    with path.open("ab") as stream:
        stream.write(b"{broken")
    reloaded = History(path, keep=3)
    assert len(reloaded.list()) == 3
    newest = record(state="after malformed row")
    reloaded.append(newest)
    assert path.read_bytes().splitlines()[-2] == b"{broken"
    assert History(path, keep=3).get(newest["id"])["state_preview"] == "after malformed row"
    assert reloaded.clear() == 3
    assert reloaded.list() == [] and History(path).list() == []


def test_ordinary_append_does_not_reread_unchanged_history(tmp_path, monkeypatch):
    path = tmp_path / "history.jsonl"
    first, second = History(path), History(path)
    first.append(record(state="initial"))
    other = record(state="external append")
    second.append(other)
    assert first.get(other["id"]) is not None

    def unexpected_read(*args, **kwargs):
        pytest.fail("an ordinary append should not reread unchanged history")

    monkeypatch.setattr(first, "_read_file", unexpected_read)
    new = record(state="local append")
    first.append(new)
    assert first.get(new["id"]) is not None
    assert History(path).get(new["id"]) is not None


def test_audio_policy_replay_reload_eviction_and_clear(tmp_path, monkeypatch):
    from basal_ui import history_audio, settings

    monkeypatch.setattr(history_audio, "BUDGET", 45)
    path = tmp_path / "h.jsonl"
    first, second = History(path), History(path)
    settings.set_preset("request", "saved", {"audio": ["saved audio"]})
    body = {"state": {"data": "business", "audio": "ordinary"}, "audio": ["data:audio/wav;base64,QUJD"]}
    replay = {"state": body["state"], "media": [{"type": "audio", "data": "QUJD"}]}

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
    assert History(path).detail(retained, True)["replay_request"] == replay
    assert append(second, "api") != retained
    assert first.detail(retained, True)["replay_request"] == replay
    settings.set_keep_history_audio(True)
    newest = append(second, "api")
    assert first.detail(retained, True)["replay_error_code"] == "history_audio_unavailable"
    assert first.detail(newest, True)["replay_request"] == replay
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
    with pytest.raises(OSError):
        history.clear()
    assert outside.read_text() == "secret"
    path.write_text("")
    mixed = {**body, "images": ["data:image/png;base64,QUJD"]}
    row = make_record("m", "api", mixed, 200, {}, 1)
    history.append(row, body=mixed, keep_audio=True)
    assert history.detail(row["id"], True)["replay_request"] is None


def test_audio_without_eviction_appends_in_place(tmp_path):
    path = tmp_path / "h.jsonl"
    history = History(path)
    body = {"media": [{"type": "audio", "data": "QUJD"}]}
    old = make_record("m", "api", body, 200, {}, 1)
    history.append(old, body=body, keep_audio=True)
    snapshot, inode = path.read_bytes(), path.stat().st_ino
    new = make_record("m", "api", body, 200, {}, 1)
    history.append(new, body=body, keep_audio=True)
    assert path.read_bytes().startswith(snapshot) and path.stat().st_ino == inode
    assert history.detail(old["id"], True)["replay_request"] == body
    assert history.detail(new["id"], True)["replay_request"] == body


def test_short_audio_append_preserves_history(tmp_path, monkeypatch):
    import contextlib
    import os
    from unittest.mock import Mock

    path = tmp_path / "h.jsonl"
    history = History(path)
    body = {"media": [{"type": "audio", "data": "QUJD"}]}
    old = make_record("m", "api", body, 200, {}, 1)
    history.append(old, body=body, keep_audio=True)
    snapshot = path.read_bytes()
    new = make_record("m", "api", body, 200, {}, 1)
    original = os.fdopen

    @contextlib.contextmanager
    def short_write(*args, **kwargs):
        with original(*args, **kwargs) as stream:
            broken = Mock(wraps=stream)
            broken.write.side_effect = lambda data: stream.write(data[:20])
            yield broken

    def fdopen(fd, mode="r", *args, **kwargs):
        opener = short_write if mode == "a+b" else original
        return opener(fd, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fdopen", fdopen)
        with pytest.raises(OSError, match="History append was incomplete"):
            history.append(new, body=body, keep_audio=True)
    assert path.read_bytes() == snapshot
    assert history.detail(old["id"], True)["replay_request"] == body
    assert "_audio" not in new
    history.append(new, body=body, keep_audio=True)
    assert history.detail(new["id"], True)["replay_request"] == body


def test_audio_write_failure_keeps_rows_and_does_not_claim_new_clip(tmp_path, monkeypatch):
    from basal_ui import settings

    path = tmp_path / "h.jsonl"
    history = History(path)
    body = {"media": [{"type": "audio", "data": "QUJD"}]}
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


def test_unsupported_decisions_audio_is_redacted_and_refused(tmp_path):
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
    from basal_ui.compat import child_request

    native_body, refusal = child_request("/v1/decisions", body)
    assert native_body is None and refusal.status_code >= 400
    detail = history.detail(row["id"], True)
    assert detail["replay_request"] is None and detail["replay_error"]
    assert "QUJD" not in str(detail["request"])
    assert "QUJD" not in row["state_preview"]


def test_audio_eviction_remains_truthful_after_metadata_failure(tmp_path, monkeypatch):
    from basal_ui import history_audio

    monkeypatch.setattr(history_audio, "BUDGET", 40)
    history = History(tmp_path / "history.jsonl")
    body = {"audio": ["data:audio/wav;base64,QUJDQUJD"]}
    old = make_record("m", "api", body, 200, {}, 1)
    history.append(old, body=body, keep_audio=True)
    new = make_record("m", "api", body, 200, {}, 1)
    monkeypatch.setattr(history, "_append_record", lambda *_: (_ for _ in ()).throw(OSError("failed")))
    with pytest.raises(OSError):
        history.append(new, body=body, keep_audio=True)
    assert history.detail(old["id"], True)["replay_request"]["media"][0]["data"] == "QUJDQUJD"
    assert History(history.path).detail(old["id"], True)["replay_request"] is not None
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
body = {'media': [{'type': 'audio', 'data': 'QUJDQUJD'}]}
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


@pytest.mark.parametrize(
    "damage", [b"{broken\n", b"[]\n", b"{}\n", b"\xff\n", b"duplicate", b'{"id":"other","_audio":{}}\n']
)
def test_corrupt_metadata_never_authorizes_cleanup_or_mutation(tmp_path, damage):
    from basal_ui import history_audio

    path = tmp_path / "history.jsonl"
    history = History(path, keep=1)
    body = {"media": [{"type": "audio", "data": "QUJD"}]}
    row = make_record("m", "api", body, 200, {}, 1)
    history.append(row, body=body, keep_audio=True)
    history_audio.journal_add(path, [row])
    import json

    padding = b"".join((json.dumps({"id": f"legacy-{i}"}) + "\n").encode() for i in range(502))
    first_row = path.read_bytes()
    path.write_bytes(first_row + padding + (first_row if damage == b"duplicate" else damage))
    journal = path.with_name(path.name + ".audio-pending.json")
    blob = path.with_name(path.name + ".audio") / row["_audio"]["name"]
    before = (path.read_bytes(), journal.read_bytes(), blob.read_bytes())
    reloaded = History(path, keep=1)
    with pytest.raises(OSError):
        reloaded.append(make_record("m", "api", body, 200, {}, 1), body=body, keep_audio=True)
    with pytest.raises(OSError):
        reloaded.clear()
    assert (path.read_bytes(), journal.read_bytes(), blob.read_bytes()) == before


@pytest.mark.parametrize("kind", ["regular", "symlink", "hardlink"])
def test_replaced_blob_is_never_deleted_during_clear_or_restart_cleanup(tmp_path, monkeypatch, kind):
    import os

    from basal_ui import history_audio

    path = tmp_path / "history.jsonl"
    history = History(path)
    body = {"media": [{"type": "audio", "data": "QUJD"}]}
    row = make_record("m", "api", body, 200, {}, 1)
    history.append(row, body=body, keep_audio=True)
    blob = path.with_name(path.name + ".audio") / row["_audio"]["name"]
    original_remove = history_audio.remove
    monkeypatch.setattr(history_audio, "remove", lambda *_: (_ for _ in ()).throw(OSError("unlink failed")))
    with pytest.raises(OSError):
        history.clear()
    monkeypatch.setattr(history_audio, "remove", original_remove)
    blob.replace(tmp_path / "original-owned")
    foreign = tmp_path / "foreign"
    foreign.write_text("foreign data")
    if kind == "regular":
        replacement = tmp_path / "replacement"
        replacement.write_text("foreign data")
        replacement.replace(blob)
    elif kind == "symlink":
        blob.symlink_to(foreign)
    else:
        os.link(foreign, blob)
    History(path).clear()
    assert blob.is_symlink() if kind == "symlink" else blob.read_text() == "foreign data"
    assert foreign.read_text() == "foreign data"


def test_postcommit_payload_failure_is_unavailable_and_budget_stays_bounded(tmp_path, monkeypatch):
    from basal_ui import history_audio

    monkeypatch.setattr(history_audio, "BUDGET", 14)
    history = History(tmp_path / "history.jsonl")
    body = {"media": [{"type": "audio", "data": "QUJDQUJD"}]}
    old = make_record("m", "api", body, 200, {}, 1)
    history.append(old, body=body, keep_audio=True)
    new = make_record("m", "api", body, 200, {}, 1)

    def partial_write(_path, _record, reservation):
        import os

        os.write(reservation[1], reservation[3][:3])
        raise OSError("write failed")

    monkeypatch.setattr(history_audio, "fill", partial_write)
    with pytest.raises(OSError):
        history.append(new, body=body, keep_audio=True)
    reloaded = History(history.path)
    assert reloaded.detail(old["id"], True)["replay_error_code"] == "history_audio_unavailable"
    assert reloaded.detail(new["id"], True)["replay_error_code"] == "history_audio_unavailable"
    assert sum(p.stat().st_size for p in history.path.with_name(history.path.name + ".audio").iterdir()) <= 14


def test_replacement_after_journal_survives_precommit_failure_and_recovery(tmp_path, monkeypatch):
    from basal_ui import history_audio

    path = tmp_path / "history.jsonl"
    history = History(path)
    body = {"media": [{"type": "audio", "data": "QUJD"}]}
    row = make_record("m", "api", body, 200, {}, 1)
    original_journal = history_audio.journal_add
    replacement = []

    def replace_after_journal(path, records):
        original_journal(path, records)
        if records and records[0]["id"] == row["id"]:
            blob = path.with_name(path.name + ".audio") / records[0]["_audio"]["name"]
            blob.replace(tmp_path / "original-owned")
            blob.write_text("foreign")
            replacement.append(blob)

    monkeypatch.setattr(history_audio, "journal_add", replace_after_journal)
    monkeypatch.setattr(history, "_append_record", lambda *_: (_ for _ in ()).throw(OSError("commit failed")))
    with pytest.raises(OSError):
        history.append(row, body=body, keep_audio=True)
    assert replacement[0].read_text() == "foreign"
    monkeypatch.setattr(history_audio, "journal_add", original_journal)
    History(path).clear()
    assert replacement[0].read_text() == "foreign"


def test_full_budget_postcommit_unlink_failure_recovers_after_restart(tmp_path, monkeypatch):
    from basal_ui import history_audio

    monkeypatch.setattr(history_audio, "BUDGET", 14)
    history = History(tmp_path / "history.jsonl")
    body = {"media": [{"type": "audio", "data": "QUJDQUJD"}]}
    old = make_record("m", "api", body, 200, {}, 1)
    history.append(old, body=body, keep_audio=True)
    new = make_record("m", "api", body, 200, {}, 1)
    real_remove = history_audio.remove
    monkeypatch.setattr(history_audio, "remove", lambda *_: (_ for _ in ()).throw(OSError("unlink failed")))
    with pytest.raises(OSError):
        history.append(new, body=body, keep_audio=True)
    monkeypatch.setattr(history_audio, "remove", real_remove)
    reloaded = History(history.path)
    assert reloaded.detail(old["id"], True)["replay_error_code"] == "history_audio_unavailable"
    assert reloaded.detail(new["id"], True)["replay_error_code"] == "history_audio_unavailable"
    assert sum(p.stat().st_size for p in history.path.with_name(history.path.name + ".audio").iterdir()) <= 14
    reloaded.clear()
    assert list(history.path.with_name(history.path.name + ".audio").iterdir()) == []


def test_in_place_blob_modification_does_not_lose_ownership(tmp_path):
    history = History(tmp_path / "history.jsonl")
    body = {"media": [{"type": "audio", "data": "QUJD"}]}
    row = make_record("m", "api", body, 200, {}, 1)
    history.append(row, body=body, keep_audio=True)
    blob = history.path.with_name(history.path.name + ".audio") / row["_audio"]["name"]
    blob.write_text("damaged in place")
    assert history.detail(row["id"], True)["replay_error_code"] == "history_audio_unavailable"
    history.clear()
    assert not blob.exists()


def test_full_budget_journal_failure_preserves_existing_audio(tmp_path, monkeypatch):
    from basal_ui import history_audio, settings

    monkeypatch.setattr(history_audio, "BUDGET", 14)
    history = History(tmp_path / "history.jsonl")
    body = {"media": [{"type": "audio", "data": "QUJDQUJD"}]}
    old = make_record("m", "api", body, 200, {}, 1)
    history.append(old, body=body, keep_audio=True)
    before = history.path.read_bytes()
    original_write = settings._write_private

    def fail_journal(path, text):
        if str(path).endswith(".audio-pending.json"):
            raise OSError("journal failed")
        return original_write(path, text)

    monkeypatch.setattr(settings, "_write_private", fail_journal)
    new = make_record("m", "api", body, 200, {}, 1)
    with pytest.raises(OSError):
        history.append(new, body=body, keep_audio=True)
    assert history.path.read_bytes() == before
    assert History(history.path).detail(old["id"], True)["replay_request"] == body
    assert "_audio" not in new
    assert sum(p.stat().st_size for p in history.path.with_name(history.path.name + ".audio").iterdir()) <= 14


@pytest.mark.parametrize("damage", [b"{broken", b"{}", b"\xff"])
def test_corrupt_pending_journal_refuses_clear_and_append_without_deletion(tmp_path, damage):
    path = tmp_path / "history.jsonl"
    history = History(path)
    body = {"media": [{"type": "audio", "data": "QUJD"}]}
    row = make_record("m", "api", body, 200, {}, 1)
    history.append(row, body=body, keep_audio=True)
    journal = path.with_name(path.name + ".audio-pending.json")
    blob = path.with_name(path.name + ".audio") / row["_audio"]["name"]
    journal.write_bytes(damage)
    before = (path.read_bytes(), journal.read_bytes(), blob.read_bytes())
    reloaded = History(path)
    with pytest.raises(OSError):
        reloaded.clear()
    with pytest.raises(OSError):
        reloaded.append(make_record("m", "api", body, 200, {}, 1), body=body, keep_audio=True)
    assert (path.read_bytes(), journal.read_bytes(), blob.read_bytes()) == before


def test_full_budget_never_evicts_a_foreign_replacement(tmp_path, monkeypatch):
    from basal_ui import history_audio

    monkeypatch.setattr(history_audio, "BUDGET", 14)
    history = History(tmp_path / "history.jsonl")
    body = {"media": [{"type": "audio", "data": "QUJDQUJD"}]}
    old = make_record("m", "api", body, 200, {}, 1)
    history.append(old, body=body, keep_audio=True)
    blob = history.path.with_name(history.path.name + ".audio") / old["_audio"]["name"]
    blob.replace(tmp_path / "owned-original")
    blob.write_text("foreign data")
    before = history.path.read_bytes()
    with pytest.raises(OSError):
        history.append(make_record("m", "api", body, 200, {}, 1), body=body, keep_audio=True)
    assert history.path.read_bytes() == before
    assert blob.read_text() == "foreign data"
    assert history.detail(old["id"], True)["replay_error_code"] == "history_audio_unavailable"


def test_native_fields_reach_child_and_replay(tmp_path, monkeypatch, child):
    from fastapi.testclient import TestClient

    from tests.test_ui_bench import make_app

    app, _ = make_app(tmp_path, monkeypatch, base_url=child.url)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        request = {
            "state": "s",
            "questions": {
                "first": {
                    "type": "choice",
                    "instructions": "Choose",
                    "criteria": {"a": "A", "b": "B"},
                    "coverage": 0.9,
                },
                "second": {
                    "type": "noul",
                    "instructions": "Follow up",
                    "ask_if": {"first": ["b"]},
                    "depends_on": ["first"],
                },
            },
        }
        child.reply = {
            "answers": {"first": {"type": "choice", "choice": "a"}, "second": None},
            "usage": {"skipped": ["second"], "truncated": {"dropped_tokens": 3}},
        }
        response = client.post("/api/decide", json={"model": "mini", **request})
        assert response.status_code == 200
        assert child.seen[-1] == ("/v1/systemone", request)
        item = client.get("/api/history").json()["items"][0]
        assert item["truncated"] == {"dropped_tokens": 3}
        record = client.get(f"/api/history/{item['id']}").json()
        assert record["response"]["usage"]["skipped"] == ["second"]
        assert record["replay_request"] == {"model": "mini", **request}
        assert client.post("/api/decide", json={"model": "mini", **record["replay_request"]}).status_code == 200
        assert child.seen[-1] == ("/v1/systemone", request)
