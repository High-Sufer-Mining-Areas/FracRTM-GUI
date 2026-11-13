from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from fracrtm.messages import join_text
from fracrtm.messages import message as msg
from fracrtm.project import ProjectWorkspace
from fracrtm.runtime import RuntimeSettings


class MPIResourceError(ValueError):
    pass


@dataclass(frozen=True)
class PFLOTRANRunContext:
    project_wsl_root: str
    cwd: str
    model_path: str
    args: tuple[str, ...]
    requested_processes: int
    available_processors: int | None
    oversubscribe: bool


def probe_available_processors(settings: RuntimeSettings, timeout: float = 15.0) -> int:
    command = [
        "wsl.exe",
        "-d",
        settings.distribution,
        "-u",
        settings.wsl_user,
        "--",
        "/usr/bin/nproc",
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
    )
    if completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.strip() or msg("text.unable.to.read.the.available.wsl.processor.count")
        )
    try:
        value = int(completed.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(msg("runtime.invalid_nproc", reason=f"{completed.stdout!r}")) from exc
    if value < 1:
        raise RuntimeError(msg("runtime.invalid_count", value=value))
    return value


def validate_mpi_resources(
    requested_processes: int, available_processors: int, oversubscribe: bool = False
) -> None:
    if requested_processes < 1:
        raise ValueError(msg("validation.pflotran.mpi.process.count.must.be.at.least.1"))
    if available_processors < 1:
        raise ValueError(msg("validation.wsl.must.have.at.least.1.available.processor"))
    if requested_processes > available_processors and (not oversubscribe):
        raise MPIResourceError(
            msg(
                "runtime.oversubscribed",
                requested=requested_processes,
                available=available_processors,
            )
        )


def mpi_implementation(version_text):
    text = version_text.casefold()
    if "open mpi" in text or "openrte" in text:
        return "openmpi"
    if "hydra" in text or "mpich" in text:
        return "mpich"
    raise ValueError(msg("repair.mpi_unrecognized"))


def probe_mpi_launcher(settings):
    command = [
        "wsl.exe",
        "-d",
        settings.distribution,
        "-u",
        settings.wsl_user,
        "--",
        settings.mpi_launcher,
        "--version",
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15
    )
    version = result.stdout + result.stderr
    if result.returncode:
        raise RuntimeError(msg("repair.mpi_probe_failed", reason=version.strip()))
    return {"command": command, "version": version, "implementation": mpi_implementation(version)}


def build_pflotran_run_context(
    workspace: ProjectWorkspace,
    settings: RuntimeSettings,
    processes: int,
    *,
    available_processors: int | None = None,
    oversubscribe: bool = False,
    task=None,
    remote_root: str | None = None,
) -> PFLOTRANRunContext:
    if processes < 1:
        raise ValueError(msg("validation.pflotran.mpi.process.count.must.be.at.least.1"))
    if available_processors is not None:
        validate_mpi_resources(processes, available_processors, oversubscribe)
    if task is None or task.stage != "pflotran":
        raise ValueError(msg("text.pflotran.requires.a.frozen.identified.launch.task"))
    validate_pflotran_launch(task)
    root = remote_root or join_text("", [workspace.wsl_job_dir, "/runs/", task.run_id])
    if task.run_id not in root.split("/"):
        raise ValueError(msg("text.the.remote.execution.root.must.contain.the.exact.run.identity"))
    cwd = join_text("", [root, "/pflotran"])
    from fracrtm.project.runs import update_run

    mpi = probe_mpi_launcher(settings)
    update_run(task, mpi_launcher_identity=mpi)
    mpi_arguments: list[str] = []
    if settings.wsl_user == "root" and mpi["implementation"] == "openmpi":
        mpi_arguments.append("--allow-run-as-root")
    if (
        mpi["implementation"] == "openmpi"
        and available_processors is not None
        and processes > available_processors
        and oversubscribe
    ):
        mpi_arguments.append("--oversubscribe")
    args = (
        "-d",
        settings.distribution,
        "-u",
        settings.wsl_user,
        "--cd",
        cwd,
        "--",
        settings.mpi_launcher,
        *mpi_arguments,
        "-np",
        str(processes),
        settings.pflotran_executable,
        "-input_prefix",
        "model",
    )
    return PFLOTRANRunContext(
        root,
        cwd,
        join_text("", [cwd, "/model.in"]),
        args,
        processes,
        available_processors,
        oversubscribe,
    )


def validate_pflotran_launch(task):
    import json

    from fracrtm.config.pflotran import PFLOTRANConfig
    from fracrtm.pflotran.compiler import PFLOTRANCompiler
    from fracrtm.pflotran.spatial_validation import validate_spatial_resources
    from fracrtm.project.generation_records import file_digest
    from fracrtm.project.runs import run_matches_current

    record = json.loads(task.record_path.read_text(encoding="utf-8"))
    if not run_matches_current(task):
        raise ValueError(
            msg("text.current.configuration.resources.input.changed.after.launch.snapshot")
        )
    for item in record["input_files"]:
        if file_digest(task.snapshot_directory / item["path"]) != item["sha256"]:
            raise ValueError(msg("text.frozen.launch.resource.changed") + item["path"])
    config = PFLOTRANConfig.load_json(task.snapshot_directory / "pflotran/model_config.json")
    report = config.validate(task.snapshot_directory)
    if not report.valid:
        raise ValueError(join_text("\n", (issue.message for issue in report.errors)))
    validate_spatial_resources(task.snapshot_directory, config).raise_for_errors()
    expected = PFLOTRANCompiler().build(config, input_directory="pflotran")
    if (task.snapshot_directory / "pflotran/model.in").read_text(encoding="utf-8") != expected:
        raise ValueError(
            msg("text.frozen.model.in.is.not.the.input.compiled.from.the.frozen.configuration")
        )
    return config


def classify_run_failure(stdout: str, stderr: str, model_output: str = "") -> tuple[str, str]:
    combined = join_text("\n", (value for value in (stderr, stdout, model_output) if value))
    lowered = combined.casefold()
    mpi_markers = (
        "not enough slots",
        "available slots",
        "--oversubscribe",
        "orterun",
        "mpirun was unable",
        "failed to start process",
        "daemon failed",
    )
    title = (
        msg("text.mpi.launch.failed")
        if any((marker in lowered for marker in mpi_markers))
        else msg("text.pflotran.run.failed")
    )
    lines = combined.splitlines()
    marker = re.compile("\\b(?:PFLOTRAN ERROR|ERROR|FATAL|STOP)\\b", re.IGNORECASE)
    for index, line in enumerate(lines):
        if marker.search(line) or any((value in line.casefold() for value in mpi_markers)):
            start = max(0, index - 1)
            end = min(len(lines), index + 4)
            return (title, join_text("\n", (item for item in lines[start:end] if item.strip())))
    tail = [line for line in lines if line.strip()][-8:]
    return (
        title,
        join_text("\n", tail) or msg("text.the.external.process.did.not.provide.an.error.message"),
    )
