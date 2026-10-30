"""basal-ui on fake hardware, a fixed catalog and a stand-in for basal-serve: for UI work, screenshots and
accessibility checks without models, a GPU or the Hub.

    uv run --no-sync python scripts/ui_fixture_server.py [--port 7861] [--preload | --empty]

The machine is an Apple Silicon Mac with 96 GiB; basal-2.0 mini and basal-omni-1.0 mini are on disk (none with
--empty), and --preload loads mini at start and sends a few Decisions so Overview, statistics and History have data.
A load runs basal-ui's real Runner: it starts a child process (this script with --child) in place of basal-serve and
reaches ready as soon as the child answers /health (a model not on disk "downloads" for about two seconds first).
Decisions get plausible, deterministic answers for every question type: probabilities, confidence, evidence spans,
calibration. Settings, history and stats live in a temporary directory; the user's real config is never read or
written. Benchmarks, calibration, inspection and backend tests run real Engine tools and are not faked.

Everything that reaches into basal-ui's internals is in one place, the adapter at the end (FakeDisk, child_command,
install, fixture_variants); tests/test_ui_a11y.py checks it against the real catalog and Runner.
"""

import argparse
import atexit
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # run as a script: basal_ui is imported from the checkout (only by the server, lazily)
    sys.path.insert(0, str(ROOT))

GB = 1_000_000_000
MINI, OMNI = "Remek/basal-2.0-mini", "Remek/basal-omni-1.0-mini"


# --- the stand-in basal-serve: its answers ---------------------------------------------------------------------------

CERTIFIED = {
    "0.01": {"choice": 0.9412, "noul": 0.9561, "score": 0.9105},
    "0.05": {"choice": 0.7718, "noul": 0.8124, "score": 0.7402},
}


def entry(name, media=(), mode="mlx"):
    """GET /v1/models: basal-serve's models_payload, plus the certified thresholds basal-ui adds."""
    out = {
        "name": name,
        "release_date": "2026-10-22",
        "description": "basal typed-decision model (fixture)",
        "mode": mode,
        "early_exit": [],
        "orders": 2,
        "certified": CERTIFIED,
        "temperature_per_prim": {"choice": 1.12, "noul": 1.05, "score": 1.31},
        "context": {"limit": 4096, "overflow": "truncate"},
    }
    return {**out, "media": list(media)} if media else out


class Rng:
    """Deterministic numbers from the request, so a repeated Decision gets the same answer."""

    def __init__(self, *parts):
        self.seed = hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).digest()
        self.i = 0

    def random(self):
        self.i += 1
        return int.from_bytes(hashlib.sha256(self.seed + self.i.to_bytes(8, "big")).digest()[:4], "big") / 2**32

    def probabilities(self, keys, peak=None):
        keys = [str(k) for k in keys] or ["0", "1"]
        weights = [0.05 + self.random() for _ in keys]
        weights[0 if peak is None else keys.index(peak)] += 2.5 + 3 * self.random()
        total = sum(weights)
        return {k: round(w / total, 4) for k, w in zip(keys, weights, strict=True)}


def option_keys(q):
    c = q.get("criteria") or q.get("options") or q.get("labels")
    if isinstance(c, dict):
        return list(c)
    if isinstance(c, list):
        return [str(x.get("name", i)) if isinstance(x, dict) else str(i) for i, x in enumerate(c)]
    return ["0", "1"]


def evidence(state, rng):
    """One sentence of the state as the supporting span."""
    sentences = [m.span() for m in re.finditer(r"[^.!?\n]+[.!?]?", state or "") if m.group().strip()]
    if not sentences:
        return []
    start, end = sentences[int(rng.random() * len(sentences))]
    while start < end and state[start].isspace():
        start += 1
    return [{"start": start, "end": end, "probability": round(0.6 + 0.35 * rng.random(), 4)}]


def calibrated(answer, prim, media):
    answer["calibration"] = "text" if media else "validated"
    return answer


def state_text(body):
    """The state as text: a structured (dict or list) state as its JSON."""
    state = body.get("state") or ""
    return state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)


