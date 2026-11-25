from __future__ import annotations

import inspect
import json
import os
import platform
import shutil
import sys
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fracrtm.config.dfn_config import DfnConfig
from fracrtm.dfn.pydfnworks_adapter import (
    NATIVE_DIRECTORY_LOCK,
    UDFM_REDUCED_MESH_VISUAL_MODE,
    PydfnworksAdapter,
)
from fracrtm.messages import (
    Message,
    diagnostic_message,
    join_text,
    prepare_json,
    render_record,
)
from fracrtm.messages import message as msg
from fracrtm.project.resources import UDFM_INPUT_FILENAMES, UdfmInputArtifacts


class DfnGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class DfnGenerationResult:
    success: bool
    stage: str
    job_directory: str
    started_at: str
    finished_at: str
    message: str | Message
    manifest_path: str
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return render_record(asdict(self))


ProgressCallback = Callable[[str, Message], None]


class DfnGenerationService:
    def __init__(self, config: DfnConfig, *, dfn_factory: Callable[..., Any] | None = None) -> None:
        self.config = config.copy()
        self._dfn_factory = dfn_factory

    def generate(
        self,
        job_directory,
        *,
        ncpu=1,
        overwrite=False,
        prepare_udfm=True,
        progress=None,
        raise_on_error=False,
    ):
        with NATIVE_DIRECTORY_LOCK:
            from fracrtm.project.storage import DirectoryReplacement

            # Validate before creating or replacing output files.
            try:
                self._validate_destination(Path(job_directory).expanduser().resolve(), overwrite)
            except DfnGenerationError:
                return self._generate(
                    job_directory,
                    ncpu=ncpu,
                    overwrite=overwrite,
                    prepare_udfm=prepare_udfm,
                    progress=progress,
                    raise_on_error=raise_on_error,
                )
            with (
                DirectoryReplacement(job_directory, overwrite=overwrite) as replacement,
            ):
                result = self._generate(
                    job_directory,
                    ncpu=ncpu,
                    overwrite=False,
                    prepare_udfm=prepare_udfm,
                    progress=progress,
                    raise_on_error=raise_on_error,
                )
                if result.success:
                    replacement.commit()
                else:
                    failure = (
                        Path(job_directory).resolve().parent
                        / ".fracrtm-failures"
                        / (_now().replace(":", "-") + ".json")
                    )
                    manifest = Path(result.manifest_path)
                    _write_json(
                        failure,
                        json.loads(manifest.read_text(encoding="utf-8"))
                        if manifest.is_file()
                        else result.to_dict(),
                    )
                return result

    def _generate(
        self,
        job_directory: str | Path,
        *,
        ncpu: int = 1,
        overwrite: bool = False,
        prepare_udfm: bool = True,
        progress: ProgressCallback | None = None,
        raise_on_error: bool = False,
    ) -> DfnGenerationResult:
        started = _now()
        stage = "preparing"
        job_path = Path(job_directory).expanduser().resolve()
        manifest_path = job_path / "fracrtm_run.json"
        original_cwd = Path.cwd()
        adapter = PydfnworksAdapter(self.config, self._dfn_factory)
        destination_owned = False
        warnings: list[str] = []
        workflow_status = {
            "dfn_geometry": "NOT_GENERATED",
            "udfm_input_preparation": "NOT_GENERATED" if prepare_udfm else "NOT_REQUESTED",
            "artifact_synchronization": "NOT_STARTED",
        }
        try:
            self._notify(progress, stage, msg("text.checking.the.dfn.configuration"))
            adapter.validate()
            self._validate_destination(job_path, overwrite)
            destination_owned = True
            stage = "building"
            self._notify(progress, stage, msg("text.creating.the.pydfnworks.model"))
            dfn = adapter.build(job_path, ncpu=ncpu)
            stage = "workspace"
            self._notify(progress, stage, msg("text.creating.an.isolated.run.directory"))
            job_path.parent.mkdir(parents=True, exist_ok=True)
            dfn.make_working_directory(delete=overwrite)
            job_path.mkdir(parents=True, exist_ok=True)
            self.config.save_json(job_path / "dfn_config.json")
            _write_json(job_path / "resolved_parameters.json", adapter.resolved_parameters())
            _write_json(
                manifest_path,
                self._manifest(
                    status="running",
                    stage=stage,
                    started_at=started,
                    ncpu=ncpu,
                    workflow_status=workflow_status,
                ),
            )
            stage = "checking_input"
            self._notify(progress, stage, msg("text.running.dfn.check.input"))
            dfn.check_input()
            stage = "creating_network"
            self._notify(progress, stage, msg("text.running.dfn.create.network"))
            dfn.create_network()
            workflow_status["dfn_geometry"] = "VALID"
            _write_json(
                manifest_path,
                self._manifest(
                    status="running",
                    stage=stage,
                    started_at=started,
                    ncpu=ncpu,
                    workflow_status=workflow_status,
                ),
            )
            if prepare_udfm:
                workflow_status["udfm_input_preparation"] = "RUNNING"
                stage = "preparing_continuum_inputs"
                self._notify(
                    progress,
                    stage,
                    msg("text.preparing.udfm.geometry.and.writing.fracture.hydraulic.properties"),
                )
                self._prepare_continuum_inputs(dfn, job_path)
                stage = "creating_reduced_mesh"
                self._notify(progress, stage, msg("text.generating.reduced.mesh.inp.for.udfm"))
                self._mesh_reduced_network(dfn, job_path)
                self._validate_udfm_inputs(job_path)
                workflow_status["udfm_input_preparation"] = "VALID"
            if self.config.output.create_output_report:
                report = getattr(dfn, "output_report", None)
                if callable(report):
                    stage = "creating_report"
                    self._notify(progress, stage, msg("text.generating.the.dfn.quality.report"))
                    try:
                        report()
                    except (Exception, SystemExit) as report_error:
                        message = (
                            diagnostic_message(report_error)
                            if str(report_error)
                            else msg("text.pydfnworks.report.generation.terminated.unexpectedly")
                        )
                        warnings.append(
                            msg(
                                "diagnostic.dfn_generation_service.quality.report.was.not.created.message",
                                message=message,
                            )
                        )
                        self._notify(
                            progress,
                            "warning",
                            msg("text.dfn.generation.completed.but.the.quality.report.failed")
                            + message,
                        )
            stage = "completed"
            finished = _now()
            result = DfnGenerationResult(
                success=True,
                stage=stage,
                job_directory=str(job_path),
                started_at=started,
                finished_at=finished,
                message=msg("text.dfn.network.generated.successfully.with.warnings")
                if warnings
                else msg("text.dfn.network.generated.successfully"),
                manifest_path=str(manifest_path),
                warnings=tuple(warnings),
            )
            manifest = self._manifest(
                status="success_with_warnings" if warnings else "success",
                stage=stage,
                started_at=started,
                ncpu=ncpu,
                workflow_status=workflow_status,
            )
            manifest["finished_at"] = finished
            manifest["warnings"] = warnings
            _write_json(manifest_path, manifest)
            self._notify(progress, stage, msg("text.dfn.generation.completed"))
            return result
        except (Exception, SystemExit) as exc:
            finished = _now()
            error_stage = stage
            error_message = (
                diagnostic_message(exc)
                if str(exc)
                else msg("text.pydfnworks.terminated.the.process.without.an.error.message")
            )
            if workflow_status["dfn_geometry"] == "VALID" and workflow_status[
                "udfm_input_preparation"
            ] in {"RUNNING", "NOT_GENERATED"}:
                workflow_status["udfm_input_preparation"] = "FAILED"
                result_message = msg(
                    "diagnostic.dfn_generation_service.dfn.geometry.generation.succeeded.but.udfm.input.preparation.failed.error.message",
                    error_message=error_message,
                )
                manifest_status = "partial_failure"
            else:
                result_message = error_message
                manifest_status = "failed"
            result = DfnGenerationResult(
                success=False,
                stage=error_stage,
                job_directory=str(job_path),
                started_at=started,
                finished_at=finished,
                message=result_message,
                manifest_path=str(manifest_path),
            )
            if destination_owned and job_path.exists():
                manifest = self._manifest(
                    status=manifest_status,
                    stage=error_stage,
                    started_at=started,
                    ncpu=ncpu,
                    workflow_status=workflow_status,
                )
                manifest.update(
                    finished_at=finished,
                    error_type=type(exc).__name__,
                    error_message=result_message,
                    traceback=traceback.format_exc(),
                )
                _write_json(manifest_path, manifest)
            self._notify(progress, "failed", result_message)
            if raise_on_error:
                raise DfnGenerationError(
                    msg(
                        "diagnostic.dfn_generation_service.dfn.workflow.failed.during.error.stage.result.message",
                        error_stage=error_stage,
                        result_message=result_message,
                    )
                ) from exc
            return result
        finally:
            os.chdir(original_cwd)

    def _prepare_continuum_inputs(self, dfn: Any, job_path: Path) -> None:
        generation_output = job_path / "dfnGen_output"
        for name in ("normal_vectors.dat", "translations.dat"):
            source = generation_output / name
            destination = job_path / name
            if not source.is_file():
                raise DfnGenerationError(
                    msg(
                        "diagnostic.dfn_generation_service.dfnworks.did.not.create.required.udfm.geometry.file.source",
                        source=source,
                    )
                )
            shutil.copy2(source, destination)
        dump_hydraulic_values = getattr(dfn, "dump_hydraulic_values", None)
        if not callable(dump_hydraulic_values):
            raise DfnGenerationError(
                msg("text.the.installed.pydfnworks.has.no.dump.hydraulic.values.method")
            )
        dump_hydraulic_values(format="FEHM")
        aperture_path = job_path / "aperture.dat"
        if not aperture_path.is_file():
            raise DfnGenerationError(
                msg(
                    "diagnostic.dfn_generation_service.pydfnworks.did.not.create.required.udfm.file.aperture.path",
                    aperture_path=aperture_path,
                )
            )
        # Use round-trip precision when transferring apertures.
        self._rewrite_apertures_full_precision(aperture_path, getattr(dfn, "aperture", None))

    @staticmethod
    def _rewrite_apertures_full_precision(aperture_path: Path, values: Any) -> None:
        if values is None:
            raise DfnGenerationError(msg("text.pydfnworks.did.not.expose.fracture.aperture.values"))
        apertures = [float(value) for value in values]
        lines = aperture_path.read_text(encoding="utf-8").splitlines()
        rows = [line for line in lines[1:] if line.strip()]
        if len(rows) != len(apertures):
            raise DfnGenerationError(
                msg(
                    "diagnostic.dfn_generation_service.fracture.aperture.count.does.not.match.aperture.dat.count.count.2",
                    count=len(apertures),
                    count_2=len(rows),
                )
            )
        rendered = [lines[0] if lines else "aperture"]
        for row, aperture in zip(rows, apertures):
            fields = row.split()
            if len(fields) < 2:
                raise DfnGenerationError(
                    msg(
                        "diagnostic.dfn_generation_service.invalid.fehm.aperture.row.row",
                        row=f"{row!r}",
                    )
                )
            rendered.append(join_text(" ", [*fields[:-1], f"{aperture:.17e}"]))
        temporary = aperture_path.with_suffix(".dat.tmp")
        temporary.write_text(join_text("\n", rendered) + "\n", encoding="utf-8")
        temporary.replace(aperture_path)

    @staticmethod
    def _mesh_reduced_network(dfn: Any, job_path: Path) -> None:
        mesh_network = getattr(dfn, "mesh_network", None)
        if not callable(mesh_network):
            raise DfnGenerationError(
                msg("text.the.installed.pydfnworks.has.no.mesh.network.method")
            )
        try:
            parameters = inspect.signature(mesh_network).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "visual_mode" in parameters:
            mesh_network(visual_mode=UDFM_REDUCED_MESH_VISUAL_MODE)
        else:
            if not hasattr(dfn, "visual_mode"):
                raise DfnGenerationError(
                    msg(
                        "text.pydfnworks.mesh.network.has.no.visual.mode.argument.and.the.dfn.object"
                    )
                )
            original_visual_mode = dfn.visual_mode
            try:
                dfn.visual_mode = UDFM_REDUCED_MESH_VISUAL_MODE
                mesh_network()
            finally:
                dfn.visual_mode = original_visual_mode
        reduced_mesh = job_path / "reduced_mesh.inp"
        if not reduced_mesh.is_file() or reduced_mesh.stat().st_size == 0:
            raise DfnGenerationError(
                msg(
                    "diagnostic.dfn_generation_service.pydfnworks.reduced.mesh.preparation.did.not.create.a.non.empty.file.reduced",
                    reduced_mesh=reduced_mesh,
                )
            )

    @staticmethod
    def _validate_udfm_inputs(job_path: Path) -> None:

        def existing(name: str) -> str:
            path = job_path / name
            return name if path.is_file() and path.stat().st_size > 0 else ""

        artifacts = UdfmInputArtifacts(*(existing(name) for name in UDFM_INPUT_FILENAMES))
        if not artifacts.complete:
            raise DfnGenerationError(
                msg("text.udfm.input.preparation.is.incomplete.missing")
                + join_text(", ", artifacts.missing)
            )

    @staticmethod
    def _validate_destination(job_path: Path, overwrite: bool) -> None:
        if job_path.exists() and (not job_path.is_dir()):
            raise DfnGenerationError(
                msg(
                    "diagnostic.dfn_generation_service.dfn.job.path.exists.but.is.not.a.directory.job.path",
                    job_path=job_path,
                )
            )
        if job_path.exists() and any(job_path.iterdir()) and (not overwrite):
            raise DfnGenerationError(
                msg(
                    "text.dfn.job.directory.is.not.empty.choose.another.directory.or.explicitly.enable"
                )
            )

    @staticmethod
    def _notify(progress: ProgressCallback | None, stage: str, message: Message) -> None:
        if progress is not None:
            progress(stage, message)

    @staticmethod
    def _manifest(
        *,
        status: str,
        stage: str,
        started_at: str,
        ncpu: int,
        workflow_status: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "status": status,
            "stage": stage,
            "started_at": started_at,
            "ncpu": ncpu,
            "python_version": sys.version,
            "platform": platform.platform(),
            "workflow_status": dict(workflow_status or {}),
        }
        try:
            import pydfnworks

            metadata["pydfnworks_path"] = str(Path(pydfnworks.__file__).resolve())
            metadata["pydfnworks_version"] = getattr(pydfnworks, "__version__", None)
        except ImportError:
            metadata["pydfnworks_path"] = None
            metadata["pydfnworks_version"] = None
        return metadata


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(prepare_json(render_record(data)), stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(path)
