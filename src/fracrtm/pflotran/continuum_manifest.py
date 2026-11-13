from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fracrtm.messages import message as msg
from fracrtm.messages import message_record, prepare_json, render_record
from fracrtm.project.resources import SEMANTIC_BOUNDARIES, ProjectArtifacts


@dataclass(frozen=True)
class ContinuumManifest:
    schema_version: str = "1.0"
    mesh: str = ""
    materials: str = ""
    permeability: str = ""
    porosity: str = ""
    boundary_regions: dict[str, str] = field(default_factory=dict)
    visualization: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save_json(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(prepare_json(self.to_dict()), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)


@dataclass(frozen=True)
class ContinuumValidationReport:
    ready: bool
    cell_count: int | None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    datasets: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        record = render_record(asdict(self))
        record["error_details"] = [message_record(value) for value in self.errors]
        record["warning_details"] = [message_record(value) for value in self.warnings]
        return record


_FILE_CANDIDATES = {
    "mesh": ("full_mesh.uge",),
    "materials": ("materials.h5",),
    "permeability": ("mesh_permeability.h5",),
    "porosity": ("mesh_porosity.h5",),
    "visualization": ("octree_dfn.vtk", "octree_dfn.inp"),
}


def discover_continuum_outputs(
    project_directory: str | Path, *, octree_directory: str = "octree"
) -> ContinuumManifest:
    project = Path(project_directory).expanduser().resolve()
    octree = project / octree_directory
    artifacts = ProjectArtifacts.discover(project)

    def find(candidates: tuple[str, ...]) -> str:
        for name in candidates:
            path = octree / name
            try:
                if path.is_file() and path.stat().st_size > 0:
                    return path.relative_to(project).as_posix()
            except OSError:
                continue
        return ""

    boundaries = {
        semantic: item.relative_path for semantic, item in artifacts.octree.boundaries.items()
    }
    visualization = find(_FILE_CANDIDATES["visualization"])
    return ContinuumManifest(
        mesh=artifacts.octree.full_mesh,
        materials=artifacts.octree.materials,
        permeability=artifacts.octree.permeability,
        porosity=artifacts.octree.porosity,
        boundary_regions=boundaries,
        visualization=visualization or None,
    )


def validate_continuum_outputs(
    project_directory: str | Path,
    manifest: ContinuumManifest,
    *,
    required_boundaries: tuple[str, ...] = SEMANTIC_BOUNDARIES,
) -> ContinuumValidationReport:
    project = Path(project_directory).expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    datasets: dict[str, dict[str, Any]] = {}

    def resolve(relative: str, label: str) -> Path | None:
        if not relative:
            errors.append(
                msg(
                    "diagnostic.continuum_manifest.missing.label.path.in.continuum.manifest",
                    label=label,
                )
            )
            return None
        path = (project / relative).resolve()
        try:
            path.relative_to(project)
        except ValueError:
            errors.append(
                msg(
                    "diagnostic.continuum_manifest.label.path.escapes.the.project.directory.relative",
                    label=label,
                    relative=relative,
                )
            )
            return None
        if not path.is_file():
            errors.append(msg("resource.missing_file", label=label, path=relative))
            return None
        if path.stat().st_size == 0:
            errors.append(msg("resource.empty_file", label=label, path=relative))
            return None
        return path

    mesh_path = resolve(manifest.mesh, msg("text.mesh"))
    material_path = resolve(manifest.materials, msg("viewer.materials"))
    permeability_path = resolve(manifest.permeability, msg("viewer.permeability"))
    porosity_path = resolve(manifest.porosity, msg("viewer.porosity"))
    for face in required_boundaries:
        resolve(manifest.boundary_regions.get(face, ""), msg("resource.boundary_label", face=face))
    cell_count = _read_uge_cell_count(mesh_path, errors) if mesh_path else None
    h5_paths = {
        "materials": material_path,
        "permeability": permeability_path,
        "porosity": porosity_path,
    }
    if all((path is not None for path in h5_paths.values())):
        try:
            import h5py
        except ImportError:
            warnings.append(
                msg(
                    "text.h5py.is.unavailable.hdf5.dataset.names.lengths.and.values.were.not.checked"
                )
            )
        else:
            for label, path in h5_paths.items():
                assert path is not None
                datasets[label] = _inspect_hdf5(path, h5py, errors)
            _validate_hdf5_contract(datasets, cell_count, errors, warnings)
    for face in required_boundaries:
        relative = manifest.boundary_regions.get(face, "")
        if relative and (not relative.lower().endswith(".ex")):
            errors.append(
                msg(
                    "diagnostic.continuum_manifest.face.boundary.must.be.a.pydfnworks.ex.artifact.relative",
                    face=face,
                    relative=relative,
                )
            )
    return ContinuumValidationReport(
        ready=not errors,
        cell_count=cell_count,
        errors=tuple(errors),
        warnings=tuple(warnings),
        datasets=datasets,
    )


def _read_uge_cell_count(path: Path, errors: list[str]) -> int | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            first = stream.readline().split()
        if len(first) != 2 or first[0].upper() != "CELLS":
            raise ValueError(msg("text.first.line.must.be.cells.count"))
        count = int(first[1])
        if count < 1:
            raise ValueError(msg("text.cell.count.must.be.positive"))
        return count
    except (OSError, ValueError) as exc:
        errors.append(
            msg("diagnostic.continuum_manifest.invalid.uge.mesh.name.exc", name=path.name, exc=exc)
        )
        return None


def _inspect_hdf5(path: Path, h5py: Any, errors: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        with h5py.File(path, "r") as handle:

            def visit(name: str, obj: Any) -> None:
                if not isinstance(obj, h5py.Dataset):
                    return
                data = obj[...]
                item: dict[str, Any] = {
                    "shape": list(data.shape),
                    "size": int(data.size),
                    "dtype": str(data.dtype),
                }
                if data.size and getattr(data.dtype, "kind", "") in "fiu":
                    item["minimum"] = float(data.min())
                    item["maximum"] = float(data.max())
                    if data.dtype.kind == "f":
                        item["finite"] = bool(
                            all((math.isfinite(float(value)) for value in data.flat))
                        )
                result[name] = item

            handle.visititems(visit)
    except (OSError, ValueError) as exc:
        errors.append(
            msg(
                "diagnostic.continuum_manifest.cannot.read.hdf5.file.name.exc",
                name=path.name,
                exc=exc,
            )
        )
    return result


def _validate_hdf5_contract(
    datasets: dict[str, dict[str, Any]],
    cell_count: int | None,
    errors: list[str],
    warnings: list[str],
) -> None:
    material = datasets.get("materials", {})
    cell_ids = _find_dataset(material, "cell ids")
    material_ids = _find_dataset(material, "material ids")
    if cell_ids is None or material_ids is None:
        errors.append(msg("text.materials.h5.must.contain.cell.ids.and.material.ids.datasets"))
    else:
        if cell_ids["size"] != material_ids["size"]:
            errors.append(msg("text.materials.h5.cell.ids.and.material.ids.lengths.differ"))
        if cell_count is not None and cell_ids["size"] != cell_count:
            errors.append(
                msg(
                    "diagnostic.continuum_manifest.materials.h5.cell.count.does.not.match.full.mesh.uge.value.cell",
                    value=cell_ids["size"],
                    cell_count=cell_count,
                )
            )
    for label in ("porosity", "permeability"):
        group = datasets.get(label, {})
        if not group:
            errors.append(
                msg(
                    "diagnostic.continuum_manifest.label.hdf5.file.contains.no.datasets",
                    label=label,
                )
            )
            continue
        if any((item.get("finite") is False for item in group.values())):
            errors.append(
                msg(
                    "diagnostic.continuum_manifest.label.hdf5.file.contains.nan.or.infinite.values",
                    label=label,
                )
            )
        sizes = {item["size"] for item in group.values()}
        if cell_count is not None and cell_count not in sizes:
            warnings.append(
                msg(
                    "diagnostic.continuum_manifest.no.label.dataset.has.exactly.cell.count.values.inspect.whether.the.file",
                    label=label,
                    cell_count=cell_count,
                )
            )
    porosity = datasets.get("porosity", {})
    for name, item in porosity.items():
        if item.get("minimum", 0.0) < 0.0 or item.get("maximum", 1.0) > 1.0:
            errors.append(
                msg(
                    "diagnostic.continuum_manifest.porosity.dataset.name.has.values.outside.0.1",
                    name=name,
                )
            )


def _find_dataset(datasets: dict[str, dict[str, Any]], suffix: str) -> dict[str, Any] | None:
    suffix = suffix.casefold()
    for name, value in datasets.items():
        if name.casefold().endswith(suffix):
            return value
    return None
