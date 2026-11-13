from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fracrtm.messages import join_text, legacy_terms
from fracrtm.messages import message as msg

from .continuum_manifest import ContinuumManifest


@dataclass(frozen=True)
class MeshSummary:
    mesh_path: str = ""
    mesh_type: str = ""
    cell_count: int = 0
    connection_count: int = 0
    coordinate_bounds: tuple = ()
    coordinate_unit: str = "m"
    status: str = "missing"
    message: str = ""
    cell_ids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class MaterialPartitionSummary:
    file_path: str = ""
    material_count: int = 0
    status: str = "missing"
    message: str = ""
    active_cell_count: int = 0
    mapped_cell_count: int = 0
    unmapped_cell_count: int = 0
    material_cell_counts: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class SpatialDatasetSummary:
    property_name: str
    dataset_name: str
    relative_file: str
    hdf5_path: str
    cell_count: int
    minimum: float | None
    maximum: float | None
    unit: str
    status: str
    message: str = ""
    dtype: str = ""
    missing_count: int = 0


@dataclass(frozen=True)
class BoundarySurfaceSummary:
    display_name: str
    internal_name: str
    file_path: str
    face_count: int
    status: str
    message: str = ""


@dataclass(frozen=True)
class DataCheckIssue:
    severity: str
    message: str


@dataclass(frozen=True)
class ProjectSpatialDataSummary:
    mesh: MeshSummary = MeshSummary()
    materials: MaterialPartitionSummary = MaterialPartitionSummary()
    datasets: tuple[SpatialDatasetSummary, ...] = ()
    boundaries: tuple[BoundarySurfaceSummary, ...] = ()
    issues: tuple[DataCheckIssue, ...] = ()


_STANDARD_BOUNDARIES = {
    "top": msg("text.top.boundary"),
    "bottom": msg("text.bottom.boundary"),
    "west": msg("text.west.boundary"),
    "east": msg("text.east.boundary"),
    "north": msg("text.north.boundary"),
    "south": msg("text.south.boundary"),
}


def inspect_project_spatial_data(
    project_directory: str | Path | None,
    manifest: ContinuumManifest | None,
    dataset_definitions: Iterable[tuple[str, str, str, str | None]],
    *,
    octree_directory: str = "octree",
) -> ProjectSpatialDataSummary:
    if project_directory is None:
        issue = DataCheckIssue(
            "error",
            msg(
                "text.the.current.project.directory.is.unavailable.mesh.and.spatial.data.cannot.be.read"
            ),
        )
        return ProjectSpatialDataSummary(issues=(issue,))
    project = Path(project_directory).expanduser().resolve()
    octree = project / octree_directory
    manifest = manifest or ContinuumManifest()
    mesh = _inspect_mesh(project, manifest.mesh)
    materials, material_issues = _inspect_materials(project, manifest.materials, mesh)
    datasets, dataset_issues = _inspect_datasets(project, manifest, dataset_definitions, mesh)
    boundaries, boundary_issues = _inspect_boundaries(project, octree, manifest, mesh)
    issues: list[DataCheckIssue] = []
    if mesh.status != "available":
        issues.append(
            DataCheckIssue("error", mesh.message or msg("validation.invalid.computational.mesh"))
        )
    issues.extend(material_issues)
    issues.extend(dataset_issues)
    issues.extend(boundary_issues)
    return ProjectSpatialDataSummary(
        mesh, materials, tuple(datasets), tuple(boundaries), tuple(issues)
    )


def _resolve(project: Path, relative: str) -> Path | None:
    if not relative:
        return None
    path = (project / relative).resolve()
    try:
        path.relative_to(project)
    except ValueError:
        return None
    from fracrtm.project.storage import resource_path

    return resource_path(project, relative, path)


