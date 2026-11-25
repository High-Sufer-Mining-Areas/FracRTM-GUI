from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path

from fracrtm.messages import exception_text, join_text
from fracrtm.messages import message as msg

from .generation_records import (
    atomic_json,
    capture_stage_identity,
    file_digest,
    file_manifest,
    safe_resource,
    utc_now,
)


@dataclass(frozen=True)
class TaskRun:
    project_directory: Path
    run_id: str
    stage: str
    directory: Path
    snapshot_directory: Path
    output_directory: Path
    record_path: Path

    def log_path(self, name):
        if name == "run.json":
            return self.record_path
        return (
            self.output_directory if self.record_path.name == "result.json" else self.directory
        ) / name


def get_run(project, run_id):
    root = Path(project).resolve()

    from .run_layout import published_records

    for path in published_records(root):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("run_id") == run_id:
            directory = safe_resource(root, path.parent.relative_to(root).as_posix())
            return TaskRun(
                root, run_id, record["stage"], directory, root, directory / "output", path
            )
    directory = safe_resource(root, ".fracrtm/tasks/" + str(run_id))
    record_path = directory / "run.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if record.get("run_id") != run_id:
        raise ValueError(msg("text.run.identity.mismatch"))
    snapshot = directory / "snapshot"
    output = snapshot / "pflotran/output" if record["stage"] == "pflotran" else directory / "output"
    return TaskRun(root, run_id, record["stage"], directory, snapshot, output, record_path)


def begin_task(project, stage, runtime=None, command=None, ncpu=None):
    from .storage import ProjectLock

    with ProjectLock(project):
        return _begin_task(project, stage, runtime, command, ncpu)


def _begin_task(project, stage, runtime=None, command=None, ncpu=None):
    from .workspace import ProjectWorkspace

    root = Path(project).resolve()
    workspace = ProjectWorkspace.open(root)
    stage_key = "pflotran_input" if stage == "pflotran" else stage
    identity = capture_stage_identity(root, stage_key)
    run_id = stage + "-" + uuid.uuid4().hex
    from .metadata import read_record, write_record

    directory = root / ".fracrtm/tasks" / run_id
    directory.mkdir(parents=True, exist_ok=False)
    snapshot = directory / "snapshot"
    snapshot.mkdir()
    paths = []
    for rel in (
        "dfn/dfn_config.json",
        "octree/continuum_config.json",
        "pflotran/model_config.json",
    ):
        if (root / rel).is_file():
            paths.append(rel)
    if stage in ("octree", "upscale"):
        paths.extend((item["path"] for item in identity["resources"]))
    if stage == "upscale":
        # Upscaling requires the existing octree work files.
        from .generation_records import _stage_products

        paths += _stage_products(root, "octree")
    if stage == "pflotran":
        paths += ["pflotran/model.in"]
        paths.extend((item["path"] for item in identity["resources"]))
        for rel in ("octree/octree_dfn.inp", "dfn/dfnGen_output/polygons.dat"):
            if (root / rel).is_file():
                paths.append(rel)
    manifest = file_manifest(root, paths)
    for item in manifest:
        destination = snapshot / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / item["path"], destination)
        if file_digest(destination) != item["sha256"]:
            raise ValueError(
                msg("diagnostic.runs.input.changed.while.freezing.run.value", value=item["path"])
            )
    if stage == "pflotran":
        atomic_json(
            snapshot / "pflotran/model_manifest.json",
            read_record(root, "pflotran/model_manifest.json"),
        )
        manifest.append(file_manifest(snapshot, ["pflotran/model_manifest.json"])[0])
    if identity["fingerprint"] != capture_stage_identity(root, stage_key)["fingerprint"]:
        raise ValueError(msg("text.scientific.inputs.changed.while.freezing.run"))
    output = snapshot / "pflotran/output" if stage == "pflotran" else directory / "output"
    output.mkdir(parents=True)
    record_path = directory / "run.json"
    task = TaskRun(root, run_id, stage, directory, snapshot, output, record_path)
    runtime = asdict(runtime) if is_dataclass(runtime) else dict(runtime or {})
    atomic_json(
        record_path,
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "project_id": workspace.project_id,
            "stage": stage,
            "status": "prepared",
            "created_at": utc_now(),
            "started_at": None,
            "finished_at": None,
            "command": list(command or ()),
            "runtime": runtime,
            "ncpu": ncpu,
            "launch_identity": identity,
            "input_files": manifest,
            "snapshot": "snapshot",
            "output": output.relative_to(directory).as_posix(),
            "stdout": "stdout.log",
            "stderr": "stderr.log",
            "returncode": None,
            "synchronized": False,
            "solver_status": "not_started",
            "visualization_status": "not_started",
            "failure_stage": "",
            "error": "",
        },
    )
    for name in ("stdout.log", "stderr.log"):
        (directory / name).touch()
    if stage == "pflotran":
        write_record(
            root, "pflotran/simulation_run.json", {"schema_version": "2.0", "run_id": run_id}
        )
    return task


