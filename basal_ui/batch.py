"""Batch inference via basal-run. Native manual Batch bypasses History and Usage; scheduled requests are recorded."""

import contextlib
import io
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import combinations
from pathlib import Path
from urllib.parse import urlsplit

from . import paths
from .jobs import run_tool

PREFLIGHT_TIMEOUT_S = 5
DELIVERY_TIMEOUT_S = 5


def default_output(input_path) -> Path:
    path = Path(input_path)
    return path.with_name(f"{path.stem}.answers.jsonl")


@lru_cache(maxsize=1)
def capabilities():
    try:
        help_text = subprocess.run(
            [sys.executable, "-m", "basal.run", "--help"], capture_output=True, text=True, timeout=30, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        help_text = ""
    return {"restart": "--restart" in help_text, "stop_after": "--stop-after" in help_text}


def unfinished(output_path):
    part = Path(str(output_path) + ".part")
    return part.exists() or Path(str(part) + ".json").exists()


def validate_paths(source, output):
    part = Path(str(output) + ".part")
    artifacts = (part, Path(str(part) + ".json"), Path(str(part) + ".json.tmp"))
    for artifact in artifacts:
        try:
            info = artifact.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or info.st_nlink > 1:
            raise ValueError("Batch checkpoint files must not be symbolic links or hard links")
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Batch checkpoint files must be regular files")
    for left, right in combinations((source, output, *artifacts), 2):
        try:
            same = os.path.samefile(left, right)
        except OSError:
            same = False
        if same or Path(left).resolve() == Path(right).resolve():
            raise ValueError("input, output and Batch checkpoint files must be distinct")


def validate_comparison_item(item, runner, early_exit=None):
    """Apply the accepted Comparison Batch preflight before basal-run can discard fields."""
    from basal.run import to_request

    from .fleet import strip_mcp_metadata

    if "questions" in item and "gold" in item:
        return {
            "code": "UNSUPPORTED_FOR_COMPARISON",
            "detail": "basal-run does not grade gold on full requests; use simple items",
        }
    permitted = (
        {"id", "state", "questions", "early_exit", "gold"}
        if "questions" in item
        else {"id", "state", "question", "options", "type", "gold"}
    )
    extra = set(item) - permitted
    if extra:
        return {"code": "UNSUPPORTED_FOR_COMPARISON", "detail": f"basal-run would discard {sorted(extra)[0]}"}
    try:
        request = strip_mcp_metadata(to_request(item, early_exit))
    except (KeyError, TypeError, ValueError) as exc:
        return {"detail": str(exc)}
    refusal, _, _ = runner._prepare(request)
    if refusal is not None:
        error = refusal.json()
        return {"code": error["code"], "detail": error["error"]}
    return None


def validate_runner(runner):
    """Preserve the Cascade Batch admission contract for every Batch caller."""
    if (getattr(runner, "models_entry", None) or {}).get("kind") == "cascade":
        spec = getattr(runner, "spec", {})
        if spec.get("kind") != "escalate" or spec.get("fallback"):
            raise ValueError("Batch requires an escalation Cascade without fallback")


@contextlib.contextmanager
def comparison_proxy(fleet, runner, model_id, source="batch", endpoint="/api/batch"):
    """Forward Comparison or scheduled Decisions through the runner leased by the Batch job."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path != "/v1/models":
                self.send_error(404)
                return
            content = json.dumps(
                {"data": [{"id": model_id, "upstream_model": getattr(runner, "model_name", model_id)}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self):
            inferred = False
            try:
                host = urlsplit("http://" + self.headers.get("Host", ""))
                origin = urlsplit(self.headers.get("Origin", ""))
                allowed = {"127.0.0.1", "localhost", "::1"}
                if (
                    self.path != "/v1/systemone"
                    or host.hostname not in allowed
                    or host.username is not None
                    or host.password is not None
                    or host.path
                    or host.query
                    or host.fragment
                    or host.port not in (None, server.server_port)
                    or (
                        self.headers.get("Origin")
                        and (
                            origin.scheme != "http"
                            or origin.hostname not in allowed
                            or origin.port != server.server_port
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
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    self.send_error(400)
                    return
                body = json.loads(self.rfile.read(length))
                if not server.begin_inference(self.connection):
                    return
                inferred = True
                response = fleet.decide_runner(runner, model_id, body, source=source, endpoint=endpoint)
                status, content = response.status_code, response.content
            except Exception:  # noqa: BLE001 — basal-run records an error for this item
                status, content = 502, b'{"error":"Batch request failed","code":"UPSTREAM_ERROR"}'
            if inferred and not server.begin_delivery(self.connection):
                return
            with contextlib.suppress(OSError):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

    class Server(ThreadingHTTPServer):
        daemon_threads = False

        def __init__(self, *args):
            super().__init__(*args)
            self.pending_lock = threading.Lock()
            self.pending = {}
            self.delivering = set()
            self.closing = False

        def abort_pending(self, connection):
            with self.pending_lock:
                timer = self.pending.pop(connection, None)
                if timer is not None:
                    timer.cancel()
                    with contextlib.suppress(OSError):
                        connection.shutdown(socket.SHUT_RDWR)

        def process_request(self, connection, address):
            timer = threading.Timer(PREFLIGHT_TIMEOUT_S, self.abort_pending, args=(connection,))
            timer.daemon = True
            with self.pending_lock:
                self.pending[connection] = timer
            timer.start()
            super().process_request(connection, address)

        def begin_inference(self, connection):
            with self.pending_lock:
                if self.closing:
                    return False
                timer = self.pending.pop(connection, None)
                if timer is None:
                    return False
                timer.cancel()
                return True

        def finish_request(self, connection, address):
            try:
                super().finish_request(connection, address)
            finally:
                with self.pending_lock:
                    timer = self.pending.pop(connection, None)
                    if timer is not None:
                        timer.cancel()
                    self.delivering.discard(connection)

        def begin_delivery(self, connection):
            with self.pending_lock:
                if self.closing:
                    return False
                self.delivering.add(connection)
                connection.settimeout(DELIVERY_TIMEOUT_S)
                return True

        def close_pending(self):
            with self.pending_lock:
                self.closing = True
                connections = list(self.pending)
                delivering = list(self.delivering)
            for connection in connections:
                self.abort_pending(connection)
            for connection in delivering:
                with contextlib.suppress(OSError):
                    connection.shutdown(socket.SHUT_RDWR)

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name="basal-ui-scheduled-batch", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.close_pending()
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
    preset=None,
    lang="auto",
    choice=None,
    stop_after=16,
    restart_supported=False,
    runner=None,
    snapshot_path=None,
    *,
    source="batch",
) -> dict:
    """The batch job: basal-run in its own process (it writes the output file at the end); a cancel stops it."""
    try:
        runner = runner or fleet.ready(model_id)
        source_path = snapshot_path or input_path
        if restart_supported:
            validate_paths(input_path, output_path)
        if restart_supported and choice and not unfinished(output_path):
            raise RuntimeError("Unfinished Batch no longer exists; start again without a choice.")
        if restart_supported and unfinished(output_path) and choice not in ("resume", "restart"):
            raise RuntimeError("Unfinished Batch found; choose Resume or Restart before starting.")
        if restart_supported and choice == "resume" and not Path(str(output_path) + ".part").exists():
            raise RuntimeError("No partial output to resume; choose Restart to replace the unfinished record.")

        class Number(str):
            pass

        def parse(line):
            try:
                return json.loads(line, parse_int=Number, parse_float=Number, parse_constant=Number)
            except json.JSONDecodeError:
                return None

        def metadata(item):
            questions = item.get("questions") if isinstance(item, dict) else None
            return (
                [
                    question
                    for question in questions.values()
                    if isinstance(question, dict) and "_mcp_source" in question
                ]
                if isinstance(questions, dict)
                else []
            )

        def write_value(target, value):
            stack = [(False, value)]
            while stack:
                raw, item = stack.pop()
                if raw or isinstance(item, Number):
                    target.write(item)
                elif isinstance(item, (list, dict)):
                    entries = list(item.items()) if isinstance(item, dict) else list(enumerate(item))
                    target.write("{" if isinstance(item, dict) else "[")
                    stack.append((True, "}" if isinstance(item, dict) else "]"))
                    for index in range(len(entries) - 1, -1, -1):
                        key, child = entries[index]
                        if index < len(entries) - 1:
                            stack.append((True, ","))
                        stack.append((False, child))
                        if isinstance(item, dict):
                            stack.append((True, json.dumps(key) + ":"))
                else:
                    target.write(json.dumps(item))

        total, changed = 0, False
        with Path(source_path).open(encoding="utf-8", newline="") as stream:
            for line in stream:
                job.check()
                total += bool(line.strip())
                if not changed:
                    changed = bool(metadata(parse(line)))
        job.progress("running", 0, total)
        job.check()
        proxied = source == "schedule" or (getattr(runner, "models_entry", None) or {}).get("kind") == "comparison"
        with (
            comparison_proxy(
                fleet, runner, model_id, source, "/api/schedules" if source == "schedule" else "/api/batch"
            )
            if proxied
            else contextlib.nullcontext(runner.base_url)
        ) as base_url:
            args = [
                "--input",
                str(source_path),
                "--output",
                str(output_path),
                "--url",
                base_url + "/v1/systemone",
                "--concurrency",
                str(concurrency),
            ]
            if early_exit:
                args += ["--early-exit", str(early_exit)]
            if choice == "restart":
                args.append("--restart")
            if stop_after != 16:
                args += ["--stop-after", str(stop_after)]
            if preset:
                args += ["--preset", preset]
            if lang in ("pl", "en"):
                args += ["--lang", lang]
            try:
                if not changed and not preset:
                    summary = run_tool(job, "run", args)
                else:
                    if preset:
                        from basal.presets import questions as build_questions

                    with tempfile.TemporaryDirectory(prefix="basal-ui-batch-") as directory:
                        clean_input = Path(directory) / "input.jsonl"
                        with (
                            Path(source_path).open(encoding="utf-8", newline="") as stream,
                            clean_input.open("w", encoding="utf-8", newline="") as target,
                        ):
                            for line in stream:
                                job.check()
                                if preset and not line.strip():
                                    continue
                                item = parse(line)
                                stored_metadata = metadata(item)
                                expand = (
                                    preset
                                    and isinstance(item, dict)
                                    and "questions" not in item
                                    and "question" not in item
                                )
                                if expand:
                                    state = json.loads(line)["state"]
                                    item["questions"] = build_questions(preset, None if lang == "auto" else lang, state)
                                if stored_metadata or expand:
                                    for question in stored_metadata:
                                        del question["_mcp_source"]
                                    write_value(target, item)
                                    if line.endswith("\r\n"):
                                        target.write("\r\n")
                                    elif line.endswith(("\r", "\n")):
                                        target.write(line[-1])
                                else:
                                    target.write(line)
                        args[1] = str(clean_input)
                        job.check()
                        summary = run_tool(job, "run", args)

            except RuntimeError as exc:
                part = Path(str(output_path) + ".part")
                if restart_supported and part.exists():
                    from .jobs import Resumable

                    with part.open("rb") as checkpoint:
                        done = sum(1 for line in checkpoint if line.endswith(b"\n") and line.strip())
                    job.progress("stopped_resumable", done, total)
                    raise Resumable(
                        str(exc),
                        {
                            "id": model_id,
                            "input": str(input_path),
                            "output": str(output_path),
                            "concurrency": concurrency,
                            "early_exit": early_exit,
                            "stop_after": stop_after,
                            **({"preset": preset, "lang": lang} if preset else {}),
                        },
                    ) from exc
                raise
        if summary is None:
            raise RuntimeError("basal-run printed no summary")
        job.progress("running", total, total)
        return {**summary, "input": str(input_path), "output": str(output_path), "model": model_id}

    finally:
        if snapshot_path is not None:
            Path(snapshot_path).unlink(missing_ok=True)


def start_scheduled(fleet, settings_mod, schedule_stop, spec, source, output, signature, runner, row):
    snapshot = None
    try:
        try:
            validate_runner(runner)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        if output.resolve() == source.resolve():
            raise RuntimeError("output must not be the input file")
        fd, name = tempfile.mkstemp(prefix=".basal-ui-schedule-", suffix=".jsonl", dir=output.parent)
        snapshot = Path(name)
        with (
            os.fdopen(fd, "wb") as target,
            paths.open_input(source) as raw,
            io.TextIOWrapper(raw, encoding="utf-8", errors="surrogateescape", newline="") as stream,
        ):
            before = os.fstat(raw.fileno())
            if [before.st_size, before.st_mtime_ns] != signature:
                raise RuntimeError("input changed while preparing the Batch")
            first = True
            for number, line in enumerate(stream, 1):
                if schedule_stop.is_set():
                    return None
                try:
                    target.write(line.encode("utf-8"))
                except UnicodeEncodeError as exc:
                    raise RuntimeError(f"{source.name}: line {number}: invalid UTF-8") from exc
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except (ValueError, RecursionError):
                    item = None
                if not isinstance(item, dict) or not ("questions" in item or "options" in item):
                    raise RuntimeError(f"{source.name}: line {number} is not a Batch request")
                if (getattr(runner, "models_entry", None) or {}).get("kind") == "comparison":
                    error = validate_comparison_item(item, runner)
                    if error:
                        raise RuntimeError({**error, "detail": f"{source.name}: line {number}: {error['detail']}"})
                if first:
                    try:
                        _, reason = paths.row_shape(item)
                    except RecursionError as exc:
                        raise RuntimeError(
                            f"{source.name}: line {number}: request nesting exceeds the parser limit"
                        ) from exc
                    if reason:
                        raise RuntimeError(f"{source.name}: line {number}: {reason}")
                    first = False
            after = os.fstat(raw.fileno())
            if [after.st_size, after.st_mtime_ns] != signature:
                raise RuntimeError("input changed while preparing the Batch")
        if first:
            raise RuntimeError(f"{source.name}: no requests")

        def reserve():
            fleet.start_model_job(
                "batch",
                spec["model"],
                f"{spec['model']} on {source.name}",
                run_scheduled,
                fleet,
                spec["model"],
                source,
                snapshot,
                output,
                spec["concurrency"],
                runner,
                spec["overwrite"],
                settings_mod,
                spec,
                row,
                runner_expected=runner,
            )
            return fleet.jobs.get("batch")

        with fleet.lock:
            if schedule_stop.is_set():
                return None
            job = settings_mod.reserve_schedule(spec, reserve)
        if job is None:
            return None
        snapshot = None
        return job
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc
    finally:
        if snapshot is not None:
            snapshot.unlink(missing_ok=True)


def run_scheduled(
    job, fleet, model_id, source, snapshot, output, concurrency, runner, overwrite, settings_mod, spec, row
):
    """Run the validated snapshot and publish only a complete output."""
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix=".basal-ui-schedule-", suffix=".jsonl", dir=output.parent)
        os.close(fd)
        temporary = Path(name)
        result = run_batch(
            job,
            fleet,
            model_id,
            source,
            temporary,
            concurrency,
            runner=runner,
            snapshot_path=snapshot,
            source="schedule",
        )
        job.check()
        if not result.get("errors"):
            if output.resolve() == source.resolve():
                raise RuntimeError("output must not be the input file")
            settings_mod.begin_schedule_publication(
                spec, {**row, "at": job.started, "rows": result.get("items", 0), "errors": 0}, temporary
            )
            with job.lock:
                job.check()
                if overwrite:
                    os.replace(temporary, output)
                else:
                    os.link(temporary, output)
        else:
            result["warnings"] = ["one or more requests failed; output was not replaced"]
        return {**result, "input": str(source), "output": str(output)}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
            for suffix in (".part", ".part.json", ".part.json.tmp"):
                Path(str(temporary) + suffix).unlink(missing_ok=True)
        Path(snapshot).unlink(missing_ok=True)


def rows(output_path, offset=0, limit=100) -> dict:
    """A page of a batch output file: {"rows", "total"}."""
    lines = [line for line in Path(output_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"rows": [json.loads(line) for line in lines[offset : offset + limit]], "total": len(lines)}
