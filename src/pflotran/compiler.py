import posixpath
from copy import deepcopy

from fracrtm.config.contracts import unassigned_boundary_placeholder
from fracrtm.messages import join_text
from fracrtm.messages import message as msg
from fracrtm.pflotran.input_sections import (
    PFLOTRANChemistryWriter,
    PFLOTRANOutputWriter,
    PFLOTRANPropertiesWriter,
    PFLOTRANRegionConditionsWriter,
    PFLOTRANSpatialAssignmentWriter,
    PFLOTRANTimeNumericsWriter,
)


def render_project_relative_path(project_relative: str, input_directory: str = "pflotran") -> str:
    return posixpath.relpath(project_relative, start=input_directory)


class PFLOTRANCompiler:
    def build(self, config, *, input_directory: str = "pflotran"):
        report = config.validate()
        if not report.valid:
            raise ValueError(
                msg("validation.model_errors.heading")
                + join_text("\n", (x.message for x in report.errors))
            )
        resolved = deepcopy(config)
        self._resolve_paths(resolved, input_directory)
        process = [
            "SIMULATION",
            "  SIMULATION_TYPE SUBSURFACE",
            "  PROCESS_MODELS",
            "    SUBSURFACE_FLOW flow",
            "      MODE RICHARDS",
        ]
        if resolved.simulation.flow_state == "steady_state":
            process.extend(["      OPTIONS", "        STEADY_STATE", "      /"])
        process.append("    /")
        if resolved.simulation.uses_transport():
            process.extend(["    SUBSURFACE_TRANSPORT transport", "      MODE GIRT", "    /"])
        process.extend(["  /", "END"])
        gravity = self._gravity(resolved.grid.gravity)
        blocks = [
            "\n".join(process),
            "SUBSURFACE",
            "\n".join(
                [
                    "GRID",
                    f"  TYPE UNSTRUCTURED_EXPLICIT {resolved.grid.file}",
                    "  GRAVITY " + " ".join((_number(value) for value in gravity)),
                    "END",
                ]
            ),
        ]
        output_writer = PFLOTRANOutputWriter()
        blocks.extend(
            (
                x
                for x in (
                    PFLOTRANPropertiesWriter().build(resolved),
                    PFLOTRANChemistryWriter().build(resolved),
                    PFLOTRANTimeNumericsWriter().build(resolved),
                    PFLOTRANRegionConditionsWriter().build(resolved),
                    output_writer.build_observations(resolved),
                    PFLOTRANSpatialAssignmentWriter().build(resolved),
                    output_writer.build(resolved),
                )
                if x
            )
        )
        blocks.append("END_SUBSURFACE")
        return "\n\n".join(blocks) + "\n"

    @staticmethod
    def _gravity(gravity):
        if gravity.mode == "off":
            return [0.0, 0.0, 0.0]
        if gravity.mode == "standard":
            return [0.0, 0.0, -9.8068]
        return gravity.vector

    @staticmethod
    def _resolve_paths(config, input_directory):

        def convert(value):
            return render_project_relative_path(value, input_directory)

        config.grid.file = convert(config.grid.file)
        if config.chemistry.database.relative_path:
            config.chemistry.database.relative_path = convert(
                config.chemistry.database.relative_path
            )
        for dataset in config.datasets:
            dataset.file = convert(dataset.file)
        for region in config.regions:
            if region.region_type == "FILE" and not unassigned_boundary_placeholder(config, region):
                region.file_path = convert(region.file_path)
        mapping = config.spatial_assignment.material_mapping
        if mapping and mapping.file_path:
            mapping.file_path = convert(mapping.file_path)


def _number(value):
    return f"{float(value):.12E}"