def update_run(task, **fields):
    from .storage import ProjectLock

    with ProjectLock(task.project_directory):
        return _update_run(task, **fields)


def _update_run(task, **fields):
    record = json.loads(task.record_path.read_text(encoding="utf-8"))
    protected = {
        "run_id",
        "project_id",
        "stage",
        "launch_identity",
        "input_files",
        "snapshot",
        "output",
    }
    for key in ("execution_source", "execution_environment"):
        if key in record and key in fields and record[key] != fields[key]:
            raise ValueError(msg("text.launch.identity.and.input.snapshot.cannot.be.rewritten"))
    if protected.intersection(fields):
        raise ValueError(msg("text.launch.identity.and.input.snapshot.cannot.be.rewritten"))
    for key in fields:
        record.pop(key + "_data", None)
    record.update(fields)
    atomic_json(task.record_path, record)
    return record


def explicit_pflotran_output_error(path):
    from .completion import scan_log

    return scan_log(path)["error"]


def verify_run_files(task, *, include_outputs=True):
    record = json.loads(task.record_path.read_text(encoding="utf-8"))
    if record.get("result_schema") == "1.0":
        from .compact_project import verify_result

        return verify_result(task, include_outputs=include_outputs)
    issues = []
    source = record.get("execution_source")
    if source is not None:
        try:
            archive = safe_resource(task.directory, source["archive"])
            if file_digest(archive) != source["sha256"]:
                raise ValueError(msg("repair.execution_source_changed"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append(msg("repair.execution_source_changed") + ": " + str(exc))
    manifests = [("input_files", task.snapshot_directory)]
    if include_outputs:
        manifests.append(("output_files", task.directory))
    for name, base in manifests:
        rows = record.get(name)
        if not isinstance(rows, list) or not rows:
            issues.append(
                msg("diagnostic.runs.name.recorded.file.manifest.is.missing.or.empty", name=name)
            )
            continue
        seen = set()
        for item in rows:
            label = str(item.get("path", "")) if isinstance(item, dict) else repr(item)
            try:
                if not isinstance(item, dict):
                    raise ValueError(msg("text.invalid.manifest.entry"))
                path = safe_resource(base, item["path"])
                if name == "output_files":
                    path.relative_to(task.output_directory.resolve())
                if path in seen:
                    raise ValueError(msg("text.duplicate.manifest.entry"))
                seen.add(path)
                if path.stat().st_size != item["size"] or file_digest(path) != item["sha256"]:
                    raise ValueError(msg("text.recorded.size.or.sha.256.does.not.match"))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                issues.append(join_text("", [name, " ", label, ": ", exc]))
    return tuple(issues)


def finish_task(
    task,
    *,
    returncode,
    synchronized,
    failure_stage="",
    error="",
    outputs=None,
    visualization_status=None,
):
    record = json.loads(task.record_path.read_text(encoding="utf-8"))
    completion_status = "not_checked"
    evidence = {}
    if task.stage == "pflotran" and returncode == 0 and synchronized and not failure_stage:
        from .completion import completion_evidence, scan_log

        try:
            from fracrtm.config.time_units import run_calendar_year_seconds

            log = scan_log(
                task.output_directory / "model.out",
                calendar_year_seconds=run_calendar_year_seconds(task),
            )
            if log["error"]:
                failure_stage, error = "solver_output", log["error"]
                completion_status = "failed"
            else:
                completion_status, evidence, diagnostic = completion_evidence(task, log)
                if completion_status != "verified":
                    failure_stage = (
                        "completion_unverified"
                        if completion_status == "unverified"
                        else "scientific_completion"
                    )
                    error = diagnostic
        except (OSError, ValueError, TypeError, KeyError) as exc:
            completion_status = "unverified"
            failure_stage = "completion_unverified"
            error = msg("repair.completion_read_failed", reason=exc)
    manifest_errors = []
    files = []
    if outputs is not None:
        files = list(outputs)
    else:

        def walk_error(exc):
            manifest_errors.append(
                {"path": str(getattr(exc, "filename", "")), "error": exception_text(exc)}
            )

        for directory, _, names in os.walk(
            task.output_directory, followlinks=False, onerror=walk_error
        ):
            files.extend((Path(directory) / name for name in names))
    manifest = []
    for p in files:
        p = Path(p)
        if not p.is_absolute():
            p = task.output_directory / p
        try:
            manifest.append(
                {
                    "path": p.relative_to(task.directory).as_posix(),
                    "size": p.stat().st_size,
                    "sha256": file_digest(p),
                }
            )
        except (OSError, ValueError) as exc:
            manifest_errors.append({"path": str(p), "error": exception_text(exc)})
    if manifest_errors and synchronized:
        synchronized = False
        failure_stage = failure_stage or "output_manifest"
        error = error or msg(
            "text.one.or.more.returned.output.files.cannot.be.inspected.see.output.manifest"
        )
    terminal = (
        "cancelled"
        if failure_stage == "cancelled"
        else "unverified"
        if failure_stage == "completion_unverified"
        else "completed"
        if returncode == 0 and synchronized and (not failure_stage)
        else "failed"
    )
    fields = dict(
        status=terminal,
        completion_status=completion_status,
        completion_evidence=evidence,
        final_time_seconds=evidence.get("log", {}).get("last_time_seconds"),
        returncode=returncode,
        synchronized=bool(synchronized),
        finished_at=utc_now(),
        solver_status="cancelled"
        if terminal == "cancelled"
        else "succeeded"
        if returncode == 0 and failure_stage != "solver_output"
        else "failed",
        failure_stage=failure_stage,
        error=error,
        output_files=manifest,
        output_manifest_errors=manifest_errors,
    )
    if visualization_status is not None:
        fields["visualization_status"] = visualization_status
    from datetime import datetime

    start = record.get("started_at") or record.get("created_at")
    if start:
        fields["duration_seconds"] = (
            datetime.fromisoformat(fields["finished_at"]) - datetime.fromisoformat(start)
        ).total_seconds()
    result = update_run(task, **fields)
    # An older task must not replace the latest-task pointer.
    if task.stage == "pflotran":
        from .metadata import read_record, write_record

        if read_record(task.project_directory, "pflotran/simulation_run.json") is None:
            latest = list_runs(task.project_directory)
            if latest and latest[0].get("run_id") == task.run_id:
                write_record(
                    task.project_directory,
                    "pflotran/simulation_run.json",
                    {"schema_version": "2.0", "run_id": task.run_id},
                )
    return result


def run_display_status(record):
    status = record.get("status")
    if (
        record.get("stage") == "pflotran"
        and status == "completed"
        and record.get("completion_status") != "verified"
    ):
        return "unverified"
    return status


def list_runs(project, stage="pflotran"):
    rows = []

    from .run_layout import published_records

    root = Path(project)
    paths = list((root / ".fracrtm/tasks").glob("*/run.json"))
    paths += published_records(root)
    for path in paths:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            if stage is None or item.get("stage") == stage:
                rows.append(item)
        except (ValueError, OSError):
            continue
    # A completed publication may precede cleanup of its temporary task directory.
    unique = {row["run_id"]: row for row in rows}
    return sorted(unique.values(), key=lambda row: row.get("created_at", ""), reverse=True)


def run_matches_current(task):
    try:
        record = json.loads(task.record_path.read_text(encoding="utf-8"))
        stage = "pflotran_input" if task.stage == "pflotran" else task.stage
        match = (
            record["launch_identity"]["fingerprint"]
            == capture_stage_identity(task.project_directory, stage)["fingerprint"]
        )
        if task.stage == "pflotran":
            if record.get("result_schema") == "1.0":
                expected = next(
                    item["sha256"]
                    for item in record["input_files"]
                    if item["path"] == "pflotran/model.in"
                )
            else:
                expected = file_digest(task.snapshot_directory / "pflotran/model.in")
            match = match and file_digest(task.project_directory / "pflotran/model.in") == expected
        return match
    except (OSError, ValueError, KeyError, StopIteration):
        return False
