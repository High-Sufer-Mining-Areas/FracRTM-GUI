"""Portable completed results without permanent input or executable copies."""

from __future__ import annotations

import copy
import json
import re
import shutil
import uuid
from pathlib import Path

from fracrtm.messages import message as msg

from .generation_records import atomic_json, file_digest, safe_resource
from .run_layout import archive_root, published_records
from .storage import ProjectLock

INPUT_COPIES = {"model.in", "final_input.in", "model_config.json", "model_manifest.json"}


def manifest(root, paths):
    return [
        {"path": p.relative_to(root).as_posix(), "size": p.stat().st_size, "sha256": file_digest(p)}
        for p in sorted(paths)
        if p.is_file()
    ]


def verify_result(task, *, include_outputs=True):
    record = json.loads(task.record_path.read_text(encoding="utf-8"))
    errors = []
    inputs = record.get("input_files", [])
    seen = set()
    for item in inputs:
        try:
            safe_resource(task.project_directory, item["path"])
            if item["path"] in seen or not re.fullmatch("[0-9a-f]{64}", item["sha256"]):
                raise ValueError("Invalid input identity")
            seen.add(item["path"])
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(str(exc))
    if not inputs:
        errors.append("Input identity manifest is missing")
    if include_outputs:
        for key, folder in [("output_files", "output"), ("visualization_files", "visualization")]:
            rows = record.get(key, [])
            if key == "output_files" and not rows:
                errors.append("Output manifest is missing")
            seen = set()
            for item in rows:
                try:
                    path = safe_resource(task.directory, item["path"])
                    path.relative_to((task.directory / folder).resolve())
                    if path in seen:
                        raise ValueError("Duplicate output identity")
                    seen.add(path)
                    if path.stat().st_size != item["size"] or file_digest(path) != item["sha256"]:
                        raise ValueError(msg("text.recorded.size.or.sha.256.does.not.match"))
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    errors.append(f"{item}: {exc}")
    return tuple(errors)


def verify_display_sources(task, mapping):
    record = json.loads(task.record_path.read_text(encoding="utf-8"))
    if mapping.get("run_id") != task.run_id or not mapping.get("bijection_verified"):
        raise ValueError(msg("text.run.identity.mismatch"))
    hashes = {item["sha256"] for item in record["input_files"]}
    for name in ("uge", "octree", "config"):
        if mapping.get("sources", {}).get(name, {}).get("sha256") not in hashes:
            raise ValueError(msg("text.frozen.input.changed") + name)
    for relative, expected in mapping.get("raw_sources", {}).items():
        path = safe_resource(task.directory, relative)
        path.relative_to(task.output_directory.resolve())
        if file_digest(path) != expected["sha256"]:
            raise ValueError(msg("text.returned.output.checksum.mismatch") + relative)


def _convert_mapping(directory, run_id):
    path = directory / "visualization/visualization_mapping.json"
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8"))

    def raw_path(value):
        # Exported raw files are direct children of the PFLOTRAN output directory.
        name = Path(value).name
        if not (directory / "output" / name).is_file():
            raise FileNotFoundError(value)
        return "output/" + name

    data["raw_sources"] = {raw_path(k): v for k, v in data.get("raw_sources", {}).items()}
    for frame in data.get("frames", []):
        frame["raw_source"] = raw_path(frame["raw_source"])
    data.update(schema_version="3.0", run_id=run_id, input_retention="hashes_only")
    atomic_json(path, data)


def publish_result(task, project=None):
    """Publish a verified terminal result; never modify the source task."""
    from .runs import get_run, verify_run_files

    root = Path(project or task.project_directory).resolve()
    source_record = json.loads(task.record_path.read_text(encoding="utf-8"))
    if source_record.get("result_schema") == "1.0":
        return task
    if source_record.get("status") != "completed" or not source_record.get("synchronized"):
        raise ValueError(msg("text.task.did.not.complete"))
    issues = verify_run_files(task)
    if issues:
        raise ValueError("\n".join(map(str, issues)))
    with ProjectLock(root):
        result_root = archive_root(root, task.stage)
        result_root.mkdir(parents=True, exist_ok=True)
        for path in published_records(root):
            if json.loads(path.read_text(encoding="utf-8")).get("run_id") == task.run_id:
                return get_run(root, task.run_id)
        index = 1
        while (result_root / f"run-{index:03d}").exists():
            index += 1
        target = result_root / (f"run-{index:03d}" if task.stage == "pflotran" else task.run_id)
        staging = result_root / (".publishing-" + uuid.uuid4().hex)
        staging.mkdir()
        (staging / "output").mkdir()
        for path in task.output_directory.iterdir() if task.stage == "pflotran" else ():
            if path.name == "visualization" and path.is_dir():
                shutil.copytree(path, staging / "visualization")
            elif path.name not in INPUT_COPIES:
                if path.is_dir():
                    shutil.copytree(path, staging / "output" / path.name)
                else:
                    shutil.copy2(path, staging / "output" / path.name)
        for name in ("stdout.log", "stderr.log", "sync_commands.jsonl"):
            path = task.directory / name
            if path.is_file():
                shutil.copy2(path, staging / "output" / name)
        record = copy.deepcopy(source_record)
        if task.stage == "pflotran":
            config = json.loads(
                (task.snapshot_directory / "pflotran/model_config.json").read_text(encoding="utf-8")
            )
            record["simulation_time_unit"] = config.get("time", {}).get("final_unit", "s")
        record.update(
            result_schema="1.0",
            input_retention="hashes_only",
            output="output",
            stdout="output/stdout.log",
            stderr="output/stderr.log",
        )
        for key in ("snapshot", "runtime_supervisor", "visualization_collection"):
            record.pop(key, None)
        record["launch_identity"].pop("configuration", None)
        if "execution_source" in record:
            source = record["execution_source"]
            record["execution_source"] = {"sha256": source["sha256"], "retention": "identity_only"}
        execution = task.directory / "execution.json"
        if execution.is_file():
            record["execution"] = json.loads(execution.read_text(encoding="utf-8"))
        _convert_mapping(staging, task.run_id)
        record["output_files"] = manifest(staging, (staging / "output").rglob("*"))
        record["visualization_files"] = manifest(staging, (staging / "visualization").rglob("*"))
        record["omitted_input_copies"] = sorted(INPUT_COPIES)
        atomic_json(staging / "result.json", record)
        from .runs import TaskRun

        candidate = TaskRun(
            root,
            task.run_id,
            task.stage,
            staging,
            root,
            staging / "output",
            staging / "result.json",
        )
        issues = verify_result(candidate)
        if issues:
            raise ValueError("\n".join(issues))
        mapping = staging / "visualization/visualization_mapping.json"
        if mapping.is_file():
            verify_display_sources(candidate, json.loads(mapping.read_text(encoding="utf-8")))
        staging.rename(target)
        return get_run(root, task.run_id)


def discard_task_work(task):
    """Remove only the completed compact project's local execution workspace."""
    root = task.project_directory.resolve()
    task.directory.resolve().relative_to((root / ".fracrtm/tasks").resolve())
    if task.directory.is_symlink():
        raise ValueError(msg("repair.transaction_path"))
    shutil.rmtree(task.directory)
