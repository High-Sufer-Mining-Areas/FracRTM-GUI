from __future__ import annotations

import math
import re
from pathlib import Path, PurePosixPath
from typing import Any

from fracrtm.messages import Message, diagnostic_message, exception_text, join_text
from fracrtm.messages import message as msg

from .contracts import (
    CONSTRAINT_TYPES,
    CURVE_MODELS,
    FLOW_FORMS_BY_USAGE,
    MINERAL_SURFACE_UNITS,
    PERMEABILITY_FUNCTIONS,
    TRANSPORT_USAGE,
    finite_real,
    flow_parameter_errors,
    numeric_type_issues,
    positive,
    strict_integer,
    unassigned_boundary_placeholder,
)
from .pflotran import (
    PFLOTRAN_SCHEMA_VERSION,
    TIME_UNITS_SECONDS,
    OutputScheduleConfig,
    PermeabilitySource,
    PFLOTRANConfig,
    PropertySource,
    ValidationIssue,
    ValidationReport,
)


class ReferenceValidator:
    def validate(
        self, c: PFLOTRANConfig, project_directory: str | Path | None = None
    ) -> ValidationReport:
        issues: list[ValidationIssue] = []

        def error(code, path, message):
            return issues.append(
                ValidationIssue(
                    "error",
                    code,
                    path,
                    message if isinstance(message, Message) else diagnostic_message(message),
                )
            )

        def warning(code, path, message):
            return issues.append(
                ValidationIssue(
                    "warning",
                    code,
                    path,
                    message if isinstance(message, Message) else diagnostic_message(message),
                )
            )

        for path, text in numeric_type_issues(c):
            error("field.type", path, text)
        if issues:
            return ValidationReport(tuple(issues))
        if c.schema_version != PFLOTRAN_SCHEMA_VERSION:
            error(
                "schema.version",
                "schema_version",
                msg("validation.configuration.version.must.be.2.0"),
            )
        if not strict_integer(c.runtime.processes) or c.runtime.processes < 1:
            error(
                "runtime.processes",
                "runtime.processes",
                msg("validation.mpi.process.count.must.be.at.least.1"),
            )
        if c.simulation.task_type not in {"flow", "conservative_transport", "reactive_transport"}:
            error(
                "simulation.task",
                "simulation.task_type",
                msg("validation.unsupported.simulation.task"),
            )
        if c.simulation.flow_state not in {"transient", "steady_state"}:
            error(
                "simulation.flow_state",
                "simulation.flow_state",
                msg("validation.flow.state.must.be.transient.or.steady.state"),
            )
        if c.simulation.saturation_state not in {"saturated", "variably_saturated"}:
            error(
                "simulation.saturation",
                "simulation.saturation_state",
                msg("validation.saturation.state.must.be.fully.or.variably.saturated"),
            )
        if c.simulation.flow_state == "steady_state" and c.simulation.uses_transport():
            error(
                "simulation.steady_transport",
                "simulation.flow_state",
                msg(
                    "text.pflotran.time.steady.state.does.not.advance.time.this.workflow.permits.steady.state.only"
                ),
            )
        if c.simulation.flow_mode != "RICHARDS":
            error(
                "simulation.flow_mode",
                "simulation.flow_mode",
                msg("text.this.workflow.supports.only.richards.flow"),
            )
        if c.simulation.uses_transport() and c.simulation.transport_mode != "GIRT":
            error(
                "simulation.transport_mode",
                "simulation.transport_mode",
                msg("text.this.transport.workflow.supports.only.girt"),
            )
        if not _positive(c.time.final_time):
            error("time.final", "time.final_time", msg("validation.final.time.must.be.positive"))
        if not _positive(c.time.initial_timestep):
            error(
                "time.initial",
                "time.initial_timestep",
                msg("validation.initial.time.step.must.be.positive"),
            )
        if not _positive(c.time.maximum_timestep):
            error(
                "time.maximum",
                "time.maximum_timestep",
                msg("validation.maximum.time.step.must.be.positive"),
            )
        if any(
            unit in {"y", "month"}
            for unit in (
                c.time.final_unit,
                c.time.initial_timestep_unit,
                c.time.maximum_timestep_unit,
                c.output.schedule.interval_unit,
                c.output.schedule.specified_times_unit,
            )
        ):
            warning("time.native_calendar", "time", msg("repair.calendar_unverified"))
        time_units = TIME_UNITS_SECONDS
        units = (c.time.final_unit, c.time.initial_timestep_unit, c.time.maximum_timestep_unit)
        if any((unit not in time_units for unit in units)):
            error("time.unit", "time", msg("validation.time.unit.must.be.s.min.h.d.month.or.y"))
        elif all(
            (
                _positive(value)
                for value in (c.time.final_time, c.time.initial_timestep, c.time.maximum_timestep)
            )
        ):
            final_seconds = c.time.final_time * time_units[c.time.final_unit]
            initial_seconds = c.time.initial_timestep * time_units[c.time.initial_timestep_unit]
            maximum_seconds = c.time.maximum_timestep * time_units[c.time.maximum_timestep_unit]
            if initial_seconds > maximum_seconds:
                error(
                    "time.order",
                    "time.initial_timestep",
                    msg("validation.initial.time.step.must.not.exceed.the.maximum.time.step"),
                )
            if maximum_seconds > final_seconds:
                error(
                    "time.order",
                    "time.maximum_timestep",
                    msg("validation.maximum.time.step.must.not.exceed.simulation.duration"),
                )
        if c.grid.grid_type != "unstructured_explicit":
            error(
                "grid.type",
                "grid.grid_type",
                msg("text.only.unstructured.explicit.grids.are.currently.supported"),
            )
        _safe_path(c.grid.file, "grid.file", error)
        gravity = c.grid.gravity
        if gravity.mode not in {"standard", "off", "custom"}:
            error(
                "grid.gravity_mode",
                "grid.gravity.mode",
                msg("validation.gravity.mode.must.be.standard.off.or.custom"),
            )
        if gravity.mode == "custom" and (
            len(gravity.vector) != 3
            or not all((math.isfinite(float(value)) for value in gravity.vector))
        ):
            error(
                "grid.gravity_vector",
                "grid.gravity.vector",
                msg("text.custom.gravity.requires.three.finite.values"),
            )
        diffusion = c.fluid_properties.diffusion_coefficient
        if c.simulation.uses_transport() and (not _positive(diffusion)):
            error(
                "fluid.diffusion",
                "fluid_properties.diffusion_coefficient",
                msg("text.transport.requires.positive.molecular.diffusivity"),
            )
        elif diffusion is not None and (not _positive(diffusion)):
            error(
                "fluid.diffusion",
                "fluid_properties.diffusion_coefficient",
                msg("validation.molecular.diffusivity.must.be.positive.when.specified"),
            )
        if c.simulation.uses_transport():
            _safe_path(
                c.chemistry.database.relative_path, "chemistry.database.relative_path", error
            )
        if c.numerical_methods.mode not in {"recommended", "custom"}:
            error(
                "numerics.mode",
                "numerical_methods.mode",
                msg("validation.invalid.solver.parameter.mode"),
            )
        if c.numerical_methods.mode == "custom":
            enabled_processes = [("flow", c.numerical_methods.flow)]
            if c.simulation.uses_transport():
                enabled_processes.append(("transport", c.numerical_methods.transport))
            for process_name, process in enabled_processes:
                base = join_text("", ["numerical_methods.", process_name])
                newton = process.newton
                linear = process.linear
                if newton.maximum_iterations is not None and newton.maximum_iterations < 1:
                    error(
                        "numerics.iterations",
                        base + ".newton.maximum_iterations",
                        msg("validation.maximum.newton.iterations.must.be.at.least.1"),
                    )
                if newton.snes_type is not None and newton.snes_type not in {
                    "LINE_SEARCH",
                    "TRUST_REGION",
                }:
                    error(
                        "numerics.snes_type",
                        base + ".newton.snes_type",
                        msg("validation.snes.type.must.be.line.search.or.trust.region"),
                    )
                if any(
                    (
                        not _positive(value)
                        for value in (
                            newton.rtol,
                            newton.atol,
                            newton.itol,
                            newton.stol,
                            newton.dtol,
                        )
                        if value is not None
                    )
                ):
                    error(
                        "numerics.newton",
                        base + ".newton",
                        msg("validation.specified.newton.tolerances.must.be.finite.and.positive"),
                    )
                if linear.solver_type not in {"BCGS", "GMRES", "IBCGS", "DIRECT", "ITERATIVE"}:
                    error(
                        "numerics.linear_solver",
                        base + ".linear.solver_type",
                        msg("validation.invalid.linear.solver"),
                    )
                if linear.preconditioner is not None and linear.preconditioner not in {
                    "NONE",
                    "ILU",
                    "LU",
                    "BJACOBI",
                    "ASM",
                    "HYPRE",
                    "CPR",
                }:
                    error(
                        "numerics.preconditioner",
                        base + ".linear.preconditioner",
                        msg("validation.invalid.preconditioner"),
                    )
                if any(
                    (
                        not _positive(value)
                        for value in (linear.rtol, linear.atol, linear.dtol)
                        if value is not None
                    )
                ):
                    error(
                        "numerics.linear",
                        base + ".linear",
                        msg("validation.specified.linear.tolerances.must.be.finite.and.positive"),
                    )
        collections = {
            "datasets": c.datasets,
            "materials": c.materials,
            "characteristic_curves": c.characteristic_curves,
            "regions": c.regions,
            "flow_conditions": c.flow_conditions,
            "chemical_constraints": c.chemical_constraints,
            "transport_conditions": c.transport_conditions,
            "spatial_assignments": c.spatial_assignment.condition_assignments,
        }
        for label, objects in collections.items():
            _unique(objects, label, error)
        all_condition_names = [item.name for item in c.flow_conditions] + [
            item.name for item in c.transport_conditions
        ]
        if len(set(all_condition_names)) != len(all_condition_names):
            error(
                "condition.duplicate",
                "flow_conditions",
                msg(
                    "validation.flow.and.transport.condition.names.must.be.unique.within.the.project"
                ),
            )
        dataset_ids = {x.id for x in c.datasets}
        mineral_ids = set(c.chemistry.selected_minerals)
        curve_ids = {x.id for x in c.characteristic_curves}
        _material_ids = {x.id for x in c.materials}
        region_ids = {x.id for x in c.regions}
        flow_ids = {x.id for x in c.flow_conditions}
        constraint_ids = {x.id for x in c.chemical_constraints}
        transport_ids = {x.id for x in c.transport_conditions}
        flow_usage = {x.name: x.usage for x in c.flow_conditions}
        transport_usage = {x.name: x.usage for x in c.transport_conditions}
        for index, dataset in enumerate(c.datasets):
            if not _valid_object_name(dataset.id):
                error("dataset.id", f"datasets[{index}].id", msg("repair.solver_identifier"))
            _safe_path(dataset.file, join_text("", ["datasets[", index, "].file"]), error)
        used_numeric_ids: set[int] = set()
        if not c.materials:
            error("material.empty", "materials", msg("repair.material_required"))
        for index, material in enumerate(c.materials):
            path = join_text("", ["materials[", index, "]"])
            if not _valid_object_name(material.id):
                error("material.id", path + ".id", msg("repair.solver_identifier"))
            if (
                not strict_integer(material.material_id)
                or material.material_id <= 0
                or material.material_id in used_numeric_ids
            ):
                error(
                    "material.numeric_id",
                    path + ".material_id",
                    msg("validation.material.ids.must.be.unique.positive.integers"),
                )
            used_numeric_ids.add(material.material_id)
            _property_source(material.porosity, dataset_ids, path + ".porosity", error)
            _permeability_source(material.permeability, dataset_ids, path + ".permeability", error)
            if (
                material.characteristic_curve_id
                and material.characteristic_curve_id not in curve_ids
            ):
                error(
                    "reference.curve",
                    path + ".characteristic_curve_id",
                    msg("text.referenced.characteristic.curve.does.not.exist"),
                )
            elif not material.characteristic_curve_id:
                error(
                    "material.curve",
                    path + ".characteristic_curve_id",
                    msg("text.richards.materials.require.characteristic.curves"),
                )
            if c.simulation.uses_transport():
                if material.tortuosity is not None and not positive(material.tortuosity):
                    error(
                        "material.tortuosity",
                        path + ".tortuosity",
                        msg("validation.tortuosity.must.be.positive"),
                    )
                dispersivities = (
                    material.longitudinal_dispersivity,
                    material.transverse_dispersivity_horizontal,
                    material.transverse_dispersivity_vertical,
                )
                if any(not finite_real(value) or value < 0 for value in dispersivities):
                    error(
                        "material.dispersivity",
                        path,
                        msg("validation.dispersivity.must.not.be.negative"),
                    )
        for index, curve in enumerate(c.characteristic_curves):
            if curve.model not in CURVE_MODELS:
                error(
                    "curve.model",
                    join_text("", ["characteristic_curves[", index, "].model"]),
                    msg("validation.unsupported.characteristic.curve.type"),
                )
            if curve.model != "default" and (
                not _positive(curve.alpha) or not _positive(curve.m_or_lambda)
            ):
                error(
                    "curve.parameters",
                    join_text("", ["characteristic_curves[", index, "]"]),
                    msg("text.non.default.curves.require.positive.alpha.and.m.lambda"),
                )
            if not 0 <= curve.liquid_residual_saturation < 1:
                error(
                    "curve.residual",
                    join_text(
                        "", ["characteristic_curves[", index, "].liquid_residual_saturation"]
                    ),
                    msg("validation.residual.saturation.must.be.in.0.1"),
                )
            if curve.maximum_capillary_pressure is not None and (
                not _positive(curve.maximum_capillary_pressure)
            ):
                error(
                    "curve.max_capillary_pressure",
                    join_text(
                        "", ["characteristic_curves[", index, "].maximum_capillary_pressure"]
                    ),
                    msg("validation.maximum.capillary.pressure.must.be.positive"),
                )
            if curve.model == "van_genuchten" and (
                not positive(curve.m_or_lambda) or curve.m_or_lambda >= 1
            ):
                error("curve.m", f"characteristic_curves[{index}].m_or_lambda", msg("repair.vg_m"))
            if (
                curve.model != "default"
                and curve.permeability_function not in PERMEABILITY_FUNCTIONS
            ):
                error(
                    "curve.permeability_function",
                    f"characteristic_curves[{index}].permeability_function",
                    msg("repair.curve_function"),
                )
            family = "VG" if curve.model == "van_genuchten" else "BC"
            if (
                curve.model != "default"
                and "_" in curve.permeability_function
                and f"_{family}_" not in curve.permeability_function
            ):
                error(
                    "curve.permeability_function",
                    f"characteristic_curves[{index}].permeability_function",
                    msg("repair.curve_function"),
                )
        primary_values = c.chemistry.aqueous_species.primary_species
        secondary_values = c.chemistry.aqueous_species.secondary_species
        gas_values = c.chemistry.gas_species.passive_gases
        mineral_values = c.chemistry.selected_minerals
        primary = set(primary_values)
        if c.simulation.uses_transport() and "H+" not in primary_values:
            for path, output in (
                ("output.chemistry_variables", c.output.chemistry_variables),
                (
                    "output.observation.chemistry_variables",
                    c.output.observation.chemistry_variables
                    if c.output.observation.enabled
                    else None,
                ),
            ):
                if output is not None and output.ph:
                    error("output.ph_species", path + ".ph", msg("repair.ph_species_required"))
        secondary = set(secondary_values)
        if c.simulation.uses_transport() and (not primary):
            error(
                "chemistry.primary",
                "chemistry.aqueous_species.primary_species",
                msg("validation.at.least.one.primary.species.is.required"),
            )
        for values, path, label in (
            (
                primary_values,
                "chemistry.aqueous_species.primary_species",
                msg("chemistry.primary_species"),
            ),
            (
                secondary_values,
                "chemistry.aqueous_species.secondary_species",
                msg("text.secondary.species"),
            ),
            (gas_values, "chemistry.gas_species.passive_gases", msg("text.gas.species")),
            (mineral_values, "chemistry.selected_minerals", msg("text.mineral")),
        ):
            if len(set(values)) != len(values):
                error("chemistry.duplicate", path, msg("validation.duplicate_name", category=label))
            if any((not str(value).strip() for value in values)):
                error("chemistry.empty_name", path, msg("validation.empty_name", category=label))
        overlap = primary & secondary
        if overlap:
            error(
                "chemistry.primary_secondary_overlap",
                "chemistry.aqueous_species",
                msg("text.a.species.cannot.belong.to.both.primary.species.and.secondary.species")
                + join_text(", ", sorted(overlap)),
            )
        if c.simulation.uses_transport() and project_directory is not None:
            self._validate_database_references(c, Path(project_directory), error)
        kinetic_names = [item.mineral_name for item in c.chemistry.mineral_kinetics]
        if len(set(kinetic_names)) != len(kinetic_names):
            error(
                "mineral.duplicate_kinetics",
                "chemistry.mineral_kinetics",
                msg("text.each.mineral.may.have.only.one.kinetics.definition"),
            )
        chemical_species = primary | secondary
        for index, kinetic in enumerate(c.chemistry.mineral_kinetics):
            path = join_text("", ["chemistry.mineral_kinetics[", index, "]"])
            if kinetic.mineral_name not in mineral_ids:
                error(
                    "reference.mineral",
                    path + ".mineral_name",
                    msg("validation.kinetic.minerals.must.belong.to.the.minerals.set"),
                )
            if not _positive(kinetic.rate_constant):
                error(
                    "mineral.rate",
                    path + ".rate_constant",
                    msg("validation.rate.constant.must.be.positive"),
                )
            if kinetic.surface_area_function not in {"default", "mineral_mass"}:
                error(
                    "mineral.surface_area_function",
                    path + ".surface_area_function",
                    msg("validation.surface.area.model.must.be.default.or.mineral.mass"),
                )
            if kinetic.surface_area_function == "mineral_mass" and (
                not _positive(kinetic.specific_surface_area)
            ):
                error(
                    "mineral.surface_area",
                    path + ".specific_surface_area",
                    msg(
                        "text.the.mass.specific.surface.area.model.requires.positive.specific.surface.area"
                    ),
                )
            if (
                kinetic.surface_area_function == "default"
                and kinetic.specific_surface_area is not None
            ):
                warning(
                    "mineral.surface_area_unused",
                    path + ".specific_surface_area",
                    msg("text.the.default.surface.area.model.does.not.emit.specific.surface.area"),
                )
            if kinetic.prefactor_species and kinetic.prefactor_species not in chemical_species:
                error(
                    "reference.species",
                    path + ".prefactor_species",
                    msg("validation.prefactor_species", name=kinetic.prefactor_species),
                )
        if c.simulation.uses_reactions() and (not mineral_values):
            error(
                "chemistry.minerals",
                "chemistry.selected_minerals",
                msg(
                    "text.reactive.transport.requires.at.least.one.mineral.or.another.reaction.definition"
                ),
            )
        if c.simulation.task_type == "conservative_transport" and mineral_values:
            error(
                "chemistry.conservative",
                "chemistry.selected_minerals",
                msg("text.conservative.transport.cannot.include.mineral.reactions"),
            )
        if c.chemistry.activity_coefficients not in {"default", "timestep"}:
            error(
                "chemistry.activity_coefficients",
                "chemistry.activity_coefficients",
                msg("validation.activity.coefficient.option.must.be.default.or.timestep"),
            )
        required_system_regions = {"all"}
        region_names = [item.name for item in c.regions]
        missing_system = required_system_regions - {name.casefold() for name in region_names}
        if missing_system:
            error(
                "region.system_missing",
                "regions",
                msg("text.system.regions.are.missing") + join_text(", ", sorted(missing_system)),
            )
        for index, region in enumerate(c.regions):
            if unassigned_boundary_placeholder(c, region):
                continue
            path = join_text("", ["regions[", index, "]"])
            if not _valid_object_name(region.name):
                error(
                    "region.name",
                    path + ".name",
                    msg(
                        "validation.region.names.may.contain.letters.digits.underscores.and.hyphens.and.must.not.begin.with"
                    ),
                )
            if region.region_type not in {"COORDINATE", "COORDINATES", "LIST", "FILE"}:
                error(
                    "region.type",
                    path + ".region_type",
                    msg("validation.region.type.must.be.coordinate.coordinates.list.or.file"),
                )
            elif region.region_type == "FILE":
                if not region.file_path:
                    error(
                        "region.file", path + ".file_path", msg("text.file.regions.require.a.file")
                    )
                else:
                    _safe_path(region.file_path, path + ".file_path", error)
            elif region.region_type == "COORDINATES":
                if region.bounds is None or len(region.bounds) != 6:
                    error(
                        "region.bounds",
                        path + ".bounds",
                        msg("text.coordinate.bounds.require.six.values"),
                    )
                elif any((region.bounds[i] > region.bounds[i + 3] for i in range(3))):
                    error(
                        "region.bounds_order",
                        path + ".bounds",
                        msg("validation.lower.coordinate.bounds.must.not.exceed.upper.bounds"),
                    )
            elif region.region_type == "COORDINATE" and (
                region.coordinate is None or len(region.coordinate) != 3
            ):
                error(
                    "region.coordinate",
                    path + ".coordinate",
                    msg("text.a.spatial.point.requires.three.coordinates"),
                )
            elif region.region_type == "LIST" and (not region.cell_ids):
                error(
                    "region.cells",
                    path + ".cell_ids",
                    msg("validation.cell.list.regions.must.not.be.empty"),
                )
            if region.region_type == "LIST" and (
                any((value <= 0 for value in region.cell_ids))
                or len(set(region.cell_ids)) != len(region.cell_ids)
            ):
                error(
                    "region.cells",
                    path + ".cell_ids",
                    msg("validation.cell.ids.must.be.unique.positive.integers"),
                )
        allowed_by_usage = FLOW_FORMS_BY_USAGE
        allowed_flow = frozenset().union(*FLOW_FORMS_BY_USAGE.values())
        for index, condition in enumerate(c.flow_conditions):
            path = join_text("", ["flow_conditions[", index, "]"])
            if not _valid_object_name(condition.name):
                error("condition.name", path + ".name", msg("validation.invalid.condition.name"))
            if condition.usage not in allowed_by_usage:
                error("flow.usage", path + ".usage", msg("validation.invalid.flow.condition.usage"))
            elif condition.form not in allowed_by_usage[condition.usage]:
                error(
                    "flow.form",
                    path + ".form",
                    msg("text.condition.type.is.incompatible.with.the.process.and.usage"),
                )
            if condition.form not in allowed_flow:
                error(
                    "flow.form", path + ".form", msg("validation.unsupported.flow.condition.type")
                )
            for field, text in flow_parameter_errors(condition):
                error("flow.parameter", path + "." + field, text)
            if condition.form in {"hydrostatic_head", "seepage"}:
                gravity = c.grid.gravity
                magnitude = None
                if gravity.mode == "standard":
                    magnitude = 9.8068
                elif gravity.mode == "off":
                    magnitude = 0.0
                elif len(gravity.vector) == 3 and all(finite_real(v) for v in gravity.vector):
                    magnitude = math.hypot(*gravity.vector)
                # PFLOTRAN 4 requires Earth gravity for DATUM conditions.
                if magnitude is not None and abs(magnitude - 9.8068) > 0.1:
                    error(
                        "flow.hydrostatic_gravity",
                        path + ".form",
                        msg("repair.hydrostatic_gravity", name=condition.name),
                    )
        for index, constraint in enumerate(c.chemical_constraints):
            seen: set[str] = set()
            for value in constraint.concentrations:
                constraint_type = (
                    "P" if value.constraint_type == "pH" else value.constraint_type.upper()
                )
                if value.species not in primary:
                    error(
                        "reference.species",
                        join_text("", ["chemical_constraints[", index, "]"]),
                        msg("validation.primary_member", name=value.species),
                    )
                if value.species in seen:
                    error(
                        "constraint.duplicate",
                        join_text("", ["chemical_constraints[", index, "]"]),
                        msg("validation.species_duplicate", name=value.species),
                    )
                if not finite_real(value.value):
                    error(
                        "constraint.value",
                        join_text("", ["chemical_constraints[", index, "]"]),
                        msg("validation.species_value", name=value.species),
                    )
                if constraint_type not in CONSTRAINT_TYPES:
                    error(
                        "constraint.type",
                        join_text("", ["chemical_constraints[", index, "]"]),
                        msg("validation.constraint.type.must.be.f.t.p.g.or.m"),
                    )
                elif constraint_type not in {"G", "M"} and value.associated_species:
                    error(
                        "constraint.association",
                        join_text("", ["chemical_constraints[", index, "]"]),
                        msg("validation.constraint_object", kind=constraint_type),
                    )
                elif constraint_type == "G" and value.associated_species not in set(gas_values):
                    error(
                        "constraint.gas",
                        join_text("", ["chemical_constraints[", index, "]"]),
                        msg(
                            "validation.gas_reference",
                            name=value.associated_species or msg("text.not.selected"),
                        ),
                    )
                elif constraint_type == "M" and value.associated_species not in set(mineral_values):
                    error(
                        "constraint.mineral",
                        join_text("", ["chemical_constraints[", index, "]"]),
                        msg(
                            "validation.mineral_reference",
                            name=value.associated_species or msg("text.not.selected"),
                        ),
                    )
                if (
                    constraint_type in {"F", "T", "G", "M", "Z"}
                    and finite_real(value.value)
                    and value.value < 0
                ):
                    error(
                        "constraint.nonnegative",
                        f"chemical_constraints[{index}].concentrations",
                        msg("repair.concentration_nonnegative"),
                    )
                if constraint_type == "P" and value.species != "H+":
                    error(
                        "constraint.ph_species",
                        f"chemical_constraints[{index}].concentrations",
                        msg("repair.ph_species"),
                    )
                if constraint_type == "PE" and value.species not in {"H+", "O2(aq)"}:
                    error(
                        "constraint.pe_species",
                        f"chemical_constraints[{index}].concentrations",
                        msg("repair.pe_species"),
                    )
                seen.add(value.species)
            if sum(value.constraint_type.upper() == "Z" for value in constraint.concentrations) > 1:
                error(
                    "constraint.charge_balance",
                    f"chemical_constraints[{index}].concentrations",
                    msg("repair.charge_balance"),
                )
            if sum(mineral.volume_fraction for mineral in constraint.minerals) > 1 + 1e-12:
                error(
                    "constraint.mineral_total",
                    f"chemical_constraints[{index}].minerals",
                    msg("repair.mineral_total"),
                )
            if len({mineral.mineral_id for mineral in constraint.minerals}) != len(
                constraint.minerals
            ):
                error(
                    "constraint.mineral_duplicate",
                    f"chemical_constraints[{index}].minerals",
                    msg("repair.mineral_duplicate"),
                )
            for mineral in constraint.minerals:
                if mineral.mineral_id not in mineral_ids:
                    error(
                        "reference.mineral",
                        join_text("", ["chemical_constraints[", index, "]"]),
                        msg("text.referenced.mineral.does.not.exist"),
                    )
                if (
                    not finite_real(mineral.volume_fraction)
                    or not 0 <= mineral.volume_fraction <= 1
                    or not finite_real(mineral.surface_area)
                    or mineral.surface_area < 0
                ):
                    error(
                        "constraint.mineral_value",
                        join_text("", ["chemical_constraints[", index, "]"]),
                        msg(
                            "validation.mineral.volume.fraction.and.initial.reactive.surface.area.must.not.be.negative"
                        ),
                    )
                if mineral.surface_area_unit not in MINERAL_SURFACE_UNITS:
                    error(
                        "constraint.mineral_unit",
                        join_text("", ["chemical_constraints[", index, "]"]),
                        msg("text.initial.reactive.surface.area.requires.a.unit"),
                    )
        allowed_transport = {
            "initial_solution",
            "inflow_solution",
            "fixed_solution",
            "zero_gradient_outflow",
            "injection_solution",
        }
        form_transport_usage = TRANSPORT_USAGE
        for index, condition in enumerate(c.transport_conditions):
            path = join_text("", ["transport_conditions[", index, "]"])
            if not _valid_object_name(condition.name):
                error("condition.name", path + ".name", msg("validation.invalid.condition.name"))
            if condition.form not in allowed_transport:
                error(
                    "transport.form",
                    path + ".form",
                    msg("validation.unsupported.transport.condition.type"),
                )
            elif condition.usage != form_transport_usage[condition.form]:
                error(
                    "transport.usage",
                    path + ".usage",
                    msg("text.condition.type.is.incompatible.with.its.usage"),
                )
            if condition.constraint_id:
                if condition.constraint_id not in constraint_ids:
                    error(
                        "reference.constraint",
                        path + ".constraint_id",
                        msg("text.referenced.chemical.constraint.does.not.exist"),
                    )
            elif condition.form != "zero_gradient_outflow":
                error(
                    "transport.constraint",
                    path + ".constraint_id",
                    msg("text.transport.conditions.require.a.chemical.constraint"),
                )
            elif condition.reference_solution:
                reference = next(
                    (
                        item
                        for item in c.transport_conditions
                        if item.name == condition.reference_solution
                    ),
                    None,
                )
                if reference is None or not reference.constraint_id:
                    error(
                        "reference.transport",
                        path + ".reference_solution",
                        msg(
                            "text.outflow.references.a.missing.solution.or.a.solution.without.a.chemical.constraint"
                        ),
                    )
        for index, assignment in enumerate(c.spatial_assignment.condition_assignments):
            path = join_text("", ["spatial_assignment.condition_assignments[", index, "]"])
            if not _valid_object_name(assignment.name):
                error("assignment.name", path + ".name", msg("validation.invalid.assignment.name"))
            if assignment.usage not in {"initial", "boundary", "source_sink"}:
                error(
                    "assignment.usage", path + ".usage", msg("validation.invalid.assignment.usage")
                )
            if assignment.region_name not in region_ids:
                error(
                    "reference.region",
                    path + ".region_name",
                    msg("text.referenced.region.does.not.exist"),
                )
            if assignment.flow_condition_name and assignment.flow_condition_name not in flow_ids:
                error(
                    "reference.flow",
                    path + ".flow_condition_name",
                    msg("text.referenced.flow.condition.does.not.exist"),
                )
            if (
                assignment.transport_condition_name
                and assignment.transport_condition_name not in transport_ids
            ):
                error(
                    "reference.transport",
                    path + ".transport_condition_name",
                    msg("text.referenced.transport.condition.does.not.exist"),
                )
            if (
                assignment.flow_condition_name in flow_usage
                and flow_usage[assignment.flow_condition_name] != assignment.usage
            ):
                error(
                    "assignment.flow_usage",
                    path + ".flow_condition_name",
                    msg("text.flow.condition.usage.does.not.match.the.spatial.assignment"),
                )
            if (
                assignment.transport_condition_name in transport_usage
                and transport_usage[assignment.transport_condition_name] != assignment.usage
            ):
                error(
                    "assignment.transport_usage",
                    path + ".transport_condition_name",
                    msg("text.transport.condition.usage.does.not.match.the.spatial.assignment"),
                )
            if (
                assignment.usage == "initial"
                and c.simulation.uses_transport()
                and not assignment.transport_condition_name
            ):
                error(
                    "assignment.initial_transport",
                    path + ".transport_condition_name",
                    msg("repair.initial_transport"),
                )
            if assignment.usage == "initial" and not assignment.flow_condition_name:
                error(
                    "assignment.initial_flow",
                    path + ".flow_condition_name",
                    msg("repair.initial_flow"),
                )
            if not assignment.flow_condition_name and (not assignment.transport_condition_name):
                error(
                    "assignment.empty",
                    path,
                    msg("text.select.at.least.one.flow.or.transport.condition"),
                )
            if assignment.usage == "source_sink" and (not assignment.flow_condition_name):
                error(
                    "assignment.source_flow",
                    path + ".flow_condition_name",
                    msg("text.source.sink.assignments.require.a.flow.condition"),
                )
        if not any(item.usage == "initial" for item in c.spatial_assignment.condition_assignments):
            error(
                "assignment.initial",
                "spatial_assignment.condition_assignments",
                msg("repair.initial_required"),
            )
        mapping = c.spatial_assignment.material_mapping
        if mapping is None or not mapping.file_path:
            error(
                "material_mapping.file",
                "spatial_assignment.material_mapping",
                msg("text.material.mapping.requires.a.material.id.file"),
            )
        else:
            _safe_path(mapping.file_path, "spatial_assignment.material_mapping.file_path", error)
        if not c.spatial_assignment.condition_assignments:
            error(
                "model.assignments",
                "spatial_assignment",
                msg("text.no.spatial.condition.assignments.defined"),
            )
        return ValidationReport(tuple(issues))

    @staticmethod
    def _validate_database_references(c, project_directory: Path, error) -> None:
        from fracrtm.pflotran.thermodynamic_database import parse_thermodynamic_database

        relative = c.chemistry.database.relative_path
        try:
            pure = PurePosixPath(relative)
            if not relative or pure.is_absolute() or ".." in pure.parts:
                raise ValueError(
                    msg("validation.thermodynamic.database.must.use.a.safe.project.relative.path")
                )
            from fracrtm.project.storage import resource_path

            database_path = resource_path(project_directory, pure.as_posix())
            parsed = parse_thermodynamic_database(database_path)
        except (OSError, ValueError) as exc:
            error("chemistry.database_file", "chemistry.database", exception_text(exc))
            return
        checks = (
            (
                c.chemistry.aqueous_species.primary_species,
                parsed.aqueous_species,
                "chemistry.aqueous_species.primary_species",
                msg("text.aqueous.species"),
            ),
            (
                c.chemistry.aqueous_species.secondary_species,
                parsed.aqueous_species,
                "chemistry.aqueous_species.secondary_species",
                msg("text.aqueous.species"),
            ),
            (
                c.chemistry.gas_species.passive_gases,
                set(parsed.gas_species),
                "chemistry.gas_species.passive_gases",
                msg("text.gas"),
            ),
            (
                c.chemistry.selected_minerals,
                set(parsed.minerals),
                "chemistry.selected_minerals",
                msg("text.mineral"),
            ),
        )
        for selected, available, path, label in checks:
            for name in selected:
                if name not in available:
                    error(
                        "chemistry.database_reference",
                        path,
                        msg("validation.database_member", category=label, name=name),
                    )


