"""Batch inference via basal-run. Ordinary basal Batch bypasses History and Usage; Comparison Batch is recorded."""

import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .jobs import run_tool


def default_output(input_path) -> Path:
    path = Path(input_path)
    return path.with_name(f"{path.stem}.answers.jsonl")


@contextlib.contextmanager
def comparison_proxy(fleet, runner, model_id):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                host = urlsplit("http://" + self.headers.get("Host", ""))
                origin = urlsplit(self.headers.get("Origin", ""))
                port = server.server_port
                allowed = {"127.0.0.1", "localhost", "::1"}
                if (
                    host.hostname not in allowed
                    or host.username is not None
                    or host.password is not None
                    or host.path
                    or host.query
                    or host.fragment
                    or host.port not in (None, port)
                    or (
                        self.headers.get("Origin")
                        and (
                            origin.scheme != "http"
                            or origin.hostname not in allowed
                            or origin.port != port
                            or origin.username is not None
                            or origin.password is not None
                            or origin.path
                            or origin.query
                            or origin.fragment
                        )
                    )
                ):
                    self.send_error(403)
                    return
            except ValueError:
                self.send_error(403)
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                response = fleet.decide_runner(runner, model_id, body, source="batch", endpoint="/api/batch")
                status, content = response.status_code, response.content
            except Exception:  # noqa: BLE001 — basal-run records a safe error for this item
                status, content = 502, b'{"error":"Comparison server request failed","code":"UPSTREAM_ERROR"}'
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
        thread.join()


def run_batch(
    job,
    fleet,
    model_id,
    input_path,
    output_path,
    concurrency=16,
    early_exit=None,
    comparison_runner=None,
    snapshot_path=None,
) -> dict:
    """The batch job: basal-run in its own process (it writes the output file at the end); a cancel stops it."""
    try:
        runner = comparison_runner or fleet.ready(model_id)
        source = snapshot_path or input_path
        total = sum(1 for line in Path(source).read_text(encoding="utf-8").splitlines() if line.strip())
        job.progress("running", 0, total)
        with (
            comparison_proxy(fleet, runner, model_id) if comparison_runner else contextlib.nullcontext(runner.base_url)
        ) as base_url:
            args = [
                "--input",
                str(source),
                "--output",
                str(output_path),
                "--url",
                base_url + "/v1/systemone",
                "--concurrency",
                str(concurrency),
            ]
            if early_exit:
                args += ["--early-exit", str(early_exit)]
            summary = run_tool(job, "run", args)
        if summary is None:
            raise RuntimeError("basal-run printed no summary")
        job.progress("running", total, total)
        return {**summary, "input": str(input_path), "output": str(output_path), "model": model_id}
    finally:
        if snapshot_path is not None:
            Path(snapshot_path).unlink(missing_ok=True)


def rows(output_path, offset=0, limit=100) -> dict:
    """A page of a batch output file: {"rows", "total"}."""
    lines = [line for line in Path(output_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"rows": [json.loads(line) for line in lines[offset : offset + limit]], "total": len(lines)}
