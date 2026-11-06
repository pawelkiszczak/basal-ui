"""Batch inference from basal-ui: basal-run over a JSONL file (simple items or full /v1/systemone requests) against a
loaded model's private basal-serve; the answers go to a JSONL file, one line per input line. Manual Batch requests go
straight to the child and are not counted in statistics or history. Scheduled requests use
the same proxy to record Decisions through Fleet. A Comparison model is reached
through a private loopback proxy for the job's lifetime: basal-run sends neither a model name nor an API key, and the
proxy adds both (and the Decisions translation of a hosted server) through the model's runner."""

import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .jobs import run_tool


def default_output(input_path) -> Path:
    path = Path(input_path)
    return path.with_name(f"{path.stem}.answers.jsonl")


@contextlib.contextmanager
def _batch_proxy(runner, fleet=None, model_id=None):
    """A loopback /v1/systemone forwarding to the runner or through Fleet for scheduled Decisions."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                response = (
                    fleet.decide(model_id, body, source="schedule", endpoint="/api/schedules", method="RUN")
                    if fleet
                    else runner.decide(body)
                )
                status, content = response.status_code, response.content
            except Exception as exc:  # noqa: BLE001 — basal-run records the line's error
                status, content = 502, json.dumps({"error": str(exc), "code": "UPSTREAM_ERROR"}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name="basal-ui-comparison-batch", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def run_batch(
    job,
    fleet,
    model_id,
    input_path,
    output_path,
    concurrency=16,
    early_exit=None,
    preset=None,
    lang="auto",
    source=None,
) -> dict:
    """The batch job: basal-run in its own process (it writes the output file at the end); a cancel stops it."""
    runner = fleet.ready(model_id)
    total = sum(1 for line in Path(input_path).read_text(encoding="utf-8").splitlines() if line.strip())
    job.progress("running", 0, total)
    comparison = (getattr(runner, "models_entry", None) or {}).get("kind") == "comparison"
    proxy = _batch_proxy(runner, fleet, model_id) if source == "schedule" else _batch_proxy(runner)
    with proxy if comparison or source == "schedule" else contextlib.nullcontext(runner.base_url) as base_url:
        args = [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--url",
            base_url + "/v1/systemone",
            "--concurrency",
            str(concurrency),
        ]
        if early_exit:
            args += ["--early-exit", str(early_exit)]
        if preset:
            args += ["--preset", preset]
        if lang in ("pl", "en"):
            args += ["--lang", lang]
        summary = run_tool(job, "run", args)  # basal-run prints its summary as the last line
    if summary is None:
        raise RuntimeError("basal-run printed no summary")
    job.progress("running", total, total)
    return {**summary, "input": str(input_path), "output": str(output_path), "model": model_id}


def rows(output_path, offset=0, limit=100) -> dict:
    """A page of a batch output file: {"rows", "total"}."""
    lines = [line for line in Path(output_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"rows": [json.loads(line) for line in lines[offset : offset + limit]], "total": len(lines)}
