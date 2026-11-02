import dataclasses
import re
import time

import pytest
from fastapi.testclient import TestClient

from basal_ui import backends, catalog, engine, options, settings
from basal_ui.app import create_app
from basal_ui.catalog import Variant
from basal_ui.fleet import Fleet
from basal_ui.hardware import Hardware
from basal_ui.history import History
from basal_ui.stats import Stats

CPU = Hardware("cpu", "cpu", 1024**4, None, None, frozenset())
CUDA = Hardware("cuda", "NVIDIA H100", 80 * 1024**3, None, (9, 0), frozenset({"cuda", "vllm", "sglang"}))
BF16 = Variant("mini", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False)
GGUF = Variant("mini-gguf", "Remek/basal-2.0-mini-GGUF", "basal-2.0", "mini", "GGUF", "GGUF Q8_0", "m.gguf", 100, False)


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))


def wait_for(condition, timeout=3):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert condition()


def available(variant, hw):
    return {m["mode"] for m in options.available_modes(variant, hw, catalog.assess(variant, hw)) if m["available"]}


def probe(monkeypatch, **flags):
    """The Engine capabilities basal_ui.options sees: the named probes as given, the others as installed."""
    real = engine.feature
    monkeypatch.setattr(options, "feature", lambda name: flags[name] if name in flags else real(name))


PICO = Variant("pico", "Remek/basal-omni-pico", "basal-omni", "pico", "bf16", "bf16", None, 100, True, recurrent=True)
APPLE = Hardware("apple", "Apple M4 Max", 64 * 1024**3, None, None, frozenset({"mps", "mlx"}))


def test_defaults_emit_nothing_and_every_non_default_its_flag():
    assert options.args({}) == [] and options.args(dict(options.DEFAULTS)) == []
    flags = options.args(
        {
            "mode": "fast",
            "quant": "fp8",
            "attn": "fa3",
            "deterministic": True,
            "orders": "2le4",
            "auto_band": [0.1, 0.9],
            "soam": False,
            "max_batch": 16,
            "wait_ms": 2,
            "prefix_cache": True,
            "state_cache_gb": 1.5,
            "scheduler": "on",
            "long_lane": True,
            "long_tokens": 4096,
            "context_overflow": "refuse",
            "truncate_head": 0.5,
            "max_len": 8192,
            "vllm_preset": "deterministic",
            "ollama_model": "basal:mini",
            "device": "cpu",
            "think": True,
            "think_tokens": 512,
            "early_exit": "0.99",
        }
    )
    assert flags == [
        "--quant",
        "fp8",
        "--attn",
        "fa3",
        "--deterministic",
        "--orders",
        "2le4",
        "--auto-band",
        "0.1",
        "0.9",
        "--soam",
        "off",
        "--max-batch",
        "16",
        "--wait-ms",
        "2",
        "--state-cache-gb",
        "1.5",
        "--prefix-cache",
        "on",
        "--scheduler",
        "on",
        "--long-lane",
        "on",
        "--long-tokens",
        "4096",
        "--context-overflow",
        "refuse",
        "--truncate-head",
        "0.5",
        "--max-len",
        "8192",
        "--vllm-preset",
        "deterministic",
        "--ollama-model",
        "basal:mini",
        "--device",
        "cpu",
        "--think",
        "auto",
        "--think-tokens",
        "512",
        "--early-exit",
        "0.99",
    ]


def test_layout_is_passed_and_refused_with_the_caches():
    assert options.args({"layout": "qfirst"}) == ["--layout", "qfirst"]
    with pytest.raises(ValueError, match="layout"):
        options.validate({"layout": "statefirst"})
    for cache in ({"prefix_cache": True}, {"state_cache_gb": 1.0}):
        with pytest.raises(ValueError, match="need the canonical layout"):
            options.check_combination(BF16, CUDA, options.validate({"layout": "state_twice", **cache}), "fast")
        options.check_combination(BF16, CUDA, options.validate({"layout": "canonical", **cache}), "fast")
    options.check_combination(BF16, CUDA, options.validate({"layout": "qfirst"}), "fast")


@pytest.mark.parametrize("orders", ["2le00", "2le01"])
def test_orders_limit_must_be_at_least_two(orders):
    with pytest.raises(ValueError, match="orders"):
        options.validate({"orders": orders})


