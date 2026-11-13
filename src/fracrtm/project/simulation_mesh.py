from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .resources import BoundaryArtifact, ProjectArtifacts


@dataclass(frozen=True)
class SimulationMeshArtifacts:
    project_root: Path
    grid: Path | None
    visualization_geometry: Path | None
    materials: Path | None
    permeability: Path | None
    porosity: Path | None
    boundaries: dict[str, BoundaryArtifact] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        # Display geometry is optional for simulation.
        return all((self.grid, self.materials, self.permeability, self.porosity))


class ModelSourceProvider(Protocol):
    source_id: str
    display_name: str

    def simulation_mesh(self, project_directory: str | Path) -> SimulationMeshArtifacts: ...

    def spatial_structure(self, project_directory: str | Path) -> Path | None: ...


class DfnUdfmProvider:
    source_id = "dfn_udfm"
    display_name = "DFN–UDFM"

    def simulation_mesh(self, project_directory: str | Path) -> SimulationMeshArtifacts:
        discovered = ProjectArtifacts.discover(project_directory)
        root = discovered.project_root

        def resolve(value):
            return root / value if value else None

        octree = discovered.octree
        return SimulationMeshArtifacts(
            root,
            resolve(octree.full_mesh),
            resolve(octree.visualization_mesh),
            resolve(octree.materials),
            resolve(octree.permeability),
            resolve(octree.porosity),
            dict(octree.boundaries),
            {"provider": self.source_id, "source_name": self.display_name},
        )

    def spatial_structure(self, project_directory: str | Path) -> Path | None:
        discovered = ProjectArtifacts.discover(project_directory)
        return (
            discovered.project_root / discovered.dfn.polygons if discovered.dfn.polygons else None
        )


DEFAULT_MODEL_SOURCE = DfnUdfmProvider()
