from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from fracrtm.config.contracts import unassigned_boundary_placeholder
from fracrtm.config.pflotran import ValidationIssue, ValidationReport
from fracrtm.messages import exception_text, join_text
from fracrtm.messages import message as msg
from fracrtm.visualization.spatial_mapping import (
    read_hdf5_values,
    read_uge,
    resolve_region_points,
    safe_resource,
)


@dataclass(frozen=True)
class SpatialValidationReport(ValidationReport):
    def raise_for_errors(self):
        if not self.valid:
            raise ValueError(join_text("\n", (issue.message for issue in self.errors)))


def validate_spatial_resources(project, config):
    issues = []
    root = Path(project).resolve() if project is not None else None

    def add(path, file, reason, *, code="spatial.resource", severity="error"):
        issues.append(
            ValidationIssue(
                severity,
                code,
                path,
                msg(
                    "validation.resource_detail",
                    path=path,
                    file=file or msg("text.not.set"),
                    reason=reason,
                ),
            )
        )

    def resource(relative, field):
        if root is None:
            add(field, relative, msg("text.project.directory.is.not.set"))
            return None
        try:
            path = safe_resource(root, relative)
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError(msg("text.referenced.file.is.missing.or.empty"))
            return path
        except (OSError, ValueError) as exc:
            add(field, relative, exception_text(exc))
            return None

    grid = resource(config.grid.file, "grid.file")
    volumes = None
    if grid is not None:
        try:
            volumes = read_uge(grid)
        except (OSError, ValueError, TypeError, OverflowError) as exc:
            add("grid.file", config.grid.file, exception_text(exc), code="spatial.grid")
    material_mapping = config.spatial_assignment.material_mapping
    material_file = material_mapping.file_path if material_mapping else ""
    material_path = resource(material_file, "spatial_assignment.material_mapping.file_path")
    if material_path is not None and volumes is not None:
        try:
            values = read_hdf5_values(
                material_path, "Materials/Material Ids", "Materials/Cell Ids", volumes.ids
            )
            if values.ndim != 1 or np.any(values != np.floor(values)) or np.any(values < 0):
                raise ValueError(
                    msg("text.material.ids.must.be.a.rank.1.nonnegative.integer.array")
                )
            actual = set(values.astype(np.int64).tolist())
            declared = {int(item.material_id) for item in config.materials}
            unknown = sorted(actual - declared - {0})
            if unknown:
                raise ValueError(
                    msg(
                        "diagnostic.spatial_validation.material.ids.have.no.material.property.definition.unknown",
                        unknown=unknown,
                    )
                )
            if 0 in actual:
                add(
                    "spatial_assignment.material_mapping",
                    material_file,
                    msg(
                        "diagnostic.spatial_validation.value.control.volumes.have.inactive.material.id.0",
                        value=int(np.count_nonzero(values == 0)),
                    ),
                    severity="warning",
                    code="spatial.inactive",
                )
        except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
            add(
                "spatial_assignment.material_mapping",
                material_file,
                exception_text(exc),
                code="spatial.material_ids",
            )
    property_roles = {}
    for material in config.materials:
        if material.porosity.mode == "dataset":
            property_roles.setdefault(material.porosity.dataset_id, set()).add("porosity")
        permeability = material.permeability
        if permeability.mode == "dataset":
            references = (
                [permeability.dataset_x, permeability.dataset_y, permeability.dataset_z]
                if permeability.anisotropic_dataset
                else [permeability.dataset_id]
            )
            for reference in references:
                property_roles.setdefault(reference, set()).add("permeability")
    for index, definition in enumerate(config.datasets):
        field = join_text("", ["datasets[", index, "](", definition.id, ")"])
        path = resource(definition.file, field + ".file")
        if path is None or volumes is None:
            continue
        dataset_name = definition.hdf5_dataset_name or definition.name
        try:
            values = read_hdf5_values(path, dataset_name, None, volumes.ids)
            if values.ndim != 1:
                raise ValueError(
                    msg(
                        "diagnostic.spatial_validation.hdf5.dataset.dataset.name.must.be.a.single.scalar.per.cell.id",
                        dataset_name=f"{dataset_name!r}",
                        shape=values.shape,
                    )
                )
            roles = property_roles.get(definition.id, set())
            if "porosity" in roles and (np.any(values < 0) or np.any(values > 1)):
                raise ValueError(msg("text.porosity.values.must.be.finite.and.within.0.1"))
            if "permeability" in roles and np.any(values <= 0):
                raise ValueError(msg("text.permeability.values.must.be.finite.and.positive"))
        except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
            add(
                field + ".hdf5_dataset_name",
                definition.file,
                msg("validation.dataset_detail", dataset=repr(dataset_name), reason=exc),
                code="spatial.dataset",
            )
    for index, region in enumerate(config.regions):
        if unassigned_boundary_placeholder(config, region):
            continue
        field = join_text("", ["regions[", index, "](", region.name, ")"])
        relative = region.file_path if region.region_type == "FILE" else config.grid.file
        if (
            region.region_type == "FILE"
            and resource(region.file_path, field + ".file_path") is None
        ):
            continue
        if region.region_type in ("COORDINATE", "COORDINATES") and region.coordinate_unit != "m":
            add(
                field + ".coordinate_unit",
                relative,
                msg(
                    "diagnostic.spatial_validation.unit.coordinate.unit.is.not.emitted.by.the.current.pflotran.region.writer",
                    coordinate_unit=f"{region.coordinate_unit!r}",
                ),
                code="spatial.region_unit",
            )
            continue
        if volumes is None:
            continue
        try:
            selection = resolve_region_points(root, region, volumes)
            if region.region_type == "COORDINATE":
                coordinate = np.asarray(region.coordinate, dtype=float)
                if np.any(coordinate < volumes.points.min(axis=0)) or np.any(
                    coordinate > volumes.points.max(axis=0)
                ):
                    raise ValueError(
                        msg(
                            "text.coordinate.is.outside.the.computational.control.volume.coordinate.range"
                        )
                    )
            if not len(selection.points):
                raise ValueError(msg("text.region.selection.is.empty"))
        except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
            add(field, relative, exception_text(exc), code="spatial.region")
    if config.simulation.uses_transport():
        resource(config.chemistry.database.relative_path, "chemistry.database.relative_path")
    return SpatialValidationReport(tuple(issues))