@pytest.mark.parametrize(
    "bad, message",
    [
        ({"colour": 1}, "unknown load option: colour"),
        ({"orders": "3"}, "orders"),
        ({"max_batch": 0}, "max_batch"),
        ({"gpu_memory": 0}, "gpu_memory"),
        ({"truncate_head": 1.5}, "truncate_head"),
        ({"early_exit": "0.9"}, "early_exit"),
        ({"deterministic": "yes"}, "deterministic"),
        ({"max_len": 100}, "max_len"),
        ({"mode": "warp"}, "mode"),
        ({"mode": "mlx", "quant": "fp8"}, "--quant fp8 is not supported with --mode mlx"),
        ({"eager_attn": "sliding=fa9,full=math"}, "eager_attn"),
        ({"lean": "yes"}, "lean"),
        ({"think_memory": 0}, "think_memory"),
        ({"ple_offload": "on"}, "ple_offload"),
    ],
)
def test_invalid_options_name_the_option(bad, message):
    with pytest.raises(ValueError, match=message):
        options.validate(bad)


def test_every_engine_switch_reaches_basal_serve_as_it_reads_it(tmp_path):
    from basal.server import parser

    chosen = {
        "gemma_attn": "flex",
        "merge": "off",
        "lean": "pass2",
        "tree": "flex",
        "batches": "fine",
        "readout_layer": "on",
        "template_prefix": "off",
        "fp8_accum": "full",
        "eager_attn": "sliding=fa3,full=efficient",
        "eager_forward": "hf",
        "vllm_fast_prefill": "off",
        "dtype": "float16",
        "exit_heads": "/heads",
        "think_calibration": "off",
        "think_model": "/think",
        "think_adapter": "/adapter",
        "think_memory": 0.5,
        "think_max_len": 4096,
        "think_log": "t.log",
    }
    engine_25 = {  # engine 2.5's flags: only what the installed basal-serve declares (validate refuses the rest)
        "mlx_forward": "basal",
        "mlx_compute": "bf16",
        "order_average": 4,
        "reread": 0.9,
        "reread_combine": "second",
        "reread_max_tokens": 1024,
        "reread_image": 280,
        "prepared": "off",
        "global_attn": "triton",
        "compile_scope": "layer",
        "state_cache_bits": 8,
        "fp8_scaling": "row",
        "fp8_bf16_layers": 0,
        "quant_load": "cpu",
        "graph_memory_gb": 12.5,
        "nvfp4_amax": "/a.json",
        "nvfp4_scope": "mlp",
        "rank_max_candidates": 50,
    }
    flags = engine.serve_flags()
    chosen.update({k: v for k, v in engine_25.items() if "--" + k.replace("_", "-") in flags})
    if "--vllm-overlap" in flags:  # the engine 2.5 on/off switches (argparse keeps them as strings)
        switches = {"vllm_overlap": True, "state_cache_lock": True, "long_lane_gdn": True}
        on_off = parser().parse_args(["--model", "m", *options.args(switches)])
        assert {k: getattr(on_off, k) for k in switches} == {k: "on" for k in switches}
    if engine.feature("adapter"):
        ad = tmp_path / "ad"
        ad.mkdir()
        (ad / "adapter_config.json").write_text('{"peft_type": "LORA", "r": 2, "lora_alpha": 4}', encoding="utf-8")
        (ad / "adapter_model.safetensors").write_bytes(b"")
        chosen["adapter"] = str(ad)
    a = parser().parse_args(["--model", "m", "--ple-offload", "on", "--strict-calibration", *options.args(chosen)])
    assert {k: getattr(a, k) for k in chosen} == chosen
    assert options.args({"ple_offload": True, "strict_calibration": True}) == [
        "--ple-offload",
        "on",
        "--strict-calibration",
    ]
    defaults = parser().parse_args(["--model", "m"])
    assert {k: getattr(defaults, k) for k in chosen} == {k: options.DEFAULTS[k] for k in chosen}


def test_the_adapter_option_is_refused_and_explained_without_an_engine_that_takes_it(tmp_path, monkeypatch):
    probe(monkeypatch, adapter=False)  # engine 2.4.1
    with pytest.raises(ValueError, match="load option adapter: LoRA adapters at load need basal engine 2.5.0"):
        options.validate({"adapter": str(tmp_path)})
    app = create_app(catalog_fn=lambda **_: ([BF16], "test"), hardware_fn=lambda: CPU)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        data = client.get("/api/models/options", params={"id": BF16.id}).json()
        assert data["unavailable"]["adapter"] == "LoRA adapters at load need basal engine 2.5.0 or newer"
        refused = client.put("/api/models/options", json={"id": BF16.id, "options": {"adapter": str(tmp_path)}})
        assert refused.status_code == 400 and "2.5.0" in refused.json()["detail"]


