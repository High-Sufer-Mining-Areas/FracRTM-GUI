from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fracrtm.messages import message as msg
from fracrtm.project.simulation_mesh import DEFAULT_MODEL_SOURCE, ModelSourceProvider


@dataclass(frozen=True)
class VisualizationArtifacts:
    project_root: Path
    dfn_polygons: Path | None
    octree_mesh: Path | None
    uge_mesh: Path | None
    permeability: Path | None
    porosity: Path | None
    materials: Path | None
    raw_result_directory: Path
    visualization_directory: Path
    result_collection: Path


class VisualizationArtifactResolver:
    def resolve(
        self,
        project_directory: str | Path,
        provider: ModelSourceProvider = DEFAULT_MODEL_SOURCE,
        *,
        run_id=None,
    ) -> VisualizationArtifacts:
        project = Path(project_directory).expanduser().resolve()
        from fracrtm.project.runs import get_run

        if not run_id:
            raise ValueError(msg("text.no.run.records"))
        task = get_run(project, run_id)
        project = task.snapshot_directory
        simulation_mesh = provider.simulation_mesh(project)
        grid = simulation_mesh.grid
        config_path = project / "pflotran" / "model_config.json"
        if config_path.is_file():
            from fracrtm.config.pflotran import PFLOTRANConfig

            from .spatial_mapping import safe_resource

            config = PFLOTRANConfig.load_json(config_path)
            if config.grid.file:
                grid = safe_resource(project, config.grid.file)

        output = task.output_directory
        visual = (
            task.directory / "visualization"
            if task.record_path.name == "result.json"
            else output / "visualization"
        )
        return VisualizationArtifacts(
            project,
            provider.spatial_structure(project),
            simulation_mesh.visualization_geometry,
            grid,
            simulation_mesh.permeability,
            simulation_mesh.porosity,
            simulation_mesh.materials,
            output,
            visual,
            visual / "model.pvd",
        )
