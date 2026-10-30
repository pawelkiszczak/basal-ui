"""Explicit, provenance-preserving installation of supported basal Engine extras."""

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from .jobs import Cancelled, terminate

ADDONS = {"media": "Media (image and audio)", "gguf": "GGUF (llama.cpp)"}
ADDON_MODULES = {"media": ("PIL", "scipy", "torchvision"), "gguf": ("llama_cpp",)}
ADDON_JOB = "engine-addon"
ADDON_MESSAGES = {
    "addon_missing_extra": "The installed Engine does not declare the {extra} extra",
    "addon_gguf_machine": "GGUF installation is unavailable on this machine",
    "addon_requirements_error": "Engine add-on requirements are not safe to install: {message}",
    "addon_no_requirements": "The installed Engine does not declare installable requirements for this extra",
    "addon_inventory_error": "Engine add-on inventory is unavailable: {message}",
}


def _engine_info():
    """Read installed Engine metadata without importing the unrelated PyPI basal package."""
    try:
        dist = importlib.metadata.distribution("basal")
        direct = json.loads(dist.read_text("direct_url.json") or "{}")
        metadata = dist.metadata
    except (importlib.metadata.PackageNotFoundError, ValueError, TypeError) as exc:
        raise RuntimeError("Cannot identify the installed rkinas/basal Engine source safely") from exc
    if not isinstance(direct, dict):
        raise RuntimeError("Engine direct_url.json is not a valid provenance object")
    files = {str(path) for path in dist.files or ()}
    if not any(path == "basal/server.py" or path.startswith("basal/server/") for path in files):
        raise RuntimeError("The installed basal distribution does not contain the Engine server")
    source = _recorded_source(direct)
    extras = {value.lower() for value in metadata.get_all("Provides-Extra", [])}
    return dist.version, source, extras