def test_engine_25_load_options_follow_the_installed_serve_flags(monkeypatch):
    """A load option whose flag the installed basal-serve lacks is refused with the engine reason and listed
    unavailable on the options route; its default never emits a flag. On 2.5 the values reach basal-serve."""
    real = engine.serve_flags()
    if "--reread" in real:
        assert "reread" not in options.unavailable_options()
        assert options.args({"reread": 0.9}) == ["--reread", "0.9"]
    monkeypatch.setattr(options, "serve_flags", lambda: real - {"--reread"})  # an engine that lacks the flag (2.4.1)
    assert options.unavailable_options()["reread"] == "needs basal engine 2.5.0 or newer"
    with pytest.raises(ValueError, match="load option reread: needs basal engine 2.5.0 or newer"):
        options.validate({"reread": 0.9})
    assert options.args({"reread": None}) == []  # the default never needs the flag
    app = create_app(catalog_fn=lambda **_: ([BF16], "test"), hardware_fn=lambda: CPU)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        data = client.get("/api/models/options", params={"id": BF16.id}).json()
        assert data["unavailable"]["reread"] == "needs basal engine 2.5.0 or newer"
        assert data["options"]["reread"] is None
        refused = client.put("/api/models/options", json={"id": BF16.id, "options": {"reread": 0.9}})
        assert refused.status_code == 400 and "needs basal engine 2.5.0" in refused.json()["detail"]


def test_graph_memory_gb_sizes_the_graph_engine_estimate():
    sized = catalog.assess(BF16, CUDA, {"graph_memory_gb": 8})
    assert sized.est_memory_bytes == int(BF16.weights_bytes * 1.2) + 8 * catalog.GIB
    assert sized.est_memory_bytes != catalog.assess(BF16, CUDA).est_memory_bytes  # the fixed 4 GiB overhead otherwise
    # eager (the CPU default) has no graphs: the option changes nothing
    assert (
        catalog.assess(BF16, CPU, {"graph_memory_gb": 8}).est_memory_bytes == catalog.assess(BF16, CPU).est_memory_bytes
    )


@pytest.mark.skipif(not engine.feature("adapter"), reason="needs an engine that takes --adapter (2.5.0)")
def test_an_adapter_that_basal_serve_would_refuse_is_refused_before_loading(tmp_path):
    with pytest.raises(ValueError, match="load option adapter: --adapter nowhere: no adapter_config.json"):
        options.validate({"adapter": str(tmp_path / "nowhere")})
    dora = tmp_path / "dora"
    dora.mkdir()
    (dora / "adapter_config.json").write_text(
        '{"fine_tune_type": "dora", "lora_parameters": {"scale": 2}}', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="fine_tune_type dora"):
        options.validate({"adapter": str(dora)})


def test_media_loads_are_eager_or_vllm_where_cuda_has_vllm():
    omni = Variant(
        "omni",
        "Remek/basal-omni-1.0-mini",
        "basal-omni-1.0",
        "mini",
        "bf16",
        "bf16",
        None,
        100,
        True,
        media=("image", "audio"),
    )
    media_cuda = Hardware("cuda", "NVIDIA H100", 80 * 1024**3, None, (9, 0), frozenset({"cuda", "vllm", "media"}))
    no_vllm = Hardware("cuda", "NVIDIA H100", 80 * 1024**3, None, (9, 0), frozenset({"cuda", "media"}))
    modes = lambda hw: {m["mode"]: m["available"] for m in options.available_media_modes(catalog.assess(omni, hw), hw)}
    assert modes(media_cuda) == {"eager": True, "vllm": True}
    assert modes(no_vllm) == {"eager": True, "vllm": False}
    assert modes(CUDA) == {"eager": False, "vllm": False}  # no basal[media]
    assert catalog.serve_args(omni, "/m", "vllm", "cuda")[-4:] == ["--mode", "vllm", "--media", "on"]
    assert catalog.serve_args(omni, "/m", "eager", "apple")[-4:] == ["--media", "on", "--device", "mps"]


