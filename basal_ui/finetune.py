"""Private, cancellable Native fine-tuning jobs and their owned outputs."""

import json
import math
import os
import re
import stat
import tempfile
from contextlib import suppress
from pathlib import Path

from . import catalog, engine
from .jobs import run_tool

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?\Z")
REPORT_FIELDS = (
    "train_decisions",
    "heldout_lines",
    "scored",
    "skipped",
    "top1_before",
    "top1_after",
    "steps",
    "seconds",
)


def _name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value) or value in (".", ".."):
        raise ValueError("out_name must be a safe single path segment")
    return value


def _slug(model_id):
    if not isinstance(model_id, str) or not MODEL.fullmatch(model_id):
        raise ValueError("invalid model id")
    return model_id.replace("/", "--")


def _directory(record):
    root = record.get("root")
    if not isinstance(root, str) or not Path(root).is_absolute():
        raise ValueError("invalid fine-tune output root")
    return Path(root) / "adapters" / _slug(record.get("id")) / _name(record.get("name"))


def _safe_directory(record, missing=False):
    path = _directory(record)
    root = Path(record["root"])
    if root.resolve() != root:
        raise ValueError("fine-tune output root changed or contains a symlink")
    for part in (root / "adapters", root / "adapters" / _slug(record["id"]), path):
        if part.is_symlink():
            raise ValueError("fine-tune output contains a symlink")
        if part.exists() and not part.is_dir():
            raise ValueError("fine-tune output path is not a directory")
    if path.exists():
        stat = path.stat(follow_symlinks=False)
        if (stat.st_dev, stat.st_ino) != (record.get("device"), record.get("inode")):
            raise ValueError("fine-tune output ownership no longer matches")
    if not missing and not path.is_dir():
        raise ValueError("fine-tune output is missing")
    return path


