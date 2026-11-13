"""Scheduled preflight against the separately delivered Comparison implementation."""

import json

import pytest

try:
    from basal_ui.fleet import ComparisonRunner  # noqa: F401
except ImportError:
    pytest.skip("Comparison requires the separately reviewed PR 60", allow_module_level=True)

from tests import test_ui_comparison

app_client = test_ui_comparison.app_client


@pytest.mark.parametrize(
    "mutation, reason",
    [
        ("metadata", "would discard metadata"),
        ("gold", "does not grade gold"),
        ("capability", "do not support questions.q.type"),
        ("option_limit", "limit is 2"),
    ],
)
def test_scheduled_comparison_refuses_unrepresentable_input_before_inference(
    app_client, child, tmp_path, mutation, reason
):
    client, fleet = app_client
    _, model = test_ui_comparison.attach(client, child)
    inputs, outputs = tmp_path / "input", tmp_path / "output"
    inputs.mkdir()
    outputs.mkdir()
    item = json.loads(json.dumps(test_ui_comparison.BODY))
    if mutation == "metadata":
        item["metadata"] = {"must_not_drop": True}
    elif mutation == "gold":
        item["gold"] = 0
    elif mutation == "option_limit":
        item = {"state": "synthetic", "question": "Pick", "options": ["a", "b", "c"]}
    else:
        item["questions"]["q"]["type"] = "multi"
    source = inputs / "bad.jsonl"
    source.write_text(json.dumps(item) + "\n")
    manual = client.post("/api/batch", json={"id": model["id"], "input": str(source)})
    assert manual.status_code == 400
    assert (
        client.post(
            "/api/schedules",
            json={
                "id": "compare",
                "model": model["id"],
                "input_dir": str(inputs),
                "output_dir": str(outputs),
                "interval": {"every_minutes": 1},
            },
        ).status_code
        == 201
    )
    result = client.app.state.schedules.run_due(1000)
    assert result["status"] == "failed"
    row = client.get("/api/schedules/compare/runs").json()["runs"][0]
    assert reason in row["error"]
    assert child.seen == [] and fleet.jobs.get("batch") is None