def test_available_modes_follow_the_hardware_and_the_variant():
    assert available(BF16, CPU) == {"eager", "mps", "ollama", "llamacpp"}
    gguf_cpu = Hardware("cpu", "cpu", 1024**4, None, None, frozenset({"gguf"}))
    assert available(GGUF, gguf_cpu) == {"gguf", "ollama", "llamacpp"}
    cuda = available(BF16, CUDA)
    assert {"fast", "fast-nocompile", "fp8", "eager", "vllm", "sglang"} <= cuda
    assert not cuda & {"tpu", "strix", "mlx", "nvfp4", "fast-exit", "int8"}
    reasons = {m["mode"]: m["reason"] for m in options.available_modes(BF16, CUDA, catalog.assess(BF16, CUDA))}
    assert reasons["fast-exit"] == "exit heads ship with basal-1.0" and "TPU" in reasons["tpu"]
    assert [m["mode"] for m in options.available_modes(BF16, CUDA, catalog.assess(BF16, CUDA)) if m["default"]] == [
        "fast"
    ]


def test_strix_and_tpu_modes_follow_architecture_and_bf16_format():
    hw = Hardware("cuda", "AMD Strix Halo", 96 * 1024**3, None, (11, 5), frozenset({"cuda", "rocm", "strix", "tpu"}))
    supported = Variant("supported", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False)
    older_llama = Variant("older", "Remek/basal-1.5-4.5B", "basal-1.5", "4.5B", "bf16", "bf16", None, 100, False)
    gemma = Variant("gemma", "Remek/basal-omni-1.0-mini", "basal-omni-1.0", "mini", "bf16", "bf16", None, 100, True)
    recurrent = Variant(
        "pico", "Remek/basal-omni-pico", "basal-omni", "pico", "bf16", "bf16", None, 100, True, recurrent=True
    )
    fp8 = Variant("fp8", "Remek/basal-2.0-mini-FP8", "basal-2.0", "mini", "FP8", "FP8", None, 100, False)
    assert {"strix", "tpu"} <= available(supported, hw)
    assert {"strix", "tpu"} <= available(older_llama, hw)
    assert not available(gemma, hw) & {"strix", "tpu"}
    assert not available(recurrent, hw) & {"strix", "tpu"}
    assert not available(fp8, hw) & {"strix", "tpu"}
    reasons = {row["mode"]: row["reason_code"] for row in options.available_modes(gemma, hw, catalog.assess(gemma, hw))}
    assert reasons["strix"] == reasons["tpu"] == "mode_architecture"
    reasons = {row["mode"]: row["reason_code"] for row in options.available_modes(fp8, hw, catalog.assess(fp8, hw))}
    assert reasons["strix"] == "mode_strix_format" and reasons["tpu"] == "mode_tpu_format"


def test_rocm_is_not_offered_the_nvidia_only_modes():
    rocm = Hardware(
        "cuda", "AMD Radeon 8060S", 96 * 1024**3, None, (11, 5), frozenset({"cuda", "rocm", "vllm", "sglang"})
    )
    assert not available(BF16, rocm) & {"vllm", "sglang", "fp8", "nvfp4"}
    fp8 = Variant("fp8", "Remek/basal-omni-1.0-mini-FP8", "basal-omni-1.0", "mini", "FP8", "FP8", None, 100, False)
    assert not catalog.assess(fp8, rocm).runnable


