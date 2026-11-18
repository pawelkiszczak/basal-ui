"""Batch inference from basal-ui: basal-run over a JSONL file (simple items or full /v1/systemone requests) against a
loaded model's private basal-serve; the answers go to a JSONL file, one line per input line. The requests go straight
to the child: they are not counted in the statistics or the history (as benchmarks)."""

import json
import os
import stat
import subprocess
import sys
import tempfile
from functools import lru_cache
from itertools import combinations
from pathlib import Path

from .jobs import run_tool


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
) -> dict:
    """The batch job: basal-run in its own process (it writes the output file at the end); a cancel stops it."""
    runner = fleet.ready(model_id)
    if restart_supported:
        validate_paths(input_path, output_path)
    if restart_supported and choice and not unfinished(output_path):
        raise RuntimeError("Unfinished Batch no longer exists; start again without a choice.")
    if restart_supported and unfinished(output_path) and choice not in ("resume", "restart"):
        raise RuntimeError("Unfinished Batch found; choose Resume or Restart before starting.")
    if restart_supported and choice == "resume" and not Path(str(output_path) + ".part").exists():
        raise RuntimeError("No partial output to resume; choose Restart to replace the unfinished record.")
    total = 0
    with Path(input_path).open(encoding="utf-8") as source:
        for line in source:
            job.check()
            total += bool(line.strip())
    job.progress("running", 0, total)
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
    if choice == "restart":
        args.append("--restart")
    if stop_after != 16:
        args += ["--stop-after", str(stop_after)]
    try:
        if preset:
            args += ["--preset", preset]
        if lang in ("pl", "en"):
            args += ["--lang", lang]
        if preset:
            from basal.presets import questions

            with tempfile.TemporaryDirectory(prefix="basal-ui-batch-") as directory:
                temporary = Path(directory) / "input.jsonl"
                with Path(input_path).open(encoding="utf-8") as source, temporary.open("w", encoding="utf-8") as target:
                    for line in source:
                        job.check()
                        if not line.strip():
                            continue
                        item = json.loads(line)
                        if "questions" not in item and "question" not in item:
                            item["questions"] = questions(preset, None if lang == "auto" else lang, item["state"])
                        target.write(json.dumps(item, ensure_ascii=False) + "\n")
                args[1] = str(temporary)
                job.check()
                summary = run_tool(job, "run", args)
        else:
            job.check()
            summary = run_tool(job, "run", args)  # basal-run prints its summary as the last line
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


def rows(output_path, offset=0, limit=100) -> dict:
    """A page of a batch output file: {"rows", "total"}."""
    lines = [line for line in Path(output_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"rows": [json.loads(line) for line in lines[offset : offset + limit]], "total": len(lines)}
