from __future__ import annotations

import json
import os
import shutil
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fracrtm.config.continuum_config import ContinuumConfig
from fracrtm.dfn.pydfnworks_adapter import NATIVE_DIRECTORY_LOCK
from fracrtm.messages import (
    Message,
    diagnostic_message,
    join_text,
    prepare_json,
    render_record,
)
from fracrtm.messages import message as msg
from fracrtm.project.resources import UDFM_INPUT_FILENAMES


class ContinuumMappingError(RuntimeError):
    pass


@dataclass(frozen=True)
class ContinuumMappingResult:
    success: bool
    stage: str
    job_directory: str
    output_directory: str
    message: str | Message
    started_at: str
    finished_at: str

    def to_dict(self) -> dict[str, Any]:
        return render_record(asdict(self))


ProgressCallback = Callable[[str, Message], None]


class ContinuumMappingService:
    REQUIRED_OCTREE_INPUTS = (
        "params.txt",
        "poly_info.dat",
        "normal_vectors.dat",
        "translations.dat",
        "reduced_mesh.inp",
    )
    REQUIRED_UPSCALE_INPUTS = ("params.txt", "normal_vectors.dat", "aperture.dat")
    REQUIRED_DFN_FILES = UDFM_INPUT_FILENAMES
    REQUIRED_OCTREE_FILES = ("octree_dfn.inp",)

    def __init__(
        self, config: ContinuumConfig, *, dfn_factory: Callable[..., Any] | None = None
    ) -> None:
        self.config = ContinuumConfig.from_dict(config.to_dict())
        self._dfn_factory = dfn_factory

    def run(self, job_directory, *, ncpu=1, stage="all", overwrite=False, progress=None):
        with NATIVE_DIRECTORY_LOCK:
            from fracrtm.project.storage import DirectoryReplacement

            errors = self.config.validate()
            if errors:
                raise ContinuumMappingError(join_text("\n", errors))
            if stage not in {"octree", "upscale", "all"}:
                raise ContinuumMappingError(
                    msg(
                        "diagnostic.continuum_mapping_service.unknown.continuum.stage.stage",
                        stage=stage,
                    )
                )
            if not Path(job_directory).expanduser().resolve().is_dir():
                return self._run(
                    job_directory, ncpu=ncpu, stage=stage, overwrite=overwrite, progress=progress
                )
            target = Path(job_directory).expanduser().resolve() / self.config.output_directory_name
            if stage == "upscale":
                prior = target / "continuum_config.json"
                record_path = target / "boundary_conversion.json"
                boundary_changed = False
                if record_path.is_file():
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                    boundary_changed = record.get(
                        "parameter_source", "tool_default"
                    ) != self.config.boundary_area_mode or (
                        self.config.boundary_area_mode == "custom"
                        and record.get("requested_boundary_cell_area")
                        != self.config.boundary_cell_area
                    )
                elif self.config.boundary_area_mode == "custom":
                    boundary_changed = True
                if boundary_changed or (
                    prior.is_file()
                    and ContinuumConfig.load_json(prior).boundary_identity()
                    != self.config.boundary_identity()
                ):
                    stage = "all"
                    overwrite = True
                elif not prior.is_file() and self.config.boundary_area_mode == "custom":
                    stage = "all"
                    overwrite = True
            with (
                DirectoryReplacement(
                    target,
                    overwrite=overwrite or stage == "upscale",
                    seed_existing=stage == "upscale",
                ) as replacement,
            ):
                result = self._run(
                    job_directory, ncpu=ncpu, stage=stage, overwrite=False, progress=progress
                )
                if result.success:
                    replacement.commit()
                else:
                    failure = (
                        target.parent / ".fracrtm-failures" / (_now().replace(":", "-") + ".json")
                    )
                    manifest = target / "continuum_run.json"
                    _write_json(
                        failure,
                        json.loads(manifest.read_text(encoding="utf-8"))
                        if manifest.is_file()
                        else result.to_dict(),
                    )
                return result

    def _run(
        self,
        job_directory: str | Path,
        *,
        ncpu: int = 1,
        stage: str = "all",
        overwrite: bool = False,
        progress: ProgressCallback | None = None,
    ) -> ContinuumMappingResult:
        if stage not in {"octree", "upscale", "all"}:
            raise ContinuumMappingError(
                msg(
                    "diagnostic.continuum_mapping_service.unknown.continuum.stage.stage",
                    stage=stage,
                )
            )
        errors = self.config.validate()
        if errors:
            raise ContinuumMappingError(join_text("\n", errors))
        if ncpu < 1:
            raise ContinuumMappingError(msg("text.ncpu.must.be.at.least.1"))
        started = _now()
        current_stage = "preparing"
        job_path = Path(job_directory).expanduser().resolve()
        output_path = job_path / self.config.output_directory_name
        original_cwd = Path.cwd()
        destination_owned = False
        try:
            self._notify(progress, current_stage, msg("text.checking.dfn.and.udfm.input.files"))
            if not job_path.is_dir():
                raise ContinuumMappingError(msg("continuum.missing_directory", path=job_path))
            self._normalize_dfn_layout(job_path)
            if stage in {"octree", "all"}:
                self._require_files(job_path, self.REQUIRED_OCTREE_INPUTS)
                if stage == "all":
                    self._require_files(job_path, self.REQUIRED_UPSCALE_INPUTS)
                if output_path.exists() and any(output_path.iterdir()) and (not overwrite):
                    raise ContinuumMappingError(
                        msg(
                            "text.the.octree.output.directory.is.not.empty.choose.a.new.directory.or.explicitly.allow"
                        )
                    )
            else:
                self._require_files(output_path, self.REQUIRED_OCTREE_FILES)
                self._require_files(job_path, self.REQUIRED_UPSCALE_INPUTS)
            destination_owned = True
            dfn = self._create_dfn(job_path, ncpu)
            os.chdir(job_path)
            self._hydrate_dfn(dfn, job_path, require_aperture=stage in {"upscale", "all"})
            if stage in {"octree", "all"}:
                current_stage = "octree"
                self._notify(
                    progress, current_stage, msg("text.generating.the.octree.refined.mesh")
                )
                dfn.map_to_continuum(
                    l=self.config.base_cell_size,
                    orl=self.config.refinement_levels,
                    path=str(job_path) + os.sep,
                    dir_name=self.config.output_directory_name,
                )
                self._require_files(output_path, self.REQUIRED_OCTREE_FILES)
                current_stage = "boundary_conversion"
                self._notify(
                    progress,
                    current_stage,
                    msg("text.generating.six.pflotran.external.boundaries.with.pydfnworks.zone2ex"),
                )
                self._convert_boundaries_to_ex(dfn, output_path)
            if stage in {"upscale", "all"}:
                current_stage = "upscale"
                self._notify(
                    progress,
                    current_stage,
                    msg("text.generating.equivalent.permeability.and.porosity"),
                )
                os.chdir(output_path)
                dfn.upscale(
                    mat_perm=self.config.matrix_permeability,
                    mat_por=self.config.matrix_porosity,
                    path=str(job_path) + os.sep,
                )
                self._require_files(output_path, ("mesh_permeability.h5", "mesh_porosity.h5"))
            self.config.save_json(output_path / "continuum_config.json")
            finished = _now()
            result = ContinuumMappingResult(
                success=True,
                stage="completed",
                job_directory=str(job_path),
                output_directory=str(output_path),
                message=msg("text.continuum.mapping.completed.successfully"),
                started_at=started,
                finished_at=finished,
            )
            _write_json(output_path / "continuum_run.json", result.to_dict())
            self._notify(
                progress, "completed", msg("text.octree.effective.property.task.completed")
            )
            return result
        except (Exception, SystemExit) as exc:
            message = (
                diagnostic_message(exc)
                if str(exc)
                else msg("text.pydfnworks.terminated.without.an.error.message")
            )
            finished = _now()
            result = ContinuumMappingResult(
                success=False,
                stage=current_stage,
                job_directory=str(job_path),
                output_directory=str(output_path),
                message=message,
                started_at=started,
                finished_at=finished,
            )
            if destination_owned and output_path.exists():
                record = result.to_dict()
                record["traceback"] = traceback.format_exc()
                _write_json(output_path / "continuum_run.json", record)
            self._notify(progress, "failed", msg("text.continuum.mapping.failed") + message)
            return result
        finally:
            os.chdir(original_cwd)

    def _create_dfn(self, job_path: Path, ncpu: int) -> Any:
        factory = self._dfn_factory
        if factory is None:
            from pydfnworks import DFNWORKS

            factory = DFNWORKS
        return factory(jobname=str(job_path), ncpu=ncpu, flow_solver="PFLOTRAN")

    def _convert_boundaries_to_ex(self, dfn: Any, output_path: Path) -> None:
        converter = getattr(dfn, "zone2ex", None)
        if not callable(converter):
            raise ContinuumMappingError(msg("boundary_area.unsupported"))
        dfn.uge_file = str(output_path / "full_mesh.uge")
        original = Path.cwd()
        try:
            os.chdir(output_path)
            if self.config.boundary_area_mode == "custom":
                import inspect

                try:
                    inspect.signature(converter).bind(
                        zone_file="all", boundary_cell_area=self.config.boundary_cell_area
                    )
                except (TypeError, ValueError) as exc:
                    raise ContinuumMappingError(msg("boundary_area.unsupported")) from exc
                converter(zone_file="all", boundary_cell_area=self.config.boundary_cell_area)
            else:
                converter(zone_file="all")
        finally:
            os.chdir(original)
        required = (
            "boundary_top.ex",
            "boundary_bottom.ex",
            "boundary_left_w.ex",
            "boundary_right_e.ex",
            "boundary_front_n.ex",
            "boundary_back_s.ex",
        )
        ContinuumMappingService._require_files(output_path, required)
        import hashlib
        import inspect

        parameter = inspect.signature(converter).parameters.get("boundary_cell_area")
        default_area = parameter.default if parameter is not None else None
        if default_area is inspect.Parameter.empty:
            default_area = None
        source = inspect.getsourcefile(converter)
        import math

        statistics = {}
        for name in required:
            lines = (output_path / name).read_text(encoding="utf-8").splitlines()
            header = lines[0].split()
            rows = [line.split() for line in lines[1:] if line.strip()]
            if (
                len(header) != 2
                or header[0] != "CONNECTIONS"
                or int(header[1]) != len(rows)
                or any(len(row) != 5 for row in rows)
            ):
                raise ContinuumMappingError(msg("boundary_area.invalid_output", file=name))
            areas = [float(row[4]) for row in rows]
            if any(not math.isfinite(value) or value <= 0 for value in areas):
                raise ContinuumMappingError(msg("boundary_area.invalid_output", file=name))
            # zone2ex writes areas with 13 significant digits (.12e).
            if self.config.boundary_area_mode == "custom" and any(
                not math.isclose(value, self.config.boundary_cell_area, rel_tol=5e-12, abs_tol=0)
                for value in areas
            ):
                raise ContinuumMappingError(msg("boundary_area.mismatch", file=name))
            statistics[name] = {
                "count": len(areas),
                "min": min(areas) if areas else None,
                "max": max(areas) if areas else None,
                "sha256": hashlib.sha256((output_path / name).read_bytes()).hexdigest(),
            }
        _write_json(
            output_path / "boundary_conversion.json",
            {
                "parameter_source": self.config.boundary_area_mode,
                "requested_boundary_cell_area": self.config.boundary_cell_area
                if self.config.boundary_area_mode == "custom"
                else None,
                "units": "m^2",
                "written_area_statistics": statistics,
                "method": getattr(converter, "__qualname__", type(converter).__name__),
                "module": getattr(converter, "__module__", None),
                "source": source,
                "source_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest()
                if source and Path(source).is_file()
                else None,
                "default_boundary_cell_area": default_area,
                "mesh_scale_h": getattr(dfn, "h", None),
                "area_method_status": "requires_scientific_confirmation",
            },
        )

    @staticmethod
    def _hydrate_dfn(dfn: Any, job_path: Path, *, require_aperture: bool) -> None:
        gather = getattr(dfn, "gather_dfn_gen_output", None)
        if not callable(gather):
            raise ContinuumMappingError(
                msg(
                    "text.the.installed.pydfnworks.cannot.restore.an.existing.dfn.gather.dfn.gen.output"
                )
            )
        gather()
        num_frac = getattr(dfn, "num_frac", None)
        if type(num_frac) is not int or num_frac < 1:
            raise ContinuumMappingError(
                msg(
                    "diagnostic.continuum_mapping_service.invalid.fracture.count.restored.by.pydfnworks.num.frac",
                    num_frac=f"{num_frac!r}",
                )
            )
        if require_aperture:
            aperture_path = job_path / "aperture.dat"
            apertures: list[float] = []
            try:
                with aperture_path.open("r", encoding="utf-8") as stream:
                    next(stream)
                    for line in stream:
                        fields = line.split()
                        if fields:
                            apertures.append(float(fields[-1]))
            except (OSError, StopIteration, ValueError) as exc:
                raise ContinuumMappingError(
                    msg(
                        "diagnostic.continuum_mapping_service.cannot.restore.fracture.apertures.from.aperture.path.exc",
                        aperture_path=aperture_path,
                        exc=exc,
                    )
                ) from exc
            if len(apertures) != num_frac:
                raise ContinuumMappingError(
                    msg(
                        "diagnostic.continuum_mapping_service.aperture.count.does.not.match.the.restored.fracture.count.count.num.frac",
                        count=len(apertures),
                        num_frac=num_frac,
                    )
                )
            # upscale() requires the restored aperture array.
            dfn.aperture = apertures

    @staticmethod
    def _normalize_dfn_layout(job_path: Path) -> None:
        generation_output = job_path / "dfnGen_output"
        for name in ("normal_vectors.dat", "translations.dat"):
            destination = job_path / name
            source = generation_output / name
            if not destination.is_file() and source.is_file():
                shutil.copy2(source, destination)

    @staticmethod
    def _require_files(directory: Path, names: tuple[str, ...]) -> None:
        missing = [name for name in names if not (directory / name).is_file()]
        if missing:
            raise ContinuumMappingError(
                msg("continuum.missing_files", path=directory, files=join_text(", ", missing))
            )

    @staticmethod
    def _notify(progress: ProgressCallback | None, stage: str, message: Message) -> None:
        if progress is not None:
            progress(stage, message)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(prepare_json(render_record(data)), stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(path)
