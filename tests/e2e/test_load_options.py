"""Load options: the engine modes this machine offers, options reaching basal-serve, the context policy, basal-serve's
own refusals, and options remembered for a model."""

import pytest

from basal_ui.engine import feature
from basal_ui.options import MODE_ORDER
from tests.e2e.support import CHOICE, context_of, long_state


def options_of(ui, model_id):
    return ui.get("/api/models/options", params={"id": model_id})


def test_the_mode_list_covers_every_mode_and_offers_the_default(ui, text_model, hardware):
    d = options_of(ui, text_model["id"])
    modes = {m["mode"]: m for m in d["modes"]}
    assert set(MODE_ORDER) <= set(modes) and {"ollama", "llamacpp"} <= {m for m, v in modes.items() if v["available"]}
    default = [m for m, v in modes.items() if v["default"]]
    assert default == [text_model["mode"]] and modes[text_model["mode"]]["available"]
    assert all(v["reason"] for v in modes.values() if not v["available"])
    if hardware["platform"] != "cuda":
        assert not any(modes[m]["available"] for m in ("fast", "fast-nocompile", "fast-exit", "fp8", "nvfp4", "int8"))
    if "tpu" not in hardware["engines"]:
        assert not modes["tpu"]["available"]


def test_options_reach_basal_serve_and_the_context_policy_applies(ui, text_model):
    model_id = text_model["id"]
    chosen = {"deterministic": True, "orders": "2", "context_overflow": "refuse"}
    m = ui.load(model_id, chosen)
    assert m["command"][-5:] == ["--deterministic", "--orders", "2", "--context-overflow", "refuse"]
    context = context_of(m["entry"])
    assert context["overflow"] == "refuse" and m["entry"].get("orders") in (2, "2")
    limit = context["limit"]
    body = {"state": long_state(limit), "questions": {"q": {"type": "noul", "instructions": "Czy to umowa najmu?"}}}
    refused = ui.decide(model_id, body, expect=(422,))
    assert "the prompt is" in refused["error"] and refused["code"] == "INVALID_REQUEST"
    assert ui.decide(model_id, CHOICE)["answers"]["dept"]["choice"]
    ui.load(model_id)  # back to the defaults
    assert "--deterministic" not in ui.model(model_id)["command"]


def test_invalid_unavailable_and_refused_combinations(ui, text_model):
    model_id = text_model["id"]
    ui.load(model_id)
    assert "orders" in ui.detail("POST", "/api/restart", {"id": model_id, "options": {"orders": "7"}}, 400)
    assert "unknown load option" in ui.detail("POST", "/api/restart", {"id": model_id, "options": {"x": 1}}, 400)
    unavailable = next(m["mode"] for m in options_of(ui, model_id)["modes"] if not m["available"])
    assert ui.detail("POST", "/api/restart", {"id": model_id, "options": {"mode": unavailable}}, 400).startswith(
        f"--mode {unavailable}:"
    )
    # basal-serve's own refusal reaches the model's row (on every engine: prefix cache with --deterministic)
    ui.post("/api/restart", {"id": model_id, "options": {"deterministic": True, "prefix_cache": True}})
    m = ui.wait_model(model_id)
    assert m["state"] == "error" and "--prefix-cache" in m["error"]
    assert ui.load(model_id)["state"] == "ready"


def test_remembered_options_are_used_by_a_plain_load(ui, text_model):
    model_id = text_model["id"]
    try:
        saved = ui.put("/api/models/options", {"id": model_id, "options": {"max_batch": 16}})
        assert saved["saved"] is True and saved["options"]["max_batch"] == 16
        ui.unload(model_id)
        ui.post("/api/load", {"id": model_id})  # no options: the remembered ones
        m = ui.wait_model(model_id)
        assert (
            m["state"] == "ready" and m["options"] == {"max_batch": 16} and m["command"][-2:] == ["--max-batch", "16"]
        )
    finally:
        ui.put("/api/models/options", {"id": model_id, "options": {}})
    assert options_of(ui, model_id)["saved"] is False
    ui.load(model_id)


def test_int8_mode_is_listed_with_its_reason(ui, hardware, text_model):
    row = next(m for m in options_of(ui, text_model["id"])["modes"] if m["mode"] == "int8")
    assert not row["default"]
    if not feature("int8"):
        assert row["reason_code"] == "mode_int8_engine"
    elif hardware["platform"] != "cuda":
        assert row["reason_code"] == "cuda_graph_gpu"
    else:  # the catalog's text models are not basal-omni ultra: its accuracy gate passed on that model only
        assert row["reason_code"] in ("mode_int8_model", "mode_int8_hardware", "cuda_graph_port")


def test_pico_serves_with_mlx_on_apple_silicon(ui, hardware, pico_model):
    if hardware["platform"] != "apple" or "mlx" not in hardware["engines"]:
        pytest.skip("--mode mlx needs Apple Silicon with basal[mlx]")
    model_id = pico_model["id"]
    modes = {m["mode"]: m for m in options_of(ui, model_id)["modes"]}
    assert modes["mps"]["reason_code"] == "reason_recurrent"
    if not feature("mlx_recurrent"):
        assert modes["mlx"]["reason_code"] == "reason_mlx_recurrent"
        pytest.skip("this engine's --mode mlx does not serve Qwen3.5 (2.4.1)")
    assert modes["mlx"]["available"] and modes["mlx-q8"]["reason_code"] == "reason_mlx_q8_recurrent"
    caches = ui.detail(
        "PUT", "/api/models/options", {"id": model_id, "options": {"mode": "mlx", "state_cache_gb": 1}}, 400
    )
    assert "keep attention keys and values only" in caches
    try:
        m = ui.load(model_id, {"mode": "mlx"})
        assert m["command"][m["command"].index("--mode") + 1] == "mlx"
        answer = ui.decide(model_id, CHOICE)["answers"]["dept"]
        assert answer["choice"] in CHOICE["questions"]["dept"]["criteria"]
    finally:
        ui.unload(model_id)
        ui.put("/api/models/options", {"id": model_id, "options": {}})