def _positive(value: float | None) -> bool:
    return positive(value)


def _valid_object_name(value: str) -> bool:
    return bool(re.fullmatch("[A-Za-z_][A-Za-z0-9_-]*", value or ""))


def _time_seconds(value: float, unit: str) -> float:
    return float(value) * TIME_UNITS_SECONDS.get(unit, 1.0)


def _output_schedule(schedule: OutputScheduleConfig, path: str, error: Any) -> None:
    if schedule.mode not in {"periodic_time", "periodic_timestep", "specified_times"}:
        error(
            "output.schedule_mode",
            path + ".mode",
            msg("validation.unsupported.output.schedule.type"),
        )
    elif schedule.mode == "periodic_time" and (not _positive(schedule.interval_value)):
        error(
            "output.schedule_interval",
            path + ".interval_value",
            msg("validation.periodic.output.interval.must.be.positive"),
        )
    elif schedule.mode == "periodic_time" and schedule.interval_unit not in TIME_UNITS_SECONDS:
        error(
            "output.schedule_unit",
            path + ".interval_unit",
            msg("validation.invalid.output.time.unit"),
        )
    elif schedule.mode == "periodic_timestep" and schedule.timestep_interval < 1:
        error(
            "output.schedule_timestep",
            path + ".timestep_interval",
            msg("validation.time.step.interval.must.be.at.least.1"),
        )
    elif schedule.mode == "specified_times" and (
        not schedule.specified_times
        or any((not math.isfinite(value) or value < 0 for value in schedule.specified_times))
    ):
        error(
            "output.schedule_times",
            path + ".specified_times",
            msg("text.specify.at.least.one.valid.nonnegative.output.time"),
        )
    elif (
        schedule.mode == "specified_times"
        and schedule.specified_times_unit not in TIME_UNITS_SECONDS
    ):
        error(
            "output.schedule_unit",
            path + ".specified_times_unit",
            msg("validation.invalid.output.time.unit"),
        )