def answer(name, q, body, model):
    rng, state, media = Rng(model, body.get("state"), name, q), state_text(body), bool(body.get("media"))
    kind = q.get("type", "choice")
    keys = option_keys(q)
    if kind in ("choice", "act"):
        probs = rng.probabilities(keys)
        top = max(probs, key=probs.get)
        out = {"type": kind, "choice": top, "answer": top, "probabilities": probs, "confidence": probs[top]}
        if kind == "act":
            wrong, defer = (q.get("costs") or {}).get("wrong", 10), (q.get("costs") or {}).get("defer", 1)
            costs = {k: round(wrong * (1 - p), 3) for k, p in probs.items()} | {"defer": defer}
            out |= {"expected_costs": costs, "action": min(costs, key=costs.get)}
            out["deferred"] = out["action"] == "defer"
    elif kind == "noul":
        p = round(0.15 + 0.8 * rng.random(), 4)
        out = {
            "type": kind,
            "noul": p,
            "probabilities": {"true": p, "false": round(1 - p, 4)},
            "confidence": round(max(p, 1 - p), 4),
        }
    elif kind == "score":
        levels = [str(k) for k in (q.get("levels") or [1, 2, 3, 4, 5])]
        probs = rng.probabilities(levels, peak=levels[int(rng.random() * len(levels))])
        score = sum(float(k) * p for k, p in probs.items() if k.replace(".", "").isdigit())
        out = {"type": kind, "score": round(score, 3), "probabilities": probs, "confidence": max(probs.values())}
    elif kind in ("multi", "classify") and (kind == "multi" or q.get("multi")):
        scores = {k: round(rng.random(), 4) for k in keys}
        chosen = [k for k, p in scores.items() if p >= float(q.get("threshold", 0.5))] or [max(scores, key=scores.get)]
        out = {
            "type": kind,
            "probabilities" if kind == "multi" else "scores": scores,
            "selected" if kind == "multi" else "labels": chosen,
            "set_confidence": round(0.55 + 0.4 * rng.random(), 4),
        }
        if kind == "multi":
            out |= {"objective": q.get("objective", "threshold"), "threshold": q.get("threshold", 0.5)}
    elif kind == "classify":
        scores = rng.probabilities(keys)
        top = max(scores, key=scores.get)
        out = {"type": kind, "label": top, "scores": scores, "confidence": scores[top]}
    elif kind == "tool":
        names = [
            t.get("name") or t.get("function", {}).get("name", f"tool{i}") for i, t in enumerate(q.get("tools", []))
        ]
        probs = rng.probabilities([*names, *(q.get("extra") or ["answer_directly"])])
        top = max(probs, key=probs.get)
        out = {
            "type": kind,
            "tool": top,
            "call": top in names,
            "probabilities": probs,
            "confidence": probs[top],
            "ranking": [{"tool": k, "probability": v} for k, v in sorted(probs.items(), key=lambda x: -x[1])],
        }
    elif kind == "verify":
        claims = q.get("claims") or []
        keys = list(claims) if isinstance(claims, dict) else [str(i) for i in range(len(claims))]
        verdicts = {}
        for k in keys:
            c = round(0.55 + 0.44 * rng.random(), 4)
            verdict = ("supported", "contradicted", "not_mentioned")[int(rng.random() * 3)]
            verdicts[k] = {"verdict": verdict, "confidence": c, "automated": {"0.05": c > 0.8}}
        out = {
            "type": kind,
            "claims": verdicts,
            "all_supported": all(v["verdict"] == "supported" for v in verdicts.values()),
            "not_supported": [k for k, v in verdicts.items() if v["verdict"] != "supported"],
        }
    elif kind == "compress":
        units = [{"start": s, "end": e} for s, e in (m.span() for m in re.finditer(r"[^.!?\n]+[.!?]?", state))]
        kept = [i for i in range(len(units)) if rng.random() > 0.4] or [0]
        text = " ".join(state[units[i]["start"] : units[i]["end"]].strip() for i in kept if units)
        out = {
            "type": kind,
            "unit": "sentence",
            "units": units,
            "kept": kept,
            "compressed": text,
            "compressed_chars": len(text),
            "original_chars": len(state),
            "ratio": round(len(text) / max(1, len(state)), 2),
        }
    elif kind == "extract":
        fields = {}
        for path in (q.get("schema") or {}).get("properties", {"value": {}}):
            span = evidence(state, rng)
            value = state[span[0]["start"] : span[0]["end"]][:40] if span else None
            c = round(0.6 + 0.39 * rng.random(), 4)
            fields[path] = {
                "value": value,
                "confidence": c,
                "source": "span",
                "automated": {"0.05": c > 0.85},
                **({"start": span[0]["start"], "end": span[0]["start"] + len(value)} if span else {}),
            }
        out = {"type": kind, "fields": fields, "data": {k: f["value"] for k, f in fields.items()}}
    else:
        probs = rng.probabilities(keys)
        out = {"type": kind, "probabilities": probs, "confidence": max(probs.values())}
    if kind in ("choice", "noul", "score", "act") and state and q.get("evidence"):  # as basal-serve: on request
        out["evidence"] = evidence(state, rng)
    return calibrated(out, kind, media) if kind in ("choice", "noul", "score", "act") else out


