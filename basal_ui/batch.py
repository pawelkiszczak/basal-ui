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

    def parse(line):
        try:
            return json.loads(line, parse_int=Number, parse_float=Number, parse_constant=Number)
        except json.JSONDecodeError:
            return None

    def metadata(item):
        questions = item.get("questions") if isinstance(item, dict) else None
        return (
            [question for question in questions.values() if isinstance(question, dict) and "_mcp_source" in question]
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
    total, changed = 0, False
    with Path(input_path).open(encoding="utf-8", newline="") as source:
        for line in source:
            job.check()
            total += bool(line.strip())
            if not changed:
                changed = bool(metadata(parse(line)))
    job.progress("running", 0, total)
    job.check()
    if not changed:
        summary = run_tool(job, "run", args)
    else:
        with tempfile.TemporaryDirectory(prefix="basal-ui-batch-") as directory:
            clean_input = Path(directory) / "input.jsonl"
            with (
                Path(input_path).open(encoding="utf-8", newline="") as source,
                clean_input.open("w", encoding="utf-8", newline="") as target,
            ):
                for line in source:
                    job.check()
                    item = parse(line)
                    questions = metadata(item)
                    if questions:
                        for question in questions:
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

    if summary is None:
        raise RuntimeError("basal-run printed no summary")
    job.progress("running", total, total)
    return {**summary, "input": str(input_path), "output": str(output_path), "model": model_id}


def rows(output_path, offset=0, limit=100) -> dict:
    """A page of a batch output file: {"rows", "total"}."""
    lines = [line for line in Path(output_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"rows": [json.loads(line) for line in lines[offset : offset + limit]], "total": len(lines)}