def _safe_path(value: str, path: str, error: Any) -> None:
    pure = PurePosixPath(value)
    if not value or pure.is_absolute() or ".." in pure.parts:
        error("path.unsafe", path, msg("validation.file.paths.must.be.safe.project.relative.paths"))


def _unique(objects: list[Any], label: str, error: Any) -> None:
    ids = [x.id.strip() for x in objects]
    names = [x.name.strip() for x in objects]
    if any((not x for x in ids)):
        error("object.empty_id", label, msg("validation.object.id.must.not.be.empty"))
    if len(ids) != len(set(ids)):
        error("object.duplicate_id", label, msg("validation.object.ids.must.be.unique"))
    if any((not x for x in names)):
        error("object.empty_name", label, msg("validation.object.name.must.not.be.empty"))
    if len(names) != len(set(names)):
        error(
            "object.duplicate_name",
            label,
            msg("validation.names.must.be.unique.within.each.object.type"),
        )


def _property_source(source: PropertySource, dataset_ids: set[str], path: str, error: Any) -> None:
    if source.mode == "constant":
        if source.value is None or not 0 < source.value <= 1:
            error(
                "property.constant",
                path,
                msg("validation.constant.porosity.must.satisfy.0.porosity.1"),
            )
    elif source.mode == "dataset":
        if source.dataset_id not in dataset_ids:
            error(
                "reference.dataset",
                path + ".dataset_id",
                msg("text.referenced.dataset.does.not.exist"),
            )
    else:
        error(
            "property.mode",
            path + ".mode",
            msg("validation.property.source.must.be.constant.or.dataset"),
        )