def decide(body, model):
    """The answers depend on the model too, so Compare shows two models apart."""
    questions = body.get("questions") or {}
    answers = {name: answer(name, q, body, model) for name, q in questions.items() if isinstance(q, dict)}
    tokens = 24 + len(state_text(body).split()) * 2
    usage = {
        "latency_ms": round(18 + 6 * len(answers) + Rng(body).random() * 9, 2),
        "input_tokens": tokens,
        "output_tokens": 0,
        "questions": len(answers),
        "branches": 2 * len(answers),
        "longest_prompt_tokens": tokens + 40,
    }
    if body.get("media"):
        kinds = [m.get("type") for m in body["media"]]
        usage["media"] = {
            "images": kinds.count("image"),
            "audio": kinds.count("audio"),
            "media_tokens": 256 * len(kinds),
        }
    time.sleep(usage["latency_ms"] / 1000)
    return {"answers": answers, "usage": usage}


class Child(ThreadingHTTPServer):
    """A loaded model's stand-in basal-serve on loopback (tests/conftest.py `child`, answering every route)."""

    daemon_threads = True

    def __init__(self, name, media=(), mode="mlx", port=0):
        self.entry = entry(name, media, mode)

        class Handler(BaseHTTPRequestHandler):
            def reply(self, data, status=200):
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == "/health":
                    return self.reply({"status": "ok", "running": 0, "queued": 0})
                if self.path == "/v1/models":
                    return self.reply({"models": [server.entry]})
                self.reply({"error": {"code": "NOT_FOUND", "message": self.path}}, 404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                self.reply(decide(body, name))

            def log_message(self, *args):
                pass

        server = self
        super().__init__(("127.0.0.1", port), Handler)
        self.url = f"http://127.0.0.1:{self.server_address[1]}"


def child_main(argv):
    """The stand-in basal-serve process a fixture load starts (the command child_command builds)."""
    parser = argparse.ArgumentParser(prog="ui_fixture_server.py --child")
    parser.add_argument("--name", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--mode", default="mlx")
    parser.add_argument("--media", default="", help="comma-separated media kinds (a load with --media on)")
    parser.add_argument("--parent", type=int, help="exit when this process (the fixture server) is gone")
    a = parser.parse_args(argv)
    server = Child(a.name, [k for k in a.media.split(",") if k], a.mode, a.port)
    if a.parent:  # Runner starts the child in its own session: it must not outlive a killed fixture server

        def watch():
            while os.getppid() == a.parent:
                time.sleep(1)
            os._exit(0)

        threading.Thread(target=watch, daemon=True).start()
    print(f"basal-serve (fixture) {a.name}: listening on {server.url}", flush=True)
    server.serve_forever()


def seed(fleet):
    """A few Decisions for statistics and History once mini is ready."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not any(m.get("state") == "ready" for m in fleet.status()["models"]):
        time.sleep(0.1)
    examples = ROOT / "basal_ui/examples/advanced.jsonl"
    for i, line in enumerate(examples.read_text(encoding="utf-8").splitlines()[:6]):
        request = {k: v for k, v in json.loads(line).items() if k != "id"}
        try:
            fleet.decide(MINI, {**request, "model": MINI}, source="api" if i % 2 else "playground")
        except Exception as exc:  # noqa: BLE001
            print(f"fixture: seeding decision failed: {exc}", file=sys.stderr)


# --- the adapter: the only code here that depends on basal-ui's internals -------------------------------------------
# tests/test_ui_a11y.py compares each hook with what it replaces and loads a model through the real Fleet and Runner, so
# a catalog or Runner refactor that the fixture no longer matches fails there, not silently in screenshots or a11y runs.

CATALOG_HOOKS = ("is_downloaded", "downloaded_bytes", "delete", "download")  # catalog functions FakeDisk replaces


class FakeDisk:
    """The models directory as the catalog sees it: the variants "on disk" have a fake snapshot (a model card and
    sparse weights of the variant's size) under root, and a download takes about two seconds."""

    def __init__(self, root):
        self.root, self.on_disk = Path(root), {}  # variant id -> its snapshot directory

    def snapshot(self, v):
        path = self.root / v.id.replace("/", "--").replace(":", "--")
        path.mkdir(parents=True, exist_ok=True)
        (path / "README.md").write_text(
            f"---\nlicense: apache-2.0\nbase_model: {v.repo}\n---\n# {v.repo}\n\nFixture model."
        )
        (path / "config.json").write_text(json.dumps({"model_type": "fixture"}))
        with open(path / (v.gguf_file or "model.safetensors"), "wb") as f:
            f.truncate(v.weights_bytes or 1)
        self.on_disk[v.id] = path
        return path

    def is_downloaded(self, v, cache_dir, revision=None):
        return self.on_disk.get(v.id)

    def downloaded_bytes(self, v, cache_dir):
        return v.weights_bytes if v.id in self.on_disk else None

    def delete(self, v, cache_dir):
        self.on_disk.pop(v.id, None)
        return v.weights_bytes or 0

    def download(self, v, cache_dir, tqdm_class=None, revision=None):
        total = v.weights_bytes or GB
        for step in range(1, 21):
            time.sleep(0.1)
            if tqdm_class is not None:
                tqdm_class.done = total * step // 20
        return str(self.snapshot(v))


def child_command(runner, v, path, assessment, media, port, serve):
    """Runner._build_command for the fixture: the real Runner._start (states, generation checks, health polling, the
    /v1/models read, the remembered load, termination) runs on this script's --child process instead of basal-serve."""
    media_kinds = ["--media", ",".join(v.media)] if media and v.media else []
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--child",
        "--name",
        v.id,
        "--port",
        str(port),
        "--mode",
        assessment.mode or "mlx",
        "--parent",
        str(os.getpid()),
        *media_kinds,
    ]


def install(disk):
    """Point the catalog's disk functions at disk and Runner's child command at child_command; -> a function that
    restores the originals."""
    from basal_ui import catalog
    from basal_ui.fleet import Runner

    saved = [(catalog, name, getattr(catalog, name)) for name in CATALOG_HOOKS]
    saved.append((Runner, "_build_command", Runner.__dict__["_build_command"]))
    for name in CATALOG_HOOKS:
        setattr(catalog, name, getattr(disk, name))
    Runner._build_command = child_command

    def restore():
        for owner, name, value in saved:
            setattr(owner, name, value)

    return restore


def fixture_variants():
    """The fixed catalog, built as the Hub listing builds it (catalog._variants: repository, file names and sizes)."""
    from basal_ui import catalog

    two_shards = {"model-00001-of-00002.safetensors": 5 * GB, "model-00002-of-00002.safetensors": 4 * GB}
    gguf = {
        "basal-2.0-mini-Q4_K_M.gguf": int(1.1 * GB),
        "basal-2.0-mini-Q8_0.gguf": int(1.9 * GB),
        "tokenizer.json": 1,
        "CALIBRATION.json": 1,
    }
    return [
        *catalog._variants(MINI, {"model.safetensors": int(3.2 * GB)}),
        *catalog._variants("Remek/basal-2.0-4.5B", two_shards),
        *catalog._variants("Remek/basal-2.0-4.5B-MLX", {"model.safetensors": int(4.8 * GB)}),
        *catalog._variants(OMNI, {"model.safetensors": int(10.2 * GB)}),
        *catalog._variants("Remek/basal-2.0-mini-GGUF", gguf),
    ]


def fixture_hardware():
    from basal_ui.hardware import Hardware

    return Hardware(
        "apple", "Apple M3 Max", 96 * 1024**3, 96 * 1024**3, None, frozenset({"mlx", "media", "gguf"}), 71 * GB
    )


def make_fleet(tmp, hw):
    """basal-ui's Fleet on the fake machine, with its statistics and history in tmp."""
    from basal_ui import settings
    from basal_ui.fleet import Fleet
    from basal_ui.history import History
    from basal_ui.stats import Stats

    return Fleet(
        models_dir_fn=settings.models_dir,
        platform_fn=lambda: hw.platform,
        settings_mod=settings,
        hardware_fn=lambda: hw,
        stats=Stats(Path(tmp) / "stats.json"),
        history=History(Path(tmp) / "history.jsonl"),
    )


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--child"]:
        return child_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=7861)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--preload", action="store_true", help="load basal-2.0 mini at start and send a few Decisions")
    group.add_argument("--empty", action="store_true", help="a first run: no model on disk or loaded")
    a = parser.parse_args(argv)
    tmp = Path(tempfile.mkdtemp(prefix="basal-ui-fixture-"))
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    os.environ["BASAL_UI_CONFIG"] = str(tmp / "settings.json")  # before basal_ui reads the config path
    os.environ.setdefault("BASAL_UI_ENGINE_UNCHECKED", "1")

    import uvicorn

    from basal_ui import settings
    from basal_ui.app import create_app

    disk, variants, hw = FakeDisk(tmp / "models"), fixture_variants(), fixture_hardware()
    install(disk)
    disk.root.mkdir()
    settings.set_models_dir(str(disk.root))
    if not a.empty:
        for v in variants:
            if v.id in (MINI, OMNI):
                disk.snapshot(v)
    if a.preload:
        settings.set_auto_load(True)
        settings.remember_loaded(MINI, False)
    fleet = make_fleet(tmp, hw)
    app = create_app(
        catalog_fn=lambda **_: (variants, "hub"),
        hardware_fn=lambda: hw,
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
    )
    if a.preload:
        threading.Thread(target=seed, args=(fleet,), daemon=True).start()
    print(f"basal-ui fixture: http://127.0.0.1:{a.port} (config {tmp})", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