def test_availability_messages_return_reproducible_codes_and_params():

    recurrent = Variant(
        "pico", "Remek/basal-omni-pico", "basal-omni", "pico", "bf16", "bf16", None, 100, True, recurrent=True
    )
    media_variant = Variant(
        "omni",
        "Remek/basal-omni-1.0-mini",
        "basal-omni-1.0",
        "mini",
        "bf16",
        "bf16",
        None,
        100,
        True,
        media=("image", "audio"),
    )
    apple = Hardware("apple", "M4", 32 * 1024**3, None, None, frozenset({"mlx"}))
    apple_no_mlx = Hardware("apple", "M4", 32 * 1024**3, None, None, frozenset())
    old_cuda = Hardware("cuda", "old NVIDIA", 8 * 1024**3, None, (7, 5), frozenset({"cuda"}))
    no_vllm = Hardware("cuda", "NVIDIA H100", 80 * 1024**3, None, (9, 0), frozenset({"cuda"}))
    blackwell = Hardware("cuda", "NVIDIA B200", 192 * 1024**3, None, (10, 0), frozenset({"cuda", "vllm"}))
    scenarios = (
        (BF16, CPU),
        (BF16, CUDA),
        (BF16, no_vllm),
        (BF16, apple),
        (BF16, apple_no_mlx),
        (BF16, old_cuda),
        (GGUF, CPU),
        (recurrent, CUDA),
        (media_variant, CUDA),
        (
            BF16,
            Hardware(
                "cuda", "AMD Strix Halo", 96 * 1024**3, None, (11, 5), frozenset({"cuda", "rocm", "strix", "tpu"})
            ),
        ),
        (
            Variant("omni", "Remek/basal-omni-1.0-mini", "basal-omni-1.0", "mini", "bf16", "bf16", None, 100, True),
            Hardware(
                "cuda", "AMD Strix Halo", 96 * 1024**3, None, (11, 5), frozenset({"cuda", "rocm", "strix", "tpu"})
            ),
        ),
    )
    templates = {**catalog.ASSESSMENT_TEMPLATES, **options.OPTION_TEMPLATES}
    outputs = []
    for variant, hw in scenarios:
        outputs.extend(options.available_modes(variant, hw, catalog.assess(variant, hw)))
        if variant.media:
            outputs.extend(options.available_media_modes(catalog.assess(variant, hw), hw))
    outputs.extend(options.available_modes(BF16, blackwell, catalog.assess(BF16, blackwell)))
    for row in outputs:
        if row["reason"] is None:
            assert row["reason_code"] is row["reason_params"] is None
            continue
        template = templates[row["reason_code"]]
        assert row["reason"] == template.format(**row["reason_params"])
        assert set(row["reason_params"]) == set(re.findall(r"\{(\w+)\}", template))


def test_cuda_graph_modes_require_ampere_or_newer():
    sm75 = Hardware("cuda", "old NVIDIA", 8 * 1024**3, None, (7, 5), frozenset({"cuda"}))
    modes = {m["mode"]: m for m in options.available_modes(BF16, sm75, catalog.assess(BF16, sm75))}
    for mode in ("fast", "fast-nocompile", "fast-exit", "fp8", "nvfp4"):
        assert not modes[mode]["available"]
        assert "8.0+" in modes[mode]["reason"]
    sm86 = Hardware("cuda", "RTX 3090", 24 * 1024**3, None, (8, 6), frozenset({"cuda"}))
    modes = {m["mode"]: m for m in options.available_modes(BF16, sm86, catalog.assess(BF16, sm86))}
    assert modes["fast"]["available"] and not modes["fp8"]["available"]
    assert modes["fp8"]["reason"] == options.fp8_unavailable(sm86) and "8.9+" in modes["fp8"]["reason"]
    with pytest.raises(ValueError, match=r"8\.9\+"):
        options.check_combination(BF16, sm86, options.validate({"quant": "fp8"}, "fast"), "fast")
    sm89 = Hardware("cuda", "RTX 4090", 24 * 1024**3, None, (8, 9), frozenset({"cuda"}))
    options.check_combination(BF16, sm89, options.validate({"quant": "fp8"}, "fast"), "fast")


ULTRA = Variant("ultra", "Remek/basal-omni-1.0-ultra", "basal-omni-1.0", "ultra", "bf16", "bf16", None, 100, True)