def _permeability_source(
    source: PermeabilitySource, dataset_ids: set[str], path: str, error: Any
) -> None:
    if source.mode == "isotropic":
        if not _positive(source.isotropic):
            error(
                "permeability.isotropic",
                path,
                msg("validation.isotropic.permeability.must.be.positive"),
            )
    elif source.mode == "diagonal":
        if (
            source.diagonal is None
            or len(source.diagonal) != 3
            or (not all((_positive(x) for x in source.diagonal)))
        ):
            error(
                "permeability.diagonal",
                path,
                msg("text.diagonal.permeability.requires.three.positive.values"),
            )
    elif source.mode == "dataset":
        if source.anisotropic_dataset:
            for axis, dataset_id in (
                ("x", source.dataset_x),
                ("y", source.dataset_y),
                ("z", source.dataset_z),
            ):
                if dataset_id not in dataset_ids:
                    error(
                        "reference.dataset",
                        path + join_text("", [".dataset_", axis]),
                        msg("validation.axis_dataset", axis=axis.upper()),
                    )
        elif source.dataset_id not in dataset_ids:
            error(
                "reference.dataset",
                path + ".dataset_id",
                msg("text.referenced.dataset.does.not.exist"),
            )
    else:
        error(
            "permeability.mode", path + ".mode", msg("validation.unsupported.permeability.source")
        )