def _safe_is_file(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return path.is_file()
    except OSError:
        return False


def _inspect_mesh(project: Path, relative: str) -> MeshSummary:
    path = _resolve(project, relative)
    if not _safe_is_file(path):
        return MeshSummary(
            relative, status="missing", message=msg("text.computational.mesh.file.not.found")
        )
    try:
        from fracrtm.visualization.spatial_mapping import read_uge

        volumes = read_uge(path)
        coordinates = volumes.points
        ids = set(volumes.ids.tolist())
        cell_count = len(volumes.ids)
        connection_count = len(volumes.connections)
        axes = tuple(
            (
                (
                    min((point[index] for point in coordinates)),
                    max((point[index] for point in coordinates)),
                )
                for index in range(3)
            )
        )
        return MeshSummary(
            relative,
            msg("text.unstructured.explicit.grid"),
            cell_count,
            connection_count,
            axes,
            "m",
            "available",
            "",
            frozenset(ids),
        )
    except UnicodeError as exc:
        return MeshSummary(
            relative, status="read_failed", message=msg("spatial.read_mesh", reason=exc)
        )
    except OSError as exc:
        return MeshSummary(
            relative, status="read_failed", message=msg("spatial.read_mesh", reason=exc)
        )
    except (ValueError, IndexError) as exc:
        return MeshSummary(
            relative, status="invalid", message=msg("spatial.invalid_mesh", reason=exc)
        )


def _h5_module():
    try:
        import h5py
    except ImportError:
        return None
    return h5py


def _datasets_by_name(handle):
    result = {}
    h5py = _h5_module()
    if h5py is None:
        return result
    handle.visititems(
        lambda name, obj: result.__setitem__(name, obj) if isinstance(obj, h5py.Dataset) else None
    )
    return result


def _find_named(datasets: dict, suffix: str):
    suffix = suffix.casefold()
    return next(
        ((name, value) for name, value in datasets.items() if name.casefold().endswith(suffix)),
        (None, None),
    )


def _inspect_materials(project: Path, relative: str, mesh: MeshSummary):
    issues: list[DataCheckIssue] = []
    path = _resolve(project, relative)
    if not _safe_is_file(path):
        summary = MaterialPartitionSummary(
            relative, status="missing", message=msg("text.material.partition.file.not.found")
        )
        return (summary, [DataCheckIssue("error", summary.message)])
    h5py = _h5_module()
    if h5py is None:
        summary = MaterialPartitionSummary(
            relative,
            status="read_failed",
            message=msg("text.h5py.is.unavailable.material.partitions.cannot.be.read"),
        )
        return (summary, [DataCheckIssue("error", summary.message)])
    try:
        with h5py.File(path, "r") as handle:
            arrays = _datasets_by_name(handle)
            cell_dataset = arrays.get("Materials/Cell Ids")
            material_dataset = arrays.get("Materials/Material Ids")
            if cell_dataset is None or material_dataset is None:
                raise ValueError(msg("text.cell.ids.or.material.ids.dataset.is.missing"))
            cell_ids = [int(value) for value in cell_dataset[...].reshape(-1)]
            material_values = list(material_dataset[...].reshape(-1))
        if len(cell_ids) != len(material_values):
            raise ValueError(msg("text.cell.ids.and.material.ids.have.different.lengths"))
        if len(set(cell_ids)) != len(cell_ids):
            raise ValueError(msg("text.the.material.partition.contains.duplicate.cell.ids"))
        material_ids = [int(value) for value in material_values]
        if any(
            (value < 0 or float(raw) != value for raw, value in zip(material_values, material_ids))
        ):
            raise ValueError(msg("validation.material.ids.must.be.nonnegative.integers"))
        missing: set[int] = set()
        if mesh.cell_ids:
            unknown = set(cell_ids) - set(mesh.cell_ids)
            missing = set(mesh.cell_ids) - set(cell_ids)
            if unknown:
                raise ValueError(msg("spatial.unknown_ids", count=len(unknown)))
            if missing:
                raise ValueError(msg("spatial.unassigned", count=len(missing)))
        if (
            mesh.status == "available"
            and path.stat().st_mtime < (_resolve(project, mesh.mesh_path) or path).stat().st_mtime
        ):
            issues.append(
                DataCheckIssue(
                    "warning",
                    msg(
                        "text.the.material.partition.predates.the.current.mesh.and.may.be.outdated"
                    ),
                )
            )
        status = "warning" if issues else "available"
        message = issues[0].message if issues else ""
        counts = tuple(
            sorted(((value, material_ids.count(value)) for value in set(material_ids) if value > 0))
        )
        mapped = sum((count for _, count in counts))
        return (
            MaterialPartitionSummary(
                relative,
                len(counts),
                status,
                message,
                active_cell_count=mapped,
                mapped_cell_count=mapped,
                unmapped_cell_count=len(missing),
                material_cell_counts=counts,
            ),
            issues,
        )
    except OSError as exc:
        summary = MaterialPartitionSummary(
            relative, status="read_failed", message=msg("spatial.read_material", reason=exc)
        )
    except (ValueError, TypeError) as exc:
        summary = MaterialPartitionSummary(
            relative, status="invalid", message=msg("spatial.invalid_material", reason=exc)
        )
    return (summary, [DataCheckIssue("error", summary.message)])


def _dataset_candidates(manifest, definitions):
    candidates = list(definitions)
    known_files = {item[2] for item in candidates}
    if manifest.porosity and manifest.porosity not in known_files:
        candidates.append(("udfm_porosity", "Porosity", manifest.porosity, None))
    if manifest.permeability and manifest.permeability not in known_files:
        candidates.append(("udfm_permeability", "Permeability", manifest.permeability, None))
    return candidates


def _inspect_datasets(project, manifest, definitions, mesh):
    summaries: list[SpatialDatasetSummary] = []
    issues: list[DataCheckIssue] = []
    h5py = _h5_module()
    for object_id, name, relative, requested_path in _dataset_candidates(manifest, definitions):
        text = join_text("", [object_id, " ", name, " ", requested_path or ""]).casefold()
        is_porosity = any(alias in text for alias in legacy_terms("porosity_names"))
        is_permeability = not is_porosity and any(
            alias in text for alias in legacy_terms("permeability_names")
        )
        property_name = (
            msg("viewer.porosity")
            if is_porosity
            else msg("viewer.permeability")
            if is_permeability
            else name
        )
        unit = "m²" if is_permeability else "—"
        path = _resolve(project, relative)
        status = "available"
        messages: list[str] = []
        hdf5_path = requested_path or ""
        count = 0
        minimum = None
        maximum = None
        dtype = ""
        missing_count = 0
        if not _safe_is_file(path):
            status = "missing"
            messages.append(msg("text.dataset.file.not.found"))
        elif h5py is None:
            status = "invalid"
            messages.append(msg("text.h5py.is.unavailable.hdf5.datasets.cannot.be.read"))
        else:
            try:
                with h5py.File(path, "r") as handle:
                    arrays = _datasets_by_name(handle)
                    hdf5_path = requested_path or name
                    dataset = arrays.get(hdf5_path.lstrip("/"))
                    if dataset is None:
                        raise ValueError(msg("spatial.missing_dataset", dataset=hdf5_path))
                    values = dataset[...]
                    if mesh.cell_ids:
                        from fracrtm.visualization.spatial_mapping import read_hdf5_values

                        values = read_hdf5_values(path, hdf5_path, None, sorted(mesh.cell_ids))
                    dtype = str(values.dtype)
                    count = int(values.size)
                    if values.ndim != 1:
                        messages.append(
                            msg("text.the.dataset.is.not.a.one.dimensional.cell.scalar.array")
                        )
                    numeric = [float(value) for value in values.reshape(-1)]
                    missing_count = sum((not math.isfinite(value) for value in numeric))
                    finite = [value for value in numeric if math.isfinite(value)]
                    if finite:
                        minimum, maximum = (min(finite), max(finite))
                    if missing_count:
                        messages.append(msg("spatial.missing_values", count=missing_count))
                    if mesh.cell_count and count != mesh.cell_count:
                        messages.append(
                            msg("spatial.cell_count", actual=count, expected=mesh.cell_count)
                        )
                    _, cell_dataset = _find_named(arrays, "cell ids")
                    if cell_dataset is not None:
                        cell_ids = [int(value) for value in cell_dataset[...].reshape(-1)]
                        if len(set(cell_ids)) != len(cell_ids):
                            messages.append(msg("text.duplicate.cell.ids.were.found"))
                        if mesh.cell_ids and (not set(cell_ids).issubset(mesh.cell_ids)):
                            messages.append(
                                msg("text.contains.cell.ids.that.are.not.in.the.current.mesh")
                            )
                    if is_porosity and finite and (minimum < 0 or maximum > 1):
                        messages.append(msg("text.porosity.contains.values.outside.0.1"))
                    if is_permeability and finite and (minimum <= 0):
                        messages.append(msg("text.permeability.contains.nonpositive.values"))
                if (
                    mesh.status == "available"
                    and path.stat().st_mtime
                    < (_resolve(project, mesh.mesh_path) or path).stat().st_mtime
                ):
                    status = "warning"
                    messages.append(
                        msg("text.the.dataset.file.predates.the.current.mesh.and.may.be.outdated")
                    )
                if any(
                    message.key
                    != "text.the.dataset.file.predates.the.current.mesh.and.may.be.outdated"
                    for message in messages
                ):
                    status = "invalid"
            except OSError as exc:
                status = "invalid"
                messages = [msg("spatial.read_dataset", reason=exc)]
            except (ValueError, TypeError) as exc:
                status = "invalid"
                messages = [msg("spatial.invalid_dataset", reason=exc)]
        message = join_text("; ", messages)
        summary = SpatialDatasetSummary(
            property_name,
            object_id,
            relative,
            hdf5_path,
            count,
            minimum,
            maximum,
            unit,
            status,
            message,
            dtype,
            missing_count,
        )
        summaries.append(summary)
        if status == "invalid":
            issues.append(DataCheckIssue("error", join_text("", [object_id, ": ", message])))
        elif status == "missing":
            issues.append(DataCheckIssue("warning", join_text("", [object_id, ": ", message])))
        elif status == "warning":
            issues.append(DataCheckIssue("warning", join_text("", [object_id, ": ", message])))
    return (summaries, issues)


def _boundary_name(path: Path) -> str:
    stem = path.stem
    for prefix in ("pboundary_", "boundary_"):
        if stem.casefold().startswith(prefix):
            return stem[len(prefix) :]
    return stem


def _boundary_files(project: Path, octree: Path, manifest: ContinuumManifest):
    return dict(manifest.boundary_regions)


def _read_boundary(path: Path):
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip() and (not line.lstrip().startswith("#"))
    ]
    if not lines:
        raise EOFError(msg("text.boundary.file.is.empty"))
    declared = None
    ids: list[int] = []
    for index, line in enumerate(lines):
        fields = line.replace(",", " ").split()
        if len(fields) >= 2 and fields[0].upper() in {"CONNECTIONS", "CELLS", "FACES", "ZONE"}:
            try:
                declared = int(fields[1])
            except ValueError:
                pass
            continue
        if index == 0 and len(fields) == 1:
            try:
                declared = int(fields[0])
                continue
            except ValueError:
                pass
        try:
            ids.append(int(fields[0]))
        except (ValueError, IndexError):
            continue
    count = declared if declared is not None else len(ids)
    if count < 1:
        raise ValueError(msg("text.no.boundary.faces.were.identified"))
    return (count, ids)