@pytest.mark.parametrize("int8", [True, False])
def test_int8_serves_basal_omni_ultra_on_ampere_or_newer_with_engine_2_5(monkeypatch, int8):
    if int8 and not engine.feature("int8"):
        pytest.skip("the installed engine has no --mode int8 (2.4.1)")
    probe(monkeypatch, int8=int8)
    sm86 = Hardware("cuda", "RTX 3090", 24 * 1024**3, None, (8, 6), frozenset({"cuda"}))

    def row(variant, hw):
        return next(m for m in options.available_modes(variant, hw, catalog.assess(variant, hw)) if m["mode"] == "int8")

    if not int8:  # engine 2.4.1: no --mode int8, and that is the reason everywhere
        for variant, hw in ((ULTRA, sm86), (ULTRA, CUDA), (BF16, CUDA), (PICO, CUDA), (BF16, CPU)):
            assert row(variant, hw)["reason_code"] == "mode_int8_engine"
        if "int8" not in options.MODES:  # the installed engine is 2.4.1: basal-serve's own mode list refuses it
            with pytest.raises(ValueError, match="load option mode must be one of"):
                options.validate({"mode": "int8"}, "fast")
        return
    assert row(ULTRA, sm86)["available"] and row(ULTRA, CUDA)["available"]  # Ampere and Hopper
    # the CUDA graph engine's graphs and compile workspace count for int8 as for fp8 (catalog.GRAPH_ENGINE_MODES)
    assert backends.estimate(ULTRA, CPU, "int8").est_memory_bytes == 120 + catalog.GRAPH_ENGINE_OVERHEAD
    assert backends.estimate(ULTRA, CPU, "eager").est_memory_bytes == 120 + catalog.GIB
    assert not row(ULTRA, sm86)["default"]  # never the default: fast (bf16) stays basal-serve's choice
    options.check_combination(ULTRA, sm86, options.validate({"mode": "int8"}, "fast"), "fast")
    with pytest.raises(ValueError, match="load option quant must be one of"):  # basal-serve refuses --quant int8
        options.validate({"quant": "int8"}, "fast")
    sm75 = Hardware("cuda", "old NVIDIA", 8 * 1024**3, None, (7, 5), frozenset({"cuda"}))
    assert "8.0+" in row(ULTRA, sm75)["reason"]  # the CUDA graph engine's own Ampere floor
    rocm = Hardware("cuda", "Radeon", 64 * 1024**3, None, (9, 4), frozenset({"cuda", "rocm"}))
    assert row(ULTRA, rocm)["reason_code"] in ("mode_int8_hardware", "cuda_graph_gpu")
    for variant in (BF16, PICO, dataclasses.replace(ULTRA, tier="max"), dataclasses.replace(ULTRA, port="FP8")):
        assert not row(variant, CUDA)["available"], variant
    assert row(BF16, CUDA)["reason_code"] == "mode_int8_model"
    assert "Qwen3.5" in row(PICO, CUDA)["reason"]
    with pytest.raises(ValueError, match="basal-omni ultra"):
        options.check_combination(BF16, CUDA, options.validate({"mode": "int8"}, "fast"), "fast")
    sm86_omni = options.validate({"mode": "int8"}, "fast")
    with pytest.raises(ValueError, match=r"8\.0\+"):
        options.check_combination(ULTRA, rocm, sm86_omni, "fast")
    assert row(ULTRA, CPU)["reason"] == catalog.ASSESSMENT_TEMPLATES["cuda_graph_gpu"]


def test_pico_modes_follow_basal_serve_refusals(monkeypatch):
    probe(monkeypatch, mlx_recurrent=False)
    pico = PICO
    fla = Hardware("cuda", "NVIDIA H100", 80 * 1024**3, None, (9, 0), frozenset({"cuda", "vllm", "fla"}))
    modes = {m["mode"]: m for m in options.available_modes(pico, fla, catalog.assess(pico, fla))}
    assert {m for m, row in modes.items() if row["available"]} >= {"fast", "fast-nocompile", "eager", "vllm"}
    assert [m for m, row in modes.items() if row["default"]] == ["fast"]
    for mode in ("fast-exit", "mps"):
        assert modes[mode]["reason"] == options.RECURRENT_REASON
    for mode in ("fp8", "nvfp4"):
        assert not modes[mode]["available"] and "Qwen3.5" in modes[mode]["reason"]
    assert modes["mlx"]["reason"] == modes["mlx-q8"]["reason"] == options.MLX_RECURRENT_REASON
    no_fla = {m["mode"]: m for m in options.available_modes(pico, CUDA, catalog.assess(pico, CUDA))}
    assert no_fla["fast"]["reason"] == no_fla["fast-nocompile"]["reason"] == options.FLA_REASON
    assert [m for m, row in no_fla.items() if row["default"]] == ["eager"]
    options.check_combination(pico, fla, options.validate({}, "fast"), "fast")
    for refused in ({"long_lane": True}, {"quant": "fp8"}):  # refused by 2.4.1 and 2.5.0 alike
        with pytest.raises(ValueError, match=r"Qwen3\.5"):
            options.check_combination(pico, fla, options.validate(refused), "fast")
    if "--long-lane-gdn" in engine.serve_flags():  # 2.5.1 serves the long lane with --long-lane-gdn on
        options.check_combination(pico, fla, options.validate({"long_lane": True, "long_lane_gdn": True}), "fast")
    # basal-serve 2.4.1 refuses these in the graph engine, 2.5.0 serves them: basal-ui asks the engine (gdn_graph.refused)
    engine_rules = (
        ({"batches": "compact"}, {"batches": "compact"}),
        ({"prefix_cache": True}, {"prefix": True}),
        ({"state_cache_gb": 1.0}, {"state_cache_gb": 1.0}),
        ({"lean": "on"}, {"lean": True}),
    )
    for chosen, rule in engine_rules:
        if options.gdn_refused(None, device=False, **rule):
            with pytest.raises(ValueError, match=r"Qwen3\.5"):
                options.check_combination(pico, fla, options.validate(chosen), "fast")
        else:
            options.check_combination(pico, fla, options.validate(chosen), "fast")
    options.check_combination(pico, fla, options.validate({"prefix_cache": True}), "eager")


