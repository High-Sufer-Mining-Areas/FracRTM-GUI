from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fracrtm.messages import exception_text, join_text, prepare_json
from fracrtm.messages import message as msg

from .generation_records import file_digest, utc_now
from .resources import UDFM_INPUT_FILENAMES
from .workspace import ProjectWorkspace


class WorkspaceSyncError(RuntimeError):
    pass


DFN_REQUIRED = (
    "dfn_config.json",
    "resolved_parameters.json",
    "fracrtm_run.json",
    *UDFM_INPUT_FILENAMES,
)
OCTREE_REQUIRED = ("octree_dfn.inp", "full_mesh.uge")
UPSCALE_REQUIRED = (
    "octree_dfn.inp",
    "full_mesh.uge",
    "mesh_permeability.h5",
    "mesh_porosity.h5",
    "materials.h5",
)


@dataclass(frozen=True)
class SyncResult:
    files: tuple[str, ...]
    warnings: tuple[str, ...] = ()


class ProjectSyncService:
    def __init__(
        self,
        workspace: ProjectWorkspace,
        distribution: str,
        wsl_user="root",
        warning: Callable[[str], None] | None = None,
        task=None,
    ):
        self.workspace = workspace
        self.distribution = distribution
        self.wsl_user = wsl_user
        self.warning = warning
        self.last_warnings = ()
        self.task = task
        self._warnings = []

    @property
    def _prefix(self):
        return ["wsl.exe", "-d", self.distribution, "-u", self.wsl_user, "--"]

    def _run(self, *arguments, allow_not_found=False):
        command = self._prefix + list(arguments)
        command_id = uuid.uuid4().hex
        started = utc_now()

        def log(data):
            if self.task is not None:
                with (self.task.directory / "sync_commands.jsonl").open(
                    "a", encoding="utf-8"
                ) as stream:
                    stream.write(
                        json.dumps(
                            prepare_json({"command_id": command_id, "command": command, **data}),
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

        log({"phase": "started", "started_at": started})
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )
        except Exception as exc:

            def decoded(value):
                return (
                    value.decode("utf-8", errors="replace")
                    if isinstance(value, bytes)
                    else value or ""
                )

            log(
                {
                    "phase": "failed",
                    "started_at": started,
                    "finished_at": utc_now(),
                    "returncode": None,
                    "error": exception_text(exc),
                    "stdout": decoded(getattr(exc, "stdout", "")),
                    "stderr": decoded(getattr(exc, "stderr", "")),
                }
            )
            raise WorkspaceSyncError(
                msg(
                    "diagnostic.sync_service.synchronization.command.could.not.complete.command.exc",
                    command=command,
                    exc=exc,
                )
            ) from exc
        log(
            {
                "phase": "finished",
                "started_at": started,
                "finished_at": utc_now(),
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        stderr = result.stderr.strip()
        if result.returncode != 0 and (not (allow_not_found and result.returncode == 1)):
            raise WorkspaceSyncError(stderr or msg("sync.command_failed", code=result.returncode))
        if result.returncode == 0 and stderr:
            self._warnings.append(stderr)
            if self.warning:
                self.warning(stderr)
        return result

    def _wslpath(self, path):
        return self.workspace.to_wsl_path(
            path, self.distribution, self.wsl_user, self._record_warning
        )

    def _record_warning(self, message):
        if message:
            self._warnings.append(message)
        if message and self.warning:
            self.warning(message)

    def push_run_inputs(self, task, remote_root):
        self.task = task
        self._warnings = []
        record = json.loads(task.record_path.read_text(encoding="utf-8"))
        remote_root = str(remote_root).rstrip("/")
        if task.run_id not in remote_root.split("/"):
            raise WorkspaceSyncError(msg("text.remote.task.root.must.contain.the.exact.run.id"))
        self._run("/bin/mkdir", "-p", str(Path(remote_root).parent).replace("\\", "/"))
        self._run("/bin/mkdir", remote_root)
        copied = []
        for item in record["input_files"]:
            source = task.snapshot_directory / item["path"]
            if file_digest(source) != item["sha256"]:
                raise WorkspaceSyncError(msg("text.frozen.input.changed") + item["path"])
            target = remote_root + "/" + item["path"]
            self._run("/bin/mkdir", "-p", target.rsplit("/", 1)[0])
            self._run("/bin/cp", "--", self._wslpath(source), target)
            checked = self._run("/usr/bin/sha256sum", "--", target)
            if checked.stdout.split()[0] != item["sha256"]:
                raise WorkspaceSyncError(
                    msg("text.remote.dependency.checksum.mismatch") + item["path"]
                )
            copied.append(item["path"])
        self.last_warnings = tuple(self._warnings)
        return SyncResult(tuple(copied), self.last_warnings)

    def _pull_isolated(self, task, source, required):
        self.task = task
        self._warnings = []
        target = task.output_directory
        if any(target.iterdir()):
            raise WorkspaceSyncError(
                msg("text.isolated.output.is.already.populated.refusing.to.merge.results")
            )
        staging = task.directory / "sync_staging"
        if staging.exists():
            raise WorkspaceSyncError(
                msg(
                    "text.previous.partial.synchronization.exists.retain.it.and.create.a.new.attempt"
                )
            )
        staging.mkdir()
        # Copy symlink targets so resources remain usable on Windows.
        self._run(
            "/bin/cp", "-aL", "--", str(source).rstrip("/") + "/.", self._wslpath(staging) + "/"
        )
        missing = [
            name
            for name in required
            if not (staging / name).is_file() or (staging / name).stat().st_size == 0
        ]
        if missing:
            raise WorkspaceSyncError(
                msg("text.synchronized.output.is.incomplete") + join_text(", ", missing)
            )
        listing = self._run(
            "/usr/bin/find",
            "-L",
            str(source).rstrip("/"),
            "-type",
            "f",
            "-exec",
            "/usr/bin/sha256sum",
            "--zero",
            "--",
            "{}",
            "+",
        )
        hashes = _output_checksums(listing.stdout, source)
        local_files = {p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file()}
        if local_files != set(hashes):
            raise WorkspaceSyncError(msg("text.returned.output.checksum.mismatch"))
        for relative, digest in hashes.items():
            if digest != file_digest(staging / relative):
                raise WorkspaceSyncError(msg("text.returned.output.checksum.mismatch") + relative)
        target.rmdir()
        staging.replace(target)
        self.last_warnings = tuple(self._warnings)
        return SyncResult(
            tuple((str(p) for p in target.rglob("*") if p.is_file())), self.last_warnings
        )

    def sync_run_outputs(self, task, remote_pflotran_dir):
        return self._pull_isolated(task, remote_pflotran_dir, ("model.out",))

    def sync_stage_run_outputs(self, task, remote_source):
        required = {
            "dfn": DFN_REQUIRED,
            "octree": (*OCTREE_REQUIRED, "connections.p"),
            "upscale": UPSCALE_REQUIRED,
        }[task.stage]
        return self._pull_isolated(task, remote_source, required)

    def promote_stage_outputs(self, task):
        from .runs import run_matches_current

        if not run_matches_current(task):
            return False
        if task.stage not in ("dfn", "octree", "upscale"):
            raise ValueError(msg("text.only.generated.spatial.stages.are.promoted"))
        target = task.project_directory / ("dfn" if task.stage == "dfn" else "octree")
        attempt = task.directory / "promotion_attempts" / uuid.uuid4().hex
        attempt.mkdir(parents=True, exist_ok=False)
        staging = attempt / "staging"
        shutil.copytree(task.output_directory, staging)
        if task.stage == "upscale" and target.is_dir():
            for p in target.rglob("*"):
                if p.is_file() and (not (staging / p.relative_to(target)).exists()):
                    dst = staging / p.relative_to(target)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dst)
        # Preserve prior stage receipts when replacing the current scientific products.
        history = target / "generation"
        if history.is_dir():
            shutil.copytree(history, staging / "generation", dirs_exist_ok=True)
        name = "dfn_config.json" if task.stage == "dfn" else "continuum_config.json"
        if (target / name).is_file():
            shutil.copy2(target / name, staging / name)
        from .storage import DirectoryReplacement, ProjectLock

        with ProjectLock(task.project_directory):
            if not run_matches_current(task):
                return False
            with DirectoryReplacement(target, overwrite=True) as replacement:
                shutil.copytree(staging, target)
                replacement.commit()
        return True


def _output_checksums(text, source):
    """Parse NUL-delimited checksums while preserving filenames."""
    import re

    prefix = str(source).rstrip("/") + "/"
    records = {}
    for record in text.split("\0"):
        if not record:
            continue
        digest, separator, remote = record[:64], record[64:66], record[66:]
        relative = remote[len(prefix) :]
        if (
            not re.fullmatch(r"[0-9a-f]{64}", digest)
            or separator != "  "
            or not remote.startswith(prefix)
            or not relative
            or ".." in relative.split("/")
            or relative in records
        ):
            raise WorkspaceSyncError(msg("text.returned.output.checksum.mismatch"))
        records[relative] = digest
    return records
