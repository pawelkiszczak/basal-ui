"""Batch inference from basal-ui: basal-run over a JSONL file (simple items or full /v1/systemone requests) against a
loaded model's private basal-serve; the answers go to a JSONL file, one line per input line. The requests go straight
to the child: they are not counted in the statistics or the history (as benchmarks)."""

import json
import tempfile
from pathlib import Path

from .jobs import run_tool


def default_output(input_path) -> Path:
    path = Path(input_path)
    return path.with_name(f"{path.stem}.answers.jsonl")


def run_batch(job, fleet, model_id, input_path, output_path, concurrency=16, early_exit=None) -> dict:
    """The batch job: basal-run in its own process (it writes the output file at the end); a cancel stops it."""
    runner = fleet.ready(model_id)

    class Number(str):
        pass

    def encode(value):
        if isinstance(value, Number):
            return value
        if isinstance(value, list):
            return "[" + ",".join(encode(item) for item in value) + "]"
        if isinstance(value, dict):
            return "{" + ",".join(json.dumps(key) + ":" + encode(item) for key, item in value.items()) + "}"
        return json.dumps(value)

    args = [
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--url",
        runner.base_url + "/v1/systemone",
        "--concurrency",
        str(concurrency),
    ]
    if early_exit:
        args += ["--early-exit", str(early_exit)]
    with tempfile.TemporaryDirectory(prefix="basal-ui-batch-") as directory:
        clean_input = Path(directory) / "input.jsonl"
        total, changed = 0, False
        with (
            Path(input_path).open(encoding="utf-8", newline="") as source,
            clean_input.open("w", encoding="utf-8", newline="") as target,
        ):
            for line in source:
                total += bool(line.strip())
                try:
                    item = json.loads(line, parse_int=Number, parse_float=Number, parse_constant=Number)
                    questions = item.get("questions") if isinstance(item, dict) else None
                    if isinstance(questions, dict):
                        removed = False
                        for question in questions.values():
                            if isinstance(question, dict) and "_mcp_source" in question:
                                del question["_mcp_source"]
                                removed = True
                        if removed:
                            ending = (
                                "\r\n"
                                if line.endswith("\r\n")
                                else "\n"
                                if line.endswith("\n")
                                else "\r"
                                if line.endswith("\r")
                                else ""
                            )
                            line = encode(item) + ending
                            changed = True
                except (ValueError, TypeError, RecursionError):
                    pass
                target.write(line)
        job.progress("running", 0, total)
        if changed:
            args[1] = str(clean_input)
        summary = run_tool(job, "run", args)
    if summary is None:
        raise RuntimeError("basal-run printed no summary")
    job.progress("running", total, total)
    return {**summary, "input": str(input_path), "output": str(output_path), "model": model_id}


def rows(output_path, offset=0, limit=100) -> dict:
    """A page of a batch output file: {"rows", "total"}."""
    lines = [line for line in Path(output_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"rows": [json.loads(line) for line in lines[offset : offset + limit]], "total": len(lines)}