def _inspect_boundaries(project, octree, manifest, mesh):
    discovered = _boundary_files(project, octree, manifest)
    summaries: list[BoundarySurfaceSummary] = []
    issues: list[DataCheckIssue] = []
    ordered = list(_STANDARD_BOUNDARIES) + sorted(
        (name for name in discovered if name not in _STANDARD_BOUNDARIES)
    )
    mesh_path = _resolve(project, mesh.mesh_path)
    for name in ordered:
        relative = discovered.get(name, "")
        path = _resolve(project, relative)
        status = "available"
        message = ""
        count = 0
        if not _safe_is_file(path):
            status = "missing"
            message = msg("text.boundary.face.file.not.found")
        else:
            try:
                count, ids = _read_boundary(path)
                if ids and mesh.cell_ids and (not set(ids).issubset(mesh.cell_ids)):
                    status = "invalid"
                    message = msg("text.boundary.faces.do.not.match.the.current.mesh")
                elif mesh_path is not None and path.stat().st_mtime < mesh_path.stat().st_mtime:
                    status = "warning"
                    message = msg(
                        "text.the.boundary.face.file.predates.the.current.mesh.and.may.be.outdated"
                    )
            except EOFError:
                status = "empty"
                message = msg("text.boundary.face.file.is.empty")
            except (OSError, ValueError) as exc:
                status = "invalid"
                message = msg("spatial.invalid_boundary", reason=exc)
        summaries.append(
            BoundarySurfaceSummary(
                _STANDARD_BOUNDARIES.get(name, name), name, relative, count, status, message
            )
        )
        if status in {"invalid", "empty"}:
            issues.append(DataCheckIssue("error", join_text("", [name, ": ", message])))
        elif status == "warning":
            issues.append(DataCheckIssue("warning", join_text("", [name, ": ", message])))
    return (summaries, issues)