def _parent_fd(record, create=False):
    if not _private_directories_supported():
        raise RuntimeError("private fine-tune output creation is unavailable on this platform")
    root = Path(record["root"])
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    before = root.lstat()
    if not stat.S_ISDIR(before.st_mode):
        raise ValueError("models directory is not a directory")
    fd = os.open(root, flags)
    try:
        info = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("models directory changed")
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError("models directory must be owned by the current user and not writable by others")
        for name in ("adapters", _slug(record["id"])):
            if create:
                with suppress(FileExistsError):
                    os.mkdir(name, 0o700, dir_fd=fd)
            before = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise ValueError("fine-tune path is not a directory")
            child = os.open(name, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError("fine-tune directory changed")
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise ValueError("fine-tune directories must be owned by the current user and not writable by others")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _private_directories_supported():
    return (
        hasattr(os, "geteuid")
        and all(fn in os.supports_dir_fd for fn in (os.open, os.mkdir, os.stat, os.rmdir, os.unlink, os.rename))
        and os.stat in os.supports_follow_symlinks
        and os.scandir in os.supports_fd
    )


def _owned_stat(fd, name, record):
    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (record.get("device"), record.get("inode")):
        raise ValueError("fine-tune output ownership no longer matches")
    return info


def _removed_bytes(fd, name):
    total = 0
    for _, _, files, directory in os.fwalk(name, dir_fd=fd, follow_symlinks=False):
        for file in files:
            info = os.stat(file, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def _remove_contents(fd):
    for entry in os.scandir(fd):
        info = entry.stat(follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            child = os.open(entry.name, flags, dir_fd=fd)
            try:
                opened = os.fstat(child)
                if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                    raise ValueError("fine-tune directory changed during deletion")
                _remove_contents(child)
                current = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                    raise ValueError("fine-tune directory changed during deletion")
                os.rmdir(entry.name, dir_fd=fd)
            finally:
                os.close(child)
        else:
            os.unlink(entry.name, dir_fd=fd)


def _report(path):
    if path.is_symlink():
        raise ValueError("fine-tune report is a symlink")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or any(key not in data for key in REPORT_FIELDS):
        raise ValueError("fine-tune report is incomplete")
    for key in ("train_decisions", "heldout_lines", "scored", "steps"):
        if type(data[key]) is not int or data[key] < 0:
            raise ValueError(f"invalid fine-tune report field {key}")
    if not data["train_decisions"] or not data["heldout_lines"] or not data["steps"]:
        raise ValueError("fine-tune report has no completed training")
    for key in ("top1_before", "top1_after"):
        value = data[key]
        if value is not None and (
            type(value) not in (int, float)
            or (isinstance(value, float) and not math.isfinite(value))
            or not 0 <= value <= 1
        ):
            raise ValueError(f"invalid fine-tune report field {key}")
        if (data["scored"] == 0) != (value is None):
            raise ValueError(f"inconsistent fine-tune report field {key}")
    seconds = data["seconds"]
    if type(seconds) not in (int, float) or (isinstance(seconds, float) and not math.isfinite(seconds)) or seconds < 0:
        raise ValueError("invalid fine-tune report field seconds")
    if not isinstance(data["skipped"], dict) or any(
        not isinstance(k, str) or type(v) is not int or v < 0 for k, v in data["skipped"].items()
    ):
        raise ValueError("invalid fine-tune report field skipped")
    return {key: data[key] for key in REPORT_FIELDS}


def _adapter(path):
    adapter = path / "lora"
    if adapter.is_symlink() or not adapter.is_dir() or not (adapter / "adapter_config.json").is_file():
        raise ValueError("adapter is missing")
    if any(p.is_symlink() for p in adapter.iterdir()):
        raise ValueError("adapter contains a symlink")
    if not any(p.is_file() and not p.is_symlink() and p.stat().st_size for p in adapter.glob("adapter_model.*")):
        raise ValueError("adapter weights are missing")
    from basal.adapter import validate

    try:
        validate(str(adapter))
    except SystemExit as exc:
        raise ValueError(str(exc)) from exc
    return str(adapter)


def _complete(path):
    for name in ("basal.json", "config.json", "heldout.jsonl"):
        file = path / name
        if file.is_symlink() or not file.is_file() or not file.stat().st_size:
            raise ValueError(f"fine-tune output is missing {name}")
    if not any(
        (path / name).is_file() and not (path / name).is_symlink() for name in ("tokenizer.json", "tokenizer.model")
    ):
        raise ValueError("fine-tune output is missing its tokenizer")
    index = path / "model.safetensors.index.json"
    if index.is_symlink():
        raise ValueError("fine-tune weights index is a symlink")
    if index.is_file():
        data = json.loads(index.read_text(encoding="utf-8"))
        mapping = data.get("weight_map") if isinstance(data, dict) else None
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError("fine-tune weights index is invalid")
        names = set(mapping.values())
        if any(
            not isinstance(name, str) or Path(name).name != name or not name.endswith(".safetensors") for name in names
        ):
            raise ValueError("fine-tune weights index is invalid")
        weights = [path / name for name in names]
    else:
        weights = list(path.glob("*.safetensors"))
    if not weights or any(p.is_symlink() or not p.is_file() or not p.stat().st_size for p in weights):
        raise ValueError("fine-tune merged weights are missing")


def _parameters(body):
    defaults = {"epochs": 2.0, "lr": 1e-4, "rank": 16, "alpha": 32, "holdout": 0.25, "max_len": 2048}
    out = {}
    for key, default in defaults.items():
        value = body.get(key, default)
        integer = key in ("rank", "alpha", "max_len")
        if (type(value) is not int if integer else type(value) not in (int, float)) or (
            isinstance(value, float) and not math.isfinite(value)
        ):
            raise ValueError(f"{key} has an invalid value")
        if value <= 0 or (key == "holdout" and value >= 1):
            raise ValueError(f"{key} has an invalid value")
        out[key] = value
    if "max_steps" in body:
        value = body["max_steps"]
        if type(value) is not int or value <= 0:
            raise ValueError("max_steps has an invalid value")
        out["max_steps"] = value
    value = body.get("grad_checkpointing", False)
    if type(value) is not bool:
        raise ValueError("grad_checkpointing must be a boolean")
    out["grad_checkpointing"] = value
    return out


def _snapshot(source, holdout):
    if not isinstance(source, str) or not source.strip():
        raise ValueError("input must be a labelled JSONL path")
    try:
        text = Path(source).expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read labelled input: {exc}") from exc
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip():
            try:

                def finite(value):
                    number = float(value)
                    if not math.isfinite(number):
                        raise ValueError("non-finite number")
                    return number

                json.loads(
                    line, parse_float=finite, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value))
                )
            except ValueError as exc:
                raise ValueError(f"line {number}: invalid JSON ({exc})") from exc
    temp = tempfile.TemporaryDirectory(prefix="basal-ui-finetune-")
    try:
        path = Path(temp.name) / "labelled.jsonl"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
        from basal import calibrate, finetune

        try:
            _, _, _, tasks = calibrate.read(str(path))
            train, held, skipped = finetune.split(str(path), holdout)
        except calibrate.InputError as exc:
            raise ValueError(str(exc)) from exc
        if not train or not held:
            raise ValueError(f"need training decisions and held-out groups; got {len(train)} and {len(held)}")
        return (
            temp,
            path,
            {
                "training_candidates": len(train),
                "heldout_lines": len(held),
                "skipped_training": skipped,
                "skipped_task_decisions": len(tasks),
            },
        )
    except BaseException:
        temp.cleanup()
        raise


class FineTune:
    def __init__(self, fleet, settings, hardware, addons):
        self.fleet, self.settings, self.hardware, self.addons = fleet, settings, hardware, addons
        self.deleting = set()

    def availability(self):
        if not engine.feature("finetune"):
            return False, "finetune_engine", "The installed Engine does not provide fine-tuning"
        if not _private_directories_supported():
            return False, "finetune_platform", "Private fine-tune output creation is unavailable on this platform"
        job = self.fleet.jobs.get("engine-addon")
        state = job.state() if job else None
        result = state.get("result") if state else None
        if isinstance(result, dict) and result.get("extra") == "finetune" and result.get("restart_required"):
            return False, "finetune_restart", "Restart basal-ui to use the installed fine-tuning add-on"
        inventory = self.addons.inventory()
        extra = next((x for x in inventory.get("extras", []) if x.get("id") == "finetune"), None)
        if not extra or not extra.get("installed"):
            return False, "finetune_addon", "Install the fine-tuning Engine add-on in Backends and restart basal-ui"
        return True, None, None

    def results(self, model_id):
        out = []
        for record in self.settings.finetune_results():
            if record.get("id") != model_id:
                continue
            try:
                path = _safe_directory(record)
                row = {
                    "name": _name(record.get("name")),
                    "output": str(path),
                    "base": record.get("base"),
                    "adapter": None,
                    "ready": False,
                }
                if record.get("state") == "ready":
                    _complete(path)
                    row.update(_report(path / "finetune.json"))
                    row["adapter"] = _adapter(path)
                    row["ready"] = True
                else:
                    row["error"] = "Fine-tune output is incomplete"
            except (OSError, ValueError, TypeError, ImportError) as exc:
                try:
                    row = {
                        "name": _name(record.get("name")),
                        "output": str(_directory(record)),
                        "adapter": None,
                        "ready": False,
                        "error": str(exc),
                    }
                except ValueError:
                    continue
            out.append(row)
        return out

    def check_adapter(self, model_id, local_dir, adapter):
        if not adapter:
            return
        selected = Path(adapter).expanduser().resolve()
        for record in self.settings.finetune_results():
            try:
                output = _directory(record).resolve()
            except (ValueError, OSError):
                continue
            if selected == output or output in selected.parents:
                if (
                    record.get("id") != model_id
                    or not local_dir
                    or record.get("base") != str(Path(local_dir).resolve())
                ):
                    raise ValueError("fine-tune adapter belongs to a different base model or revision")
                if not any(row["ready"] and row["adapter"] == str(selected) for row in self.results(model_id)):
                    raise ValueError("fine-tune adapter is not ready")
                return

    def start(self, variant, local_dir, body):
        available, _, reason = self.availability()
        if not available:
            raise ValueError(reason)
        if variant.port != "bf16" or not (variant.family.startswith("basal-") or variant.family == "attached"):
            raise ValueError("fine-tuning requires a downloaded bf16 basal or Attached model")
        name, params = _name(body.get("out_name")), _parameters(body)
        local_dir = Path(local_dir).resolve(strict=True)
        if not (local_dir / "basal.json").is_file():
            raise ValueError("model has no basal.json")
        assessment = catalog.assess(variant, self.hardware())
        needed = assessment.est_memory_bytes
        if type(needed) is not int or needed <= 0:
            raise ValueError("model memory estimate is unavailable")
        temp, input_path, summary = _snapshot(body.get("input"), params["holdout"])
        record = {
            "id": variant.id,
            "name": name,
            "root": str(Path(self.settings.models_dir()).resolve()),
            "base": str(local_dir),
            "state": "incomplete",
        }
        output = None
        reserved = False
        try:
            with self.fleet.lock:
                self.fleet._check_addon_maintenance()
                if self.fleet.stopping:
                    raise RuntimeError("the app is shutting down")
                if Path(self.settings.models_dir()).resolve() != Path(record["root"]):
                    raise RuntimeError("models directory changed before training")
                if (variant.id, name) in self.deleting:
                    raise RuntimeError("fine-tune output is being deleted")
                revision = self.settings.load_options(variant.id).get("revision")
                current = catalog.is_downloaded(variant, self.settings.models_dir(), revision=revision)
                if current is None or current.resolve() != local_dir:
                    raise RuntimeError("downloaded model changed before training")
                if any(r.get("id") == variant.id and r.get("name") == name for r in self.settings.finetune_results()):
                    raise RuntimeError("fine-tune output already exists")
                self.fleet.reserve("finetune", 2 * needed)
                reserved = True
                output = _directory(record)
                if output.exists() or output.is_symlink():
                    raise RuntimeError("fine-tune output already exists")
                _safe_directory(record, missing=True)
                if output == local_dir or local_dir in output.parents:
                    raise ValueError("fine-tune output cannot be inside the base model")
                parent_fd = _parent_fd(record, create=True)
                try:
                    os.mkdir(name, 0o700, dir_fd=parent_fd)
                    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    record.update(device=info.st_dev, inode=info.st_ino)
                    try:
                        _safe_directory(record)
                        self.settings.put_finetune(record)
                    except BaseException:
                        os.rmdir(name, dir_fd=parent_fd)
                        raise
                finally:
                    os.close(parent_fd)
                try:
                    state = self.fleet.jobs.start(
                        "finetune",
                        variant.id,
                        f"Fine-tune {variant.id}",
                        self._run,
                        record,
                        local_dir,
                        input_path,
                        params,
                        temp,
                        summary,
                    )
                except BaseException:
                    parent_fd = _parent_fd(record)
                    try:
                        _owned_stat(parent_fd, name, record)
                        os.rmdir(name, dir_fd=parent_fd)
                    finally:
                        os.close(parent_fd)
                    self.settings.remove_finetune(variant.id, name)
                    raise
                reserved = False
                return {**state, "validation": summary}
        except BaseException:
            if reserved:
                self.fleet.release("finetune")
            temp.cleanup()
            raise

    def _run(self, job, record, local_dir, input_path, params, temp, summary):
        try:
            job.check()
            output = _safe_directory(record)
            device = {"apple": "mps", "cuda": "cuda"}.get(self.hardware().platform, "cpu")
            argv = [str(input_path), "--model", str(local_dir), "--out", str(output)]
            for key in ("epochs", "lr", "rank", "alpha", "holdout", "max_len", "max_steps"):
                if key in params:
                    argv.extend(("--" + key.replace("_", "-"), str(params[key])))
            if params["grad_checkpointing"]:
                argv.append("--grad-checkpointing")
            argv.extend(("--device", device))
            job.log.append(f"Validated input: {summary}")
            environment = os.environ.copy()
            environment.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
            run_tool(job, "finetune", argv, env=environment)
            job.check()
            report = _report(output / "finetune.json")
            adapter = _adapter(output)
            _complete(output)
            with self.fleet.lock:
                job.check()
                self.settings.put_finetune({**record, "state": "ready"})
            return {"name": record["name"], "output": str(output), "adapter": adapter, **report}
        finally:
            try:
                temp.cleanup()
            finally:
                self.fleet.release("finetune")

    def delete(self, model_id, name):
        _slug(model_id)
        _name(name)
        with self.fleet.lock:
            self.fleet._check_addon_maintenance()
            record = next(
                (r for r in self.settings.finetune_results() if r.get("id") == model_id and r.get("name") == name), None
            )
            if record is None:
                raise ValueError("unknown fine-tune output")
            path = _safe_directory(record, missing=True)
            quarantine = path.with_name(f".{path.name}.deleting")
            if self.fleet.jobs.running() or self.fleet.reserved:
                raise RuntimeError("wait for active jobs before deleting fine-tune output")
            for runner in {
                *self.fleet.runners.values(),
                *self.fleet.temporary.values(),
                *self.fleet.retiring,
                *self.fleet.leases,
            }:
                for key in ("adapter", "think_adapter"):
                    value = runner.options.get(key)
                    if value:
                        resolved = Path(value).expanduser().resolve()
                        roots = (path.resolve(), quarantine.resolve())
                        if any(resolved == root or root in resolved.parents for root in roots):
                            raise RuntimeError("unload the active adapter before deleting its output")
            if quarantine.is_symlink():
                raise ValueError("fine-tune cleanup location is a symlink")
            if not path.exists() and not quarantine.exists():
                self.settings.remove_finetune(model_id, name)
                return {"removed_bytes": 0, "missing": True}
            if path.exists() and quarantine.exists():
                raise RuntimeError(f"cleanup location already exists: {quarantine}")
            if not path.exists() and not quarantine.is_dir():
                raise ValueError("fine-tune cleanup location is not a directory")
            parent_fd = _parent_fd(record)
            staged = False
            try:
                if path.exists():
                    _owned_stat(parent_fd, path.name, record)
                    os.rename(path.name, quarantine.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                    staged = True
                else:
                    _owned_stat(parent_fd, quarantine.name, record)
                total = _removed_bytes(parent_fd, quarantine.name)
            except BaseException as exc:
                if staged:
                    try:
                        os.rename(quarantine.name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                    except OSError as restore:
                        os.close(parent_fd)
                        raise RuntimeError(
                            f"cleanup retained at {quarantine}: {exc}; restore failed: {restore}"
                        ) from exc
                os.close(parent_fd)
                raise
            self.deleting.add((model_id, name))
            self.fleet.reserved["finetune-delete"] = 0
        try:
            try:
                _owned_stat(parent_fd, quarantine.name, record)
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
                output_fd = os.open(quarantine.name, flags, dir_fd=parent_fd)
                try:
                    opened = os.fstat(output_fd)
                    if (opened.st_dev, opened.st_ino) != (record["device"], record["inode"]):
                        raise ValueError("fine-tune output ownership no longer matches")
                    _remove_contents(output_fd)
                    _owned_stat(parent_fd, quarantine.name, record)
                    os.rmdir(quarantine.name, dir_fd=parent_fd)
                finally:
                    os.close(output_fd)
            except ValueError as exc:
                raise RuntimeError(f"cleanup retained at {quarantine}: {exc}") from exc
            except OSError as exc:
                try:
                    os.rename(quarantine.name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                except OSError as restore:
                    raise RuntimeError(f"cleanup incomplete at {quarantine}: {exc}; restore failed: {restore}") from exc
                raise RuntimeError(f"cleanup incomplete at {path}: {exc}") from exc
            self.settings.remove_finetune(model_id, name)
            return {"removed_bytes": total, "missing": False}
        finally:
            os.close(parent_fd)
            with self.fleet.lock:
                self.deleting.discard((model_id, name))
                self.fleet.reserved.pop("finetune-delete", None)