def _recorded_source(direct):
    """Describe the installed source for display and comparison; never use it to install Engine."""
    url = direct.get("url")
    if not isinstance(url, str):
        raise RuntimeError("Engine direct_url.json has no source URL")
    parsed = urlsplit(url)
    if parsed.scheme not in ("https", "file") or not parsed.path:
        raise RuntimeError("Engine source is not a supported HTTPS or local-file distribution")
    if parsed.username is not None or parsed.password is not None or parsed.query:
        raise RuntimeError("Engine source URL contains credentials or query parameters and cannot be displayed safely")
    source = url
    vcs = direct.get("vcs_info")
    if isinstance(vcs, dict) and vcs.get("vcs") == "git":
        commit = vcs.get("commit_id")
        source = (
            f"git+{url}@{commit}"
            if isinstance(commit, str) and len(commit) == 40
            else f"{url} (installed Git source; commit unavailable)"
        )
    archive = direct.get("archive_info")
    digest = (archive or {}).get("hash")
    hashes = (archive or {}).get("hashes") or {}
    if not digest and hashes:
        algorithm = next((name for name in ("sha512", "sha384", "sha256") if name in hashes), None)
        if algorithm:
            digest = f"{algorithm}={hashes[algorithm]}"
    if digest and archive:
        source = f"{url.split('#', 1)[0]}#{digest}"
    identity = hashlib.sha256(json.dumps(direct, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"{_redact(source)} [PEP 610 identity sha256:{identity}]"


def _engine_requirements(extra):
    """Return only the installed Engine's requirements selected by the supported extra marker."""
    if extra not in ADDONS:
        raise ValueError("unsupported Engine extra")
    try:
        distribution = importlib.metadata.distribution("basal")
        extras = {value.lower() for value in distribution.metadata.get_all("Provides-Extra", [])}
        if extra not in extras:
            raise RuntimeError(f"The installed Engine does not declare the {extra} extra")
        requirements = []
        for raw in distribution.requires or []:
            try:
                requirement = Requirement(raw)
            except InvalidRequirement as exc:
                raise RuntimeError("Installed Engine metadata has an invalid requirement") from exc
            try:
                selected = (
                    requirement.marker is not None
                    and requirement.marker.evaluate({"extra": extra})
                    and not requirement.marker.evaluate({"extra": ""})
                )
            except Exception as exc:
                raise RuntimeError("Installed Engine metadata has an unsupported requirement marker") from exc
            if not selected:
                continue
            if requirement.url or canonicalize_name(requirement.name) == "basal":
                raise RuntimeError(f"Unsafe direct Engine requirement: {requirement.name}")
            extras_suffix = f"[{','.join(sorted(requirement.extras))}]" if requirement.extras else ""
            requirements.append(f"{requirement.name}{extras_suffix}{requirement.specifier}")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError("Cannot read installed Engine requirements safely") from exc
    if not requirements:
        raise RuntimeError(f"The installed Engine declares no installable requirements for {extra}")
    return tuple(sorted(set(requirements)))


def _redact(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if parsed.port:
        host += f":{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))


def _environment():
    if sys.prefix == sys.base_prefix:
        raise RuntimeError("Engine add-ons can only be installed in an isolated virtual environment")
    uv = shutil.which("uv")
    if uv:
        return [uv]
    try:
        if importlib.util.find_spec("uv") is not None:
            return [sys.executable, "-m", "uv"]
    except (ImportError, ValueError):
        pass
    raise RuntimeError("uv is required to install Engine add-ons")


def _installed_constraints():
    lines = []
    for dist in importlib.metadata.distributions():
        name, version = dist.metadata.get("Name"), dist.version
        if name and version:
            lines.append(f"{canonicalize_name(name)}=={version}")
    return "\n".join(sorted(set(lines))) + "\n"


def _probe_extra(extra):
    """Check declared requirements and import the extra's feature modules in a fresh interpreter."""
    script = """
import importlib, importlib.metadata as metadata, json, sys
from packaging.requirements import Requirement

extra, modules = sys.argv[1], json.loads(sys.argv[2])
distribution = metadata.distribution("basal")
missing, import_errors = [], []
for raw in distribution.requires or []:
    requirement = Requirement(raw)
    if requirement.marker is not None and not requirement.marker.evaluate({"extra": extra}):
        continue
    try:
        version = metadata.version(requirement.name)
    except metadata.PackageNotFoundError:
        missing.append(requirement.name)
        continue
    if requirement.specifier and version not in requirement.specifier:
        missing.append(requirement.name)
for module in modules:
    try:
        importlib.import_module(module)
    except Exception as exc:
        import_errors.append(f"{module}: {type(exc).__name__}: {exc}")
print(json.dumps({"installed": not missing and not import_errors, "missing": missing, "imports": import_errors}))
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, extra, json.dumps(ADDON_MODULES[extra])],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode:
            return False, (result.stderr or result.stdout).strip()[-1000:] or "fresh-process probe failed"
        data = json.loads(result.stdout.splitlines()[-1])
        if not isinstance(data, dict):
            return False, "fresh-process probe returned invalid data"
        errors = data.get("imports") or data.get("missing") or []
        if not isinstance(errors, list) or any(not isinstance(error, str) for error in errors):
            return False, "fresh-process probe returned invalid diagnostics"
        installed = data.get("installed") is True
        return installed, None if installed else "; ".join(
            errors
        ) or "required package or feature module is unavailable"
    except subprocess.TimeoutExpired:
        return False, "fresh-process import probe timed out"
    except (OSError, ValueError, IndexError, TypeError, AttributeError) as exc:
        return False, f"fresh-process import probe failed: {exc}"


def _extra_installed(extra):
    return _probe_extra(extra)[0]


def _verify_install(extra, expected_version, expected_source):
    script = (
        "from basal_ui.addons import _engine_info, _probe_extra; import json, sys; "
        "version, source, extras = _engine_info(); "
        "ok, error = _probe_extra(sys.argv[1]); "
        "reason = None if version == sys.argv[2] and source == sys.argv[3] and "
        "sys.argv[1] in extras and ok else (error or 'Engine source or version changed'); "
        "print(json.dumps({'ok': reason is None, 'error': reason})); sys.exit(0 if reason is None else 1)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, extra, expected_version, expected_source],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode:
        try:
            error = json.loads(result.stdout.splitlines()[-1]).get("error")
        except (ValueError, IndexError):
            error = (result.stderr or "fresh-process verification failed").strip()[-500:]
        raise RuntimeError(f"Fresh-process verification failed: {error}; restart basal-ui and inspect the install log")


class AddonManager:
    """Inspect and install media and GGUF extras through the Fleet maintenance lock and Jobs."""

    def __init__(
        self,
        fleet,
        hardware_fn,
        engine_info_fn=_engine_info,
        uv_fn=_environment,
        requirements_fn=_engine_requirements,
    ):
        self.fleet, self.hardware_fn = fleet, hardware_fn
        self.engine_info_fn, self.uv_fn, self.requirements_fn = engine_info_fn, uv_fn, requirements_fn
        with fleet.lock:
            fleet._engine_addon_job_target = self._run

    def _inventory(self):
        version, source, extras = self.engine_info_fn()
        hw = self.hardware_fn()
        uv = None
        environment_error = None
        try:
            uv = self.uv_fn()
        except RuntimeError as exc:
            environment_error = str(exc)
        records = []
        for addon, name in ADDONS.items():
            requirements, requirements_error = (), None
            if addon in extras:
                try:
                    requirements = self.requirements_fn(addon)
                except RuntimeError as exc:
                    requirements_error = str(exc)
            compatible = (
                addon in extras
                and bool(requirements)
                and not requirements_error
                and (addon != "gguf" or hw.platform in ("apple", "cuda", "cpu"))
                and uv is not None
            )
            if addon not in extras:
                reason_code, reason_params = "addon_missing_extra", {"extra": addon}
            elif addon == "gguf" and hw.platform not in ("apple", "cuda", "cpu"):
                reason_code, reason_params = "addon_gguf_machine", {}
            elif requirements_error:
                reason_code, reason_params = "addon_requirements_error", {"message": requirements_error}
            elif not requirements:
                reason_code, reason_params = "addon_no_requirements", {}
            elif environment_error:
                reason_code, reason_params = "addon_inventory_error", {"message": environment_error}
            else:
                reason_code, reason_params = None, {}
            reason = ADDON_MESSAGES[reason_code].format(**reason_params) if reason_code else None
            installed = _extra_installed(addon) if addon in extras else False
            source_display = _redact(source)
            command = None
            if uv:
                command_args = [
                    *uv,
                    "pip",
                    "install",
                    "--python",
                    sys.executable,
                    "--constraint",
                    "<installed-package-constraints>",
                ]
                if addon == "gguf":
                    command_args.extend(
                        ["--no-binary", "llama-cpp-python", "--no-cache", "--reinstall-package", "llama-cpp-python"]
                    )
                command_args.extend(requirements)
                command = shlex.join(command_args)
            records.append(
                {
                    "id": addon,
                    "name": name,
                    "compatible": compatible,
                    "installed": installed,
                    "status": "installed" if installed else "available" if compatible else "unavailable",
                    "reason": reason,
                    "reason_code": reason_code,
                    "reason_params": reason_params,
                    "version": version,
                    "source": source_display,
                    "requirements": list(requirements),
                    "command": command,
                }
            )
        return {
            "environment": {"python": sys.executable, "prefix": sys.prefix, "error": environment_error},
            "extras": records,
        }

    def inventory(self):
        try:
            return self._inventory()
        except RuntimeError as exc:
            return {
                "environment": {"python": sys.executable, "prefix": sys.prefix},
                "extras": [],
                "error": ADDON_MESSAGES["addon_inventory_error"].format(message=str(exc)),
                "error_code": "addon_inventory_error",
                "error_params": {"message": str(exc)},
            }

    def _assert_idle(self):
        fleet = self.fleet
        if fleet.stopping:
            raise RuntimeError("The app is shutting down")
        if getattr(fleet, "engine_addon_installing", False):
            raise RuntimeError("An Engine add-on installation is already running")
        if (
            fleet.runners
            or fleet.temporary
            or fleet.downloads.any_alive()
            or fleet.inflight
            or fleet.reserved
            or fleet.retiring
            or fleet.leases
        ):
            raise RuntimeError(
                "Unload all models and wait for active requests, jobs, and downloads before installing an Engine add-on"
            )
        if fleet.jobs.running():
            raise RuntimeError("Wait for running jobs before installing an Engine add-on")
        fleet.engine_addon_installing = True

    def install(self, body):
        if not isinstance(body, dict) or not isinstance(body.get("extra"), str) or body["extra"] not in ADDONS:
            raise ValueError("extra must be media or gguf")
        if body.get("confirmed") is not True:
            raise ValueError("Explicit confirmation is required to install an Engine add-on")
        extra = body["extra"]
        inventory = self._inventory()
        selected = next(item for item in inventory["extras"] if item["id"] == extra)
        if not selected["compatible"]:
            raise RuntimeError(selected["reason"])
        if selected["installed"]:
            raise RuntimeError(f"The {extra} Engine extra is already installed; restart basal-ui to use it")
        uv = self.uv_fn()
        version, source, extras = self.engine_info_fn()
        if extra not in extras:
            raise RuntimeError(f"The installed Engine does not declare the {extra} extra")
        with self.fleet.lock:
            self._assert_idle()
            try:
                requirements = selected["requirements"]
                return self.fleet.jobs.start(
                    ADDON_JOB,
                    "basal",
                    f"Install {extra} Engine extra",
                    self._run,
                    extra,
                    uv,
                    source,
                    version,
                    requirements,
                )
            except BaseException:
                self.fleet.engine_addon_installing = False
                raise

    def _run(self, job, extra, uv, source, version, requirements):
        try:
            job.check()
            with tempfile.TemporaryDirectory(prefix="basal-ui-engine-addon-") as directory:
                constraints = Path(directory) / "constraints.txt"
                constraints.write_text(_installed_constraints(), encoding="utf-8")
                command = [
                    *uv,
                    "pip",
                    "install",
                    "--python",
                    sys.executable,
                    "--constraint",
                    str(constraints),
                ]
                if extra == "gguf":
                    command.extend(
                        ["--no-binary", "llama-cpp-python", "--no-cache", "--reinstall-package", "llama-cpp-python"]
                    )
                command.extend(requirements)
                environment = os.environ.copy()
                if extra == "gguf":
                    hw = self.hardware_fn()
                    environment.pop("CMAKE_ARGS", None)
                    if hw.platform == "apple":
                        environment["CMAKE_ARGS"] = "-DGGML_METAL=on"
                    elif hw.platform == "cuda" and "rocm" not in hw.engines:
                        environment["CMAKE_ARGS"] = "-DGGML_CUDA=on"
                job.log.append(f"$ {shlex.join(command)}")
                job.log.append(f"Installed Engine {version} source (not reinstalled): {_redact(source)}")
                job.log.append(
                    "All installed package versions are constrained, preserving the Engine and existing Torch stack."
                )
                if extra == "gguf":
                    job.log.append(
                        f"GGUF build acceleration: CMAKE_ARGS={environment.get('CMAKE_ARGS', '(CPU/default build)')}"
                    )
                options = (
                    {"start_new_session": True}
                    if os.name == "posix"
                    else {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
                )
                job.check()
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=environment,
                    **options,
                )
                finished = threading.Event()

                def stop_on_cancel():
                    while not finished.wait(0.1):
                        if job.cancel.is_set():
                            terminate(proc)
                            return

                threading.Thread(target=stop_on_cancel, name="basal-ui-engine-addon-cancel", daemon=True).start()
                try:
                    for line in proc.stdout:
                        job.log.append(line.rstrip("\n"))
                    proc.wait()
                finally:
                    finished.set()
                    if proc.poll() is None:
                        terminate(proc)
                if job.cancel.is_set():
                    raise Cancelled()
                if proc.returncode:
                    raise RuntimeError(f"uv exited with status {proc.returncode}")
                job.check()
                _verify_install(extra, version, source)
                job.check()
                job.log.append(
                    "Installation completed. Restart basal-ui, then refresh backend checks to verify the extra."
                )
                return {"restart_required": True, "extra": extra, "engine_version": version}
        finally:
            with self.fleet.lock:
                self.fleet.engine_addon_installing = False

    def cancel(self):
        return self.fleet.jobs.cancel(ADDON_JOB)

    def busy(self):
        job = self.fleet.jobs.get(ADDON_JOB)
        return bool(job and job.running)
