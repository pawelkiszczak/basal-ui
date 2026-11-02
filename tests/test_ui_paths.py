import json

import pytest
from fastapi.testclient import TestClient

from basal_ui import paths, settings
from basal_ui.app import create_app
from basal_ui.fleet import Fleet
from basal_ui.hardware import Hardware
from basal_ui.history import History
from basal_ui.stats import Stats

HW = Hardware("cpu", "cpu", 1024**4, None, None, frozenset())


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    stats, history = Stats(tmp_path / "stats.json"), History(tmp_path / "history.jsonl")
    fleet = Fleet(
        models_dir_fn=lambda: tmp_path,
        platform_fn=lambda: "cpu",
        settings_mod=settings,
        hardware_fn=lambda: HW,
        stats=stats,
        history=history,
    )
    app = create_app(
        catalog_fn=lambda **_: ([], "test"), hardware_fn=lambda: HW, fleet=fleet, stats=stats, history=history
    )
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client


def test_check_jsonl_reports_real_unicode_path_shape_and_row_count(tmp_path):
    source = tmp_path / "запросы.jsonl"
    source.write_text(
        '{"state":"снег","question":"вопрос?","options":["да","нет"],"gold":0}\n\n'
        '{"state":"x","question":"y","options":["a","b"]}',
        encoding="utf-8",
    )

    result = paths.check(str(source), "jsonl")

    assert result["ok"] and result["path"] == str(source.resolve())
    assert result["lines"] == 2
    assert result["first"]["format"] == "simple"
    assert result["first"]["labelled"] is True
    assert "снег" in result["first"]["preview"]


def test_check_jsonl_reports_missing_malformed_and_empty_files(tmp_path):
    assert paths.check(str(tmp_path / "absent.jsonl"), "jsonl")["code"] == "missing"
    malformed = tmp_path / "bad.jsonl"
    malformed.write_text("not json\n", encoding="utf-8")
    assert paths.check(str(malformed), "jsonl")["code"] == "not_json"
    malformed.write_text("\n", encoding="utf-8")
    assert paths.check(str(malformed), "jsonl")["code"] == "empty"


@pytest.mark.parametrize(
    "row",
    [
        {"state": "s", "questions": {}},
        {"state": "s", "questions": {"q": "not an object"}},
        {"state": "s", "question": "q", "options": 1},
        {"state": "s", "question": "q", "options": ["only one"]},
        {"question": "q", "options": ["a", "b"]},
    ],
)
def test_check_jsonl_reports_invalid_first_row_schema(tmp_path, row):
    source = tmp_path / "schema.jsonl"
    source.write_text(json.dumps(row) + "\n", encoding="utf-8")

    result = paths.check(str(source), "jsonl")

    assert result["code"] == "invalid_schema"
    assert "first" not in result


@pytest.mark.parametrize("state", ["", {"account": "A", "balance": 20}])
def test_check_jsonl_accepts_empty_and_structured_request_states(tmp_path, state):
    source = tmp_path / "structured.jsonl"
    source.write_text(
        json.dumps({"state": state, "questions": {"q": {"type": "choice", "criteria": ["a", "b"]}}}) + "\n"
    )
    result = paths.check(str(source), "jsonl")
    assert result["ok"] and result["first"]["questions"] == 1


def test_check_jsonl_bounds_first_row_and_line_count(tmp_path, monkeypatch):
    long_row = tmp_path / "large.jsonl"
    long_row.write_bytes(b'{"options":[' + b" " * (paths.FIRST_LINE_CAP + 1) + b"]}\n")
    result = paths.check(str(long_row), "jsonl")
    assert result["code"] == "line_too_long"
    assert result["size"] == long_row.stat().st_size

    many = tmp_path / "many.jsonl"
    many.write_text('{"options":["a","b"]}\n' * 5, encoding="utf-8")
    monkeypatch.setattr(paths, "LINE_CAP", 3)
    result = paths.check(str(many), "jsonl")
    assert result["lines"] == 3 and result["lines_capped"] is True
    monkeypatch.setattr(paths, "LINE_CAP", 100_000)
    monkeypatch.setattr(paths, "COUNT_CAP", 45)
    result = paths.check(str(many), "jsonl")
    assert result["lines_capped"] is True


def test_inspection_does_not_call_a_file_empty_when_its_prefix_exhausts_the_scan_limit(tmp_path, monkeypatch):
    source = tmp_path / "prefix.jsonl"
    source.write_text("\n" * 12 + '{"state":"s","question":"q","options":["a","b"]}\n')
    monkeypatch.setattr(paths, "COUNT_CAP", 8)
    result = paths.check(str(source), "jsonl")
    assert result["code"] == "scan_limit" and result["lines_capped"]


def test_check_output_reports_missing_parent_and_overwrite_without_writing(tmp_path):
    missing_parent = paths.check(str(tmp_path / "absent" / "out.jsonl"), "output")
    assert not missing_parent["ok"] and missing_parent["code"] == "dir_missing"

    output = tmp_path / "résultats.jsonl"
    output.write_text("keep", encoding="utf-8")
    before = output.read_bytes()
    result = paths.check(str(output), "output")
    assert result["ok"] and result["writable"] and result["overwrite"]
    assert output.read_bytes() == before


def test_check_rejects_unknown_kind_and_handles_unicode_folder(tmp_path):
    source = tmp_path / "資料"
    source.mkdir()
    file = source / "入力.jsonl"
    file.write_text(
        json.dumps({"state": "состояние", "questions": {"q": {"type": "choice"}}}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    result = paths.check(str(file), "jsonl")
    assert result["ok"] and result["first"]["format"] == "request"
    with pytest.raises(ValueError, match="kind must be one of"):
        paths.check(str(file), "other")


def test_paths_check_route_returns_path_facts_and_validation_errors(client, tmp_path):
    source = tmp_path / "вопросы.jsonl"
    source.write_text('{"state":"s","question":"q?","options":["a","b"]}\n', encoding="utf-8")
    response = client.post("/api/paths/check", json={"path": str(source), "kind": "jsonl"})
    assert response.status_code == 200
    assert response.json()["first"]["format"] == "simple"
    assert response.json()["lines"] == 1
    output = tmp_path / "answers.jsonl"
    output.write_text("untouched", encoding="utf-8")
    output_response = client.post("/api/paths/check", json={"path": str(output), "kind": "output"})
    assert output_response.status_code == 200
    assert output_response.json()["overwrite"] is True
    assert output.read_text(encoding="utf-8") == "untouched"
    assert client.post("/api/paths/check", json={"path": " ", "kind": "jsonl"}).status_code == 400
    assert client.post("/api/paths/check", json={"path": "\0", "kind": "jsonl"}).status_code == 400