@pytest.mark.parametrize("mlx_recurrent", [True, False])
def test_pico_serves_with_mlx_from_its_bf16_weights_with_engine_2_5(monkeypatch, mlx_recurrent):
    probe(monkeypatch, mlx_recurrent=mlx_recurrent)
    modes = {m["mode"]: m for m in options.available_modes(PICO, APPLE, catalog.assess(PICO, APPLE))}
    assert [m for m, row in modes.items() if row["default"]] == ["eager"]  # basal-serve's default stays eager
    assert modes["mps"]["reason"] == options.RECURRENT_REASON
    plain = options.validate({"mode": "mlx"}, "eager")
    if not mlx_recurrent:  # engine 2.4.1
        assert modes["mlx"]["reason"] == modes["mlx-q8"]["reason"] == options.MLX_RECURRENT_REASON
        assert modes["mlx"]["reason_code"] == "reason_mlx_recurrent"
        with pytest.raises(ValueError, match="from basal engine 2.5.0 on"):
            options.check_combination(PICO, APPLE, plain, "eager")
        return
    assert modes["mlx"]["available"] and modes["mlx"]["reason"] is None
    assert modes["mlx-q8"]["reason"] == options.MLX_Q8_RECURRENT_REASON
    assert modes["mlx-q8"]["reason_code"] == "reason_mlx_q8_recurrent"
    options.check_combination(PICO, APPLE, plain, "eager")
    for refused, match in (
        ({"mode": "mlx-q8"}, "quantised MLX weights"),
        ({"mode": "mlx", "quant": "q8"}, "quantised MLX weights"),
        ({"mode": "mlx", "state_cache_gb": 1.0}, "keep attention keys and values only"),
        ({"mode": "mlx", "prefix_cache": True}, "keep attention keys and values only"),
        ({"mode": "mlx", "mlx_packed": True}, "MLX packed needs a Llama"),
    ):
        with pytest.raises(ValueError, match=match):
            options.check_combination(PICO, APPLE, options.validate(refused, "eager"), "eager")
    # a quantised MLX export of a Qwen3.5 model: refused by the engine as well
    pico_mlx = Variant(
        "pico-mlx",
        "Remek/basal-omni-pico-MLX-8bit",
        "basal-omni",
        "pico",
        "MLX-8bit",
        "MLX 8-bit",
        None,
        1,
        True,
        recurrent=True,
    )
    rows = {m["mode"]: m for m in options.available_modes(pico_mlx, APPLE, catalog.assess(pico_mlx, APPLE))}
    assert rows["mlx"]["reason"] == options.MLX_Q8_RECURRENT_REASON
    with pytest.raises(ValueError, match="quantised MLX weights"):
        options.check_combination(pico_mlx, APPLE, options.validate({"mode": "mlx"}, "mlx"), "mlx")
    # the CPU has no MLX: the device reason comes first
    assert options.available_modes(PICO, CPU, catalog.assess(PICO, CPU))[0]["mode"] == "fast"
    cpu = {m["mode"]: m for m in options.available_modes(PICO, CPU, catalog.assess(PICO, CPU))}
    assert cpu["mlx"]["reason"] == options.OPTION_TEMPLATES["mode_mlx_device"]


