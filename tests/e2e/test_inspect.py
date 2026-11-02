"""The Inspect view's backend: basal-viz for a downloaded bf16 model at /viz, explaining a decision, stopping it."""

import time

import pytest

from basal_ui.engine import feature
from tests.e2e.support import CHOICE, catalog


def stop_inspector(ui):
    ui.delete("/api/inspect")
    deadline = time.monotonic() + 30
    while ui.get("/api/inspect")["state"] != "idle":
        assert time.monotonic() < deadline, "inspector did not stop"
        time.sleep(0.1)


def test_basal_viz_explains_a_decision_of_a_bf16_model_and_stops(ui, bf16_model):
    model_id = bf16_model["id"]
    assert ui.get("/viz/api/info", expect=(503,))["error"].startswith("no model is being inspected")
    committed = ui.status()["memory"]["committed_bytes"]
    try:
        job = ui.run_job("/api/inspect", {"id": model_id}, "inspect")
        assert job["result"]["model"] == model_id and ui.get("/api/inspect")["state"] == "ready"
        assert ui.status()["memory"]["committed_bytes"] > committed  # counted in the budget while it runs
        page = ui.http.get("/viz/")
        assert page.status_code == 200 and "api/explain" in page.text
        info = ui.get("/viz/api/info")
        assert info["model"] == model_id and info["layers"] > 0
        explained = ui.call("POST", "/viz/api/explain", json=CHOICE)
        question = explained["questions"][0]
        assert question["qname"] == "dept" and len(question["keys"]) == 3
        assert question["served_max_diff"] is not None and question["served_max_diff"] < 0.05  # viz = basal-serve
    finally:
        stop_inspector(ui)
    assert ui.http.get("/viz/api/info").status_code == 503
    assert ui.status()["memory"]["committed_bytes"] == committed


def test_ports_basal_viz_cannot_read_are_refused(ui):
    other = next((m for m in catalog(ui) if m["inspect_reason"]), None)
    assert other is not None
    assert ui.detail("POST", "/api/inspect", {"id": other["id"]}, 400) == other["inspect_reason"]


def test_basal_viz_reads_an_mlx_port_on_apple_silicon(ui, hardware, mlx_model):
    if not feature("viz_mlx"):
        pytest.skip("the engine's basal-viz has no MLX modes (2.4.1)")
    if hardware["platform"] != "apple" or "mlx" not in hardware["engines"]:
        pytest.skip("basal-viz reads MLX ports on Apple Silicon with basal[mlx]")
    model_id = mlx_model["id"]
    assert mlx_model["inspect_reason"] is None
    committed = ui.status()["memory"]["committed_bytes"]
    try:
        job = ui.run_job("/api/inspect", {"id": model_id}, "inspect")
        state = ui.get("/api/inspect")
        assert job["result"]["model"] == model_id and state["state"] == "ready"
        assert (state["mode"], state["device"]) == ("mlx", "mlx")
        assert ui.status()["memory"]["committed_bytes"] > committed
        info = ui.get("/viz/api/info")
        assert info["model"] == model_id and info["layers"] > 0
        question = ui.call("POST", "/viz/api/explain", json=CHOICE)["questions"][0]
        assert question["qname"] == "dept" and len(question["keys"]) == 3
    finally:
        stop_inspector(ui)
    assert ui.status()["memory"]["committed_bytes"] == committed
