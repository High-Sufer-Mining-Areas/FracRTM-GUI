from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fracrtm.messages import message as msg
from fracrtm.messages import prepare_json


@dataclass
class ContinuumConfig:
    schema_version: str = "0.1"
    base_cell_size: float | None = 10.0
    refinement_levels: int | None = 2
    output_directory_name: str = "octree"
    matrix_permeability: float | None = 1e-18
    matrix_porosity: float | None = 0.01
    boundary_area_mode: str = "tool_default"
    boundary_cell_area: float | None = None

    def boundary_identity(self) -> dict[str, Any]:
        return {
            "boundary_area_mode": self.boundary_area_mode,
            "boundary_cell_area": self.boundary_cell_area
            if self.boundary_area_mode == "custom"
            else None,
        }

    def scientific_dict(self) -> dict[str, Any]:
        return {**self.to_dict(), **self.boundary_identity()}

    def boundary_errors(self) -> list[str]:
        from .contracts import positive

        errors = []
        if self.boundary_area_mode not in ("tool_default", "custom"):
            errors.append(msg("boundary_area.invalid_mode"))
        if (
            self.boundary_cell_area is not None or self.boundary_area_mode == "custom"
        ) and not positive(self.boundary_cell_area):
            errors.append(msg("boundary_area.invalid_value"))
        return errors

    @property
    def minimum_cell_size(self) -> float | None:
        if self.base_cell_size is None or self.refinement_levels is None:
            return None
        return self.base_cell_size / 2**self.refinement_levels

    def validate(self) -> list[str]:
        from .contracts import numeric_type_issues

        errors = self.boundary_errors() + [detail for _, detail in numeric_type_issues(self)]
        if errors:
            return errors
        if (
            self.base_cell_size is None
            or not math.isfinite(self.base_cell_size)
            or self.base_cell_size <= 0
        ):
            errors.append(msg("validation.octree.level.zero.cell.size.must.be.greater.than.0"))
        if (
            not isinstance(self.refinement_levels, int)
            or isinstance(self.refinement_levels, bool)
            or self.refinement_levels < 1
        ):
            errors.append(msg("validation.octree.refinement.level.must.be.at.least.1"))
        name = self.output_directory_name.strip()
        if not name or name in {".", ".."} or "/" in name or ("\\" in name):
            errors.append(
                msg("validation.octree.output.directory.must.be.a.single.safe.directory.name")
            )
        if (
            self.matrix_permeability is None
            or not math.isfinite(self.matrix_permeability)
            or self.matrix_permeability <= 0
        ):
            errors.append(msg("validation.matrix.permeability.must.be.greater.than.0"))
        if (
            self.matrix_porosity is None
            or not math.isfinite(self.matrix_porosity)
            or (not 0 <= self.matrix_porosity <= 1)
        ):
            errors.append(msg("validation.matrix.porosity.must.be.between.0.and.1"))
        return errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save_json(self, filepath: str | Path) -> None:
        from fracrtm.project.storage import atomic_json

        errors = self.boundary_errors()
        if errors:
            raise ValueError("\n".join(str(e) for e in errors))
        atomic_json(Path(filepath), prepare_json(self.to_dict()))

    @classmethod
    def load_json(cls, filepath: str | Path) -> "ContinuumConfig":
        with Path(filepath).open("r", encoding="utf-8") as stream:
            return cls.from_dict(json.load(stream))

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ContinuumConfig":
        from .contracts import check_serialized_numbers

        data = data or {}
        check_serialized_numbers(data)
        boundary = cls(
            boundary_area_mode=data.get("boundary_area_mode", "tool_default"),
            boundary_cell_area=data.get("boundary_cell_area"),
        )
        if boundary.boundary_errors():
            raise ValueError("\n".join(str(e) for e in boundary.boundary_errors()))
        return cls(
            boundary_area_mode=boundary.boundary_area_mode,
            boundary_cell_area=boundary.boundary_cell_area,
            schema_version=str(data.get("schema_version", "0.1")),
            base_cell_size=None
            if data.get("base_cell_size", 10.0) is None
            else float(data.get("base_cell_size", 10.0)),
            refinement_levels=None
            if data.get("refinement_levels", 2) is None
            else int(data.get("refinement_levels", 2)),
            output_directory_name=str(data.get("output_directory_name", "octree")),
            matrix_permeability=None
            if data.get("matrix_permeability", 1e-18) is None
            else float(data.get("matrix_permeability", 1e-18)),
            matrix_porosity=None
            if data.get("matrix_porosity", 0.01) is None
            else float(data.get("matrix_porosity", 0.01)),
        )