def test_thinking_needs_basal_2_0_4_5b_with_vllm_and_early_exit_needs_fast_exit():
    big = Variant("big", "Remek/basal-2.0-4.5B", "basal-2.0", "4.5B", "bf16", "bf16", None, 100, False)
    assert options.args({"think": True, "think_tokens": 512, "early_exit": "0.99", "mode": "fast-exit"}) == [
        "--think",
        "auto",
        "--think-tokens",
        "512",
        "--early-exit",
        "0.99",
    ]
    merged = options.validate({"think": True})
    assert options.think_unavailable(BF16, CUDA) == options.THINK_REASON  # the mini has no thinking model
    assert options.think_unavailable(big, CUDA) is None
    assert "Python of an environment with vLLM" in options.think_unavailable(big, CPU)
    assert options.think_unavailable(big, CPU, {"think_url": "http://gpu:8100"}) is None
    with pytest.raises(ValueError, match="basal-2.0 4.5B"):
        options.check_combination(BF16, CUDA, merged, "fast")
    with pytest.raises(ValueError, match="early_exit needs --mode fast-exit"):
        options.check_combination(big, CUDA, options.validate({"early_exit": "0.99"}), "fast")
    options.check_combination(big, CUDA, options.validate({"early_exit": "0.99", "mode": "fast-exit"}), "fast")


def test_load_options_reach_the_command_are_remembered_and_reused_by_restart(tmp_path, monkeypatch):
    fleet = Fleet(
        models_dir_fn=lambda: tmp_path,
        platform_fn=lambda: "cpu",
        settings_mod=settings,
        hardware_fn=lambda: CPU,
        stats=Stats(tmp_path / "s.json"),
        history=History(tmp_path / "h.jsonl"),
    )
    commands = []

    def start(self, assessment, gen, media=False, cache_dir=None, serve=None):
        with self.lock:  # remembered before it is reported ready, as Runner._start does
            settings.remember_loaded(assessment.variant.id, media, self.options)
            self.state = "ready"
        commands.append(self._build_command(BF16, tmp_path, assessment, media, 1, settings.serve_settings()))

    monkeypatch.setattr("basal_ui.fleet.Runner._start", start)
    app = create_app(
        catalog_fn=lambda **_: ([BF16], "test"),
        hardware_fn=lambda: CPU,
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        bad = client.post("/api/load", json={"id": "mini", "options": {"orders": "7"}})
        assert bad.status_code == 400 and "orders" in bad.json()["detail"]
        unavailable = client.post("/api/load", json={"id": "mini", "options": {"mode": "mlx"}})
        assert unavailable.status_code == 400 and unavailable.json()["detail"].startswith("--mode mlx:")
        chosen = {"deterministic": True, "orders": "2", "context_overflow": "refuse"}
        assert client.post("/api/load", json={"id": "mini", "options": chosen}).status_code == 202
        wait_for(lambda: len(commands) == 1)
        assert commands[-1][-5:] == ["--deterministic", "--orders", "2", "--context-overflow", "refuse"]
        assert client.get("/api/status").json()["models"][0]["options"] == chosen
        assert settings.last_models() == [{"id": "mini", "media": False, "options": chosen}]
        assert client.post("/api/restart", json={"id": "mini"}).status_code == 202
        wait_for(lambda: len(commands) == 2)
        assert commands[-1][-5:] == ["--deterministic", "--orders", "2", "--context-overflow", "refuse"]
        client.post("/api/restart", json={"id": "mini", "options": {}})
        wait_for(lambda: len(commands) == 3)
        assert "--deterministic" not in commands[-1]
        saved = client.put("/api/models/options", json={"id": "mini", "options": {"max_batch": 8}}).json()
        assert saved["options"]["max_batch"] == 8 and saved["saved"] is True
        assert settings.load_options("mini") == {"max_batch": 8}
        listed = client.get("/api/models/options", params={"id": "mini"}).json()
        assert listed["defaults"]["max_batch"] == 64 and {m["mode"] for m in listed["modes"]} >= {"eager", "tpu"}


def test_revision_is_validated_but_never_emitted_as_a_serve_flag():
    assert options.validate({"revision": "refs/pr/4"})["revision"] == "refs/pr/4"
    assert options.args({"revision": "abc123"}) == []
    with pytest.raises(ValueError, match="revision"):
        options.validate({"revision": "bad revision"})
    assert options.validate({"revision": ""})["revision"] is None  # a cleared field: the default branch
    assert options.validate({"revision": " abc "})["revision"] == "abc"
