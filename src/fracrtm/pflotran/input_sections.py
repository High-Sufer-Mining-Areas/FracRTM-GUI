from __future__ import annotations

from fracrtm.config.contracts import flow_values, unassigned_boundary_placeholder
from fracrtm.config.pflotran import PFLOTRANConfig, ProcessNumericsConfig
from fracrtm.messages import message as msg


class PFLOTRANPropertiesWriter:
    def build(self, config: PFLOTRANConfig) -> str:
        blocks: list[str] = []
        if config.fluid_properties.diffusion_coefficient is not None:
            blocks.append(
                "\n".join(
                    [
                        "FLUID_PROPERTY",
                        f"  DIFFUSION_COEFFICIENT {_number(config.fluid_properties.diffusion_coefficient)}",
                        "END",
                    ]
                )
            )
        for dataset in config.datasets:
            lines = [f"DATASET {dataset.id}", f"  FILENAME {dataset.file}"]
            lines.append(f"  HDF5_DATASET_NAME {dataset.hdf5_dataset_name or dataset.name}")
            lines.append("END")
            blocks.append("\n".join(lines))
        for material in config.materials:
            blocks.append(self._material(material, config))
        for curve in config.characteristic_curves:
            blocks.append(self._curve(curve))
        return "\n\n".join(blocks)

    @staticmethod
    def _material(material, config) -> str:
        dataset_names = {item.id: item.id for item in config.datasets}
        lines = [f"MATERIAL_PROPERTY {material.id}", f"  ID {material.material_id}"]
        if material.porosity.mode == "dataset":
            lines.append(f"  POROSITY DATASET {dataset_names[material.porosity.dataset_id]}")
        else:
            lines.append(f"  POROSITY {_number(material.porosity.value)}")
        if material.tortuosity is not None:
            lines.append(f"  TORTUOSITY {_number(material.tortuosity)}")
        if config.simulation.uses_transport():
            lines.extend(
                [
                    f"  LONGITUDINAL_DISPERSIVITY {_number(material.longitudinal_dispersivity)}",
                    f"  TRANSVERSE_DISPERSIVITY_H {_number(material.transverse_dispersivity_horizontal)}",
                    f"  TRANSVERSE_DISPERSIVITY_V {_number(material.transverse_dispersivity_vertical)}",
                ]
            )
        lines.append(f"  CHARACTERISTIC_CURVES {material.characteristic_curve_id}")
        lines.append("  PERMEABILITY")
        permeability = material.permeability
        if permeability.mode == "dataset":
            if permeability.anisotropic_dataset:
                lines.extend(
                    [
                        f"    DATASET_X {dataset_names[permeability.dataset_x]}",
                        f"    DATASET_Y {dataset_names[permeability.dataset_y]}",
                        f"    DATASET_Z {dataset_names[permeability.dataset_z]}",
                    ]
                )
            else:
                lines.append(f"    DATASET {dataset_names[permeability.dataset_id]}")
        elif permeability.mode == "diagonal":
            lines.append(
                "    PERM_XYZ " + " ".join((_number(value) for value in permeability.diagonal))
            )
        else:
            lines.append(f"    PERM_ISO {_number(permeability.isotropic)}")
        lines.extend(["  /", "END"])
        return "\n".join(lines)

    @staticmethod
    def _curve(curve) -> str:
        if curve.model == "default":
            return "\n".join([f"CHARACTERISTIC_CURVES {curve.id}", "  DEFAULT", "END"])
        model = "VAN_GENUCHTEN" if curve.model == "van_genuchten" else "BROOKS_COREY"
        parameter = "M" if curve.model == "van_genuchten" else "LAMBDA"
        suffix = "VG" if curve.model == "van_genuchten" else "BC"
        lines = [
            f"CHARACTERISTIC_CURVES {curve.id}",
            f"  SATURATION_FUNCTION {model}",
            f"    {parameter} {_number(curve.m_or_lambda)}",
            f"    ALPHA {_number(curve.alpha)}",
            f"    LIQUID_RESIDUAL_SATURATION {_number(curve.liquid_residual_saturation)}",
        ]
        if curve.maximum_capillary_pressure is not None:
            lines.append(f"    MAX_CAPILLARY_PRESSURE {_number(curve.maximum_capillary_pressure)}")
        if curve.smooth and curve.model == "brooks_corey":
            lines.append("    SMOOTH")
        lines += [
            "  /",
            f"  PERMEABILITY_FUNCTION {curve.permeability_function if curve.permeability_function.endswith('_LIQ') else curve.permeability_function + '_' + suffix + '_LIQ'}",
            f"    {parameter} {_number(curve.m_or_lambda)}",
            f"    LIQUID_RESIDUAL_SATURATION {_number(curve.liquid_residual_saturation)}",
        ]
        if curve.smooth and curve.model == "van_genuchten":
            lines.append("    SMOOTH")
        lines += ["  /", "END"]
        return "\n".join(lines)


def _number(value) -> str:
    return f"{float(value):.12E}"


class PFLOTRANChemistryWriter:
    def build(self, config: PFLOTRANConfig) -> str:
        if not config.simulation.uses_transport():
            return ""
        chemistry = config.chemistry
        lines = ["CHEMISTRY"]
        _list_block(lines, "PRIMARY_SPECIES", chemistry.aqueous_species.primary_species)
        _list_block(lines, "SECONDARY_SPECIES", chemistry.aqueous_species.secondary_species)
        _list_block(lines, "PASSIVE_GAS_SPECIES", chemistry.gas_species.passive_gases)
        _list_block(lines, "MINERALS", chemistry.selected_minerals)
        if chemistry.mineral_kinetics:
            lines.append("  MINERAL_KINETICS")
            for kinetic in chemistry.mineral_kinetics:
                kinetic_lines = [
                    f"    {kinetic.mineral_name}",
                    f"      RATE_CONSTANT {_number(kinetic.rate_constant)} {kinetic.rate_unit}",
                ]
                if kinetic.surface_area_function == "mineral_mass":
                    surface_area = _surface_area_m2_per_kg(
                        kinetic.specific_surface_area, kinetic.surface_area_unit
                    )
                    kinetic_lines.append(
                        f"      SPECIFIC_SURFACE_AREA {_number(surface_area)} m^2/kg"
                    )
                lines.extend(kinetic_lines)
                if kinetic.prefactor_species:
                    lines.extend(
                        ["      PREFACTOR", f"        SPECIES {kinetic.prefactor_species}"]
                    )
                    if kinetic.alpha is not None:
                        lines.append(f"        ALPHA {_number(kinetic.alpha)}")
                    lines.append("      /")
                lines.append("    /")
            lines.append("  /")
        if not (
            chemistry.aqueous_species.secondary_species
            or chemistry.gas_species.passive_gases
            or chemistry.selected_minerals
        ) and (
            any(
                value.constraint_type.upper() != "T"
                for constraint in config.chemical_constraints
                for value in constraint.concentrations
            )
            or config.output.chemistry_variables.ph
            or (
                config.output.observation.enabled
                and config.output.observation.chemistry_variables.ph
            )
        ):
            # PFLOTRAN 4 requires speciation for non-total constraints.
            lines.append("  USE_FULL_GEOCHEMISTRY")
        lines.append(f"  DATABASE {chemistry.database.relative_path}")
        if chemistry.log_formulation:
            lines.append("  LOG_FORMULATION")
        if chemistry.activity_coefficients == "timestep":
            lines.append("  ACTIVITY_COEFFICIENTS TIMESTEP")
        if chemistry.update_porosity:
            lines.append("  UPDATE_POROSITY")
        if chemistry.update_permeability:
            lines.append("  UPDATE_PERMEABILITY")
        chemistry_output = PFLOTRANOutputWriter().build_chemistry_output(config)
        if chemistry_output:
            lines.extend(chemistry_output.splitlines())
        lines.append("END")
        return "\n".join(lines)


def _list_block(lines: list[str], name: str, values: list[str]) -> None:
    if not values:
        return
    lines.append(f"  {name}")
    lines.extend((f"    {value}" for value in values))
    lines.append("  /")


def _native_surface_unit(unit):
    return (
        unit.replace("cm2", "cm^2").replace("cm3", "cm^3").replace("m2", "m^2").replace("m3", "m^3")
    )


def _surface_area_m2_per_kg(value: float | None, unit: str) -> float:
    factors = {"m2/kg": 1.0, "m2/g": 1000.0, "cm2/g": 0.1}
    if unit not in factors:
        raise ValueError(msg("input.surface_unit", unit=unit))
    if value is None:
        raise ValueError(
            msg("text.the.mass.based.surface.area.model.requires.specific.surface.area")
        )
    return value * factors[unit]


class PFLOTRANTimeNumericsWriter:
    def build(self, config: PFLOTRANConfig) -> str:
        blocks = [self._time(config)]
        if config.numerical_methods.mode == "custom":
            blocks.append(self._process("FLOW", config.numerical_methods.flow))
            if config.simulation.uses_transport():
                blocks.append(self._process("TRANSPORT", config.numerical_methods.transport))
        return "\n\n".join(blocks)

    @staticmethod
    def _time(config: PFLOTRANConfig) -> str:
        value = config.time
        return "\n".join(
            [
                "TIME",
                f"  FINAL_TIME {_number(value.final_time)} {value.final_unit}",
                f"  INITIAL_TIMESTEP_SIZE {_number(value.initial_timestep)} {value.initial_timestep_unit}",
                f"  MAXIMUM_TIMESTEP_SIZE {_number(value.maximum_timestep)} {value.maximum_timestep_unit}",
                "END",
            ]
        )

    @staticmethod
    def _process(name: str, process: ProcessNumericsConfig) -> str:
        newton = process.newton
        linear = process.linear
        lines = [f"NUMERICAL_METHODS {name}", "  NEWTON_SOLVER"]
        for keyword, value in (
            ("MAXIMUM_ITERATIONS", newton.maximum_iterations),
            ("SNES_TYPE", newton.snes_type),
            ("RTOL", newton.rtol),
            ("ATOL", newton.atol),
            ("ITOL", newton.itol),
            ("STOL", newton.stol),
            ("DTOL", newton.dtol),
        ):
            if value is not None:
                rendered = value if isinstance(value, (str, int)) else _number(value)
                lines.append(f"    {keyword} {rendered}")
        lines.extend(["  /", "  LINEAR_SOLVER", f"    SOLVER {linear.solver_type}"])
        for keyword, value in (
            ("PRECONDITIONER", linear.preconditioner),
            ("RTOL", linear.rtol),
            ("ATOL", linear.atol),
            ("DTOL", linear.dtol),
        ):
            if value is not None:
                lines.append(
                    f"    {keyword} {(value if isinstance(value, str) else _number(value))}"
                )
        lines.extend(["  /", "END"])
        return "\n".join(lines)


class PFLOTRANRegionConditionsWriter:
    def build(self, config: PFLOTRANConfig) -> str:
        blocks = [
            self._region(item)
            for item in config.regions
            if not unassigned_boundary_placeholder(config, item)
        ]
        if config.simulation.task_type in {"flow", "conservative_transport", "reactive_transport"}:
            blocks.extend((self._flow(item, config) for item in config.flow_conditions))
        if config.simulation.uses_transport():
            blocks.extend((self._constraint(item) for item in config.chemical_constraints))
            for item in config.transport_conditions:
                blocks.append(self._transport(item, config))
        return "\n\n".join((block for block in blocks if block)) + ("\n" if blocks else "")

    @staticmethod
    def _region(item) -> str:
        if item.region_type == "COORDINATE":
            body = "COORDINATE " + " ".join((_condition_number(v) for v in item.coordinate))
        elif item.region_type == "COORDINATES":
            lo = " ".join((_condition_number(v) for v in item.bounds[:3]))
            hi = " ".join((_condition_number(v) for v in item.bounds[3:]))
            body = f"COORDINATES\n    {lo}\n    {hi}\n  /"
        elif item.region_type == "LIST":
            body = "LIST\n" + "\n".join((f"    {value}" for value in item.cell_ids)) + "\n  /"
        else:
            body = f"FILE {item.file_path}"
        return f"REGION {item.name}\n  {body}\n/"

    @staticmethod
    def _flow(item, config) -> str:
        p = item.parameters
        form = item.form
        values = flow_values(item, config)
        if form == "fixed_liquid_pressure":
            lines = [
                "TYPE",
                "    LIQUID_PRESSURE DIRICHLET",
                "  /",
                f"  LIQUID_PRESSURE {_condition_number(p['value'])} {values.unit}",
            ]
        elif form in {"hydrostatic_head", "seepage"}:
            kind = "HYDROSTATIC" if form == "hydrostatic_head" else "SEEPAGE"
            datum = " ".join(_condition_number(value) for value in values.datum)
            lines = [
                "TYPE",
                f"    LIQUID_PRESSURE {kind}",
                "  /",
                f"  DATUM {datum}",
                f"  LIQUID_PRESSURE {_condition_number(values.value)} {values.unit}",
            ]
        elif form == "darcy_flux":
            lines = [
                "TYPE",
                "    LIQUID_FLUX NEUMANN",
                "  /",
                f"  LIQUID_FLUX {_condition_number(p['value'])} {values.unit}",
            ]
        else:
            keyword = (
                "SCALED_VOLUMETRIC_RATE" if form == "scaled_volumetric_rate" else "SCALED_MASS_RATE"
            )
            distribution = p.get("distribution", "VOLUME")
            lines = [
                "TYPE",
                f"    RATE {keyword} {distribution}",
                "  /",
                f"  RATE {_condition_number(p['value'])} {values.unit}",
            ]
        return f"FLOW_CONDITION {item.name}\n  " + "\n  ".join(lines) + "\n/"

    @staticmethod
    def _constraint(item) -> str:
        lines = [f"CONSTRAINT {item.id}", "  CONCENTRATIONS"]
        for value in item.concentrations:
            constraint_type = (
                "P" if value.constraint_type == "pH" else value.constraint_type.upper()
            )
            row = f"    {value.species} {_condition_number(value.value)} {constraint_type}"
            if constraint_type in {"G", "M"}:
                row += f" {value.associated_species}"
            lines.append(row)
        lines.append("  /")
        if item.minerals:
            lines.append("  MINERALS")
            for mineral in item.minerals:
                lines.append(
                    f"    {mineral.mineral_id} {_condition_number(mineral.volume_fraction)} {_condition_number(mineral.surface_area)} {_native_surface_unit(mineral.surface_area_unit)}"
                )
            lines.append("  /")
        lines.append("END")
        return "\n".join(lines)

    @staticmethod
    def _transport(item, config) -> str:
        condition_type = {
            "initial_solution": "DIRICHLET_ZERO_GRADIENT",
            "inflow_solution": "DIRICHLET_ZERO_GRADIENT",
            "fixed_solution": "DIRICHLET",
            "zero_gradient_outflow": "ZERO_GRADIENT",
            "injection_solution": "DIRICHLET",
        }[item.form]
        constraint = item.constraint_id
        if not constraint and item.reference_solution:
            reference = next(
                (
                    value
                    for value in config.transport_conditions
                    if value.name == item.reference_solution
                ),
                None,
            )
            constraint = reference.constraint_id if reference else ""
        return f"TRANSPORT_CONDITION {item.name}\n  TYPE {condition_type}\n  CONSTRAINT_LIST\n    0.d0 {constraint}\n  /\n/"


def _condition_number(value) -> str:
    return f"{float(value):.12g}"


class PFLOTRANSpatialAssignmentWriter:
    def build(self, config: PFLOTRANConfig) -> str:
        blocks = [
            self._assignment(item) for item in config.spatial_assignment.condition_assignments
        ]
        mapping = config.spatial_assignment.material_mapping
        if mapping and mapping.file_path:
            blocks.append(f"STRATA\n  FILE {mapping.file_path}\n/")
        return "\n\n".join(blocks) + ("\n" if blocks else "")

    @staticmethod
    def _assignment(item) -> str:
        keyword = {
            "initial": "INITIAL_CONDITION",
            "boundary": "BOUNDARY_CONDITION",
            "source_sink": "SOURCE_SINK",
        }[item.usage]
        lines = [f"{keyword} {item.name}"]
        if item.flow_condition_name:
            lines.append(f"  FLOW_CONDITION {item.flow_condition_name}")
        if item.transport_condition_name:
            lines.append(f"  TRANSPORT_CONDITION {item.transport_condition_name}")
        lines.append(f"  REGION {item.region_name}")
        lines.append("/")
        return "\n".join(lines)


class PFLOTRANOutputWriter:
    def build(self, config: PFLOTRANConfig) -> str:
        output = config.output
        lines = ["OUTPUT"]
        if output.velocity_at_center or output.flow_variables.velocity:
            lines.append("  VELOCITY_AT_CENTER")
        if output.mass_flowrate:
            lines.append("  MASS_FLOWRATE")
        if output.mass_balance:
            lines.append("  MASS_BALANCE")
        lines.append("  SNAPSHOT_FILE")
        _append_schedule(lines, output.schedule, "    ")
        format_name = "HDF5 SINGLE_FILE" if output.snapshot_format == "HDF5" else "VTK"
        lines.append(f"    FORMAT {format_name}")
        if not output.print_initial:
            lines.append("    NO_PRINT_INITIAL")
        if not output.print_final:
            lines.append("    NO_PRINT_FINAL")
        variables = _flow_variable_keywords(output.flow_variables)
        if variables:
            lines.append("    VARIABLES")
            lines.extend((f"      {name}" for name in variables))
            lines.append("    /")
        lines.append("  /")
        mass_file = output.mass_balance_file
        if mass_file.enabled:
            lines.append("  MASS_BALANCE_FILE")
            _append_schedule(lines, mass_file.schedule, "    ")
            if not mass_file.print_initial:
                lines.append("    NO_PRINT_INITIAL")
            if not mass_file.print_final:
                lines.append("    NO_PRINT_FINAL")
            lines.append("    TOTAL_MASS_REGIONS")
            lines.extend((f"      {name}" for name in mass_file.regions))
            lines.append("    /")
            lines.append("  /")
        observation = output.observation
        if observation.enabled:
            lines.append("  OBSERVATION_FILE")
            _append_schedule(lines, observation.schedule, "    ")
            if not observation.print_initial:
                lines.append("    NO_PRINT_INITIAL")
            if not observation.print_final:
                lines.append("    NO_PRINT_FINAL")
            observation_variables = _flow_variable_keywords(observation.flow_variables)
            observation_variables.extend(
                _chemistry_variable_keywords(observation.chemistry_variables, config)
            )
            if observation_variables:
                lines.append("    VARIABLES")
                lines.extend((f"      {name}" for name in observation_variables))
                lines.append("    /")
            lines.append("  /")
        lines.append("END")
        return "\n".join(lines)

    def build_chemistry_output(self, config: PFLOTRANConfig) -> str:
        if not config.simulation.uses_transport():
            return ""
        outputs = [config.output.chemistry_variables]
        if config.output.observation.enabled:
            outputs.append(config.output.observation.chemistry_variables)
        chemistry = config.chemistry
        variables: list[str] = []

        def requested(name):
            return any((getattr(output, name) for output in outputs))

        if requested("ph"):
            variables.append("PH")
        if requested("all_primary_species") and chemistry.aqueous_species.primary_species:
            variables.extend(("PRIMARY_SPECIES", "TOTAL"))
        if requested("all_species"):
            variables.append("ALL")
        if requested("secondary_species") and chemistry.aqueous_species.secondary_species:
            variables.append("SECONDARY_SPECIES")
        if chemistry.selected_minerals:
            if requested("mineral_volume_fraction"):
                variables.append("MINERALS")
            if requested("mineral_rate") and chemistry.mineral_kinetics:
                variables.append("MINERAL_RATE")
            if requested("mineral_saturation_index"):
                variables.append("MINERAL_SATURATION_INDEX")
        if not variables:
            return ""
        return "\n".join(["  OUTPUT", *[f"    {name}" for name in variables], "  /"])

    def build_observations(self, config: PFLOTRANConfig) -> str:
        observation = config.output.observation
        if not observation.enabled:
            return ""
        blocks = []
        for name in observation.regions:
            blocks.append("\n".join(["OBSERVATION", f"  REGION {name}", "  AT_CELL_CENTER", "/"]))
        return "\n\n".join(blocks)


def _append_schedule(lines: list[str], schedule, indent: str) -> None:
    if schedule.mode == "periodic_time":
        lines.append(
            f"{indent}PERIODIC TIME {_number(schedule.interval_value)} {schedule.interval_unit}"
        )
    elif schedule.mode == "specified_times":
        values = " ".join((_number(value) for value in schedule.specified_times))
        lines.append(f"{indent}TIMES {schedule.specified_times_unit} {values}")
    elif schedule.mode == "periodic_timestep":
        lines.append(f"{indent}PERIODIC TIMESTEP {schedule.timestep_interval}")


def _flow_variable_keywords(flow) -> list[str]:
    return [
        keyword
        for enabled, keyword in (
            (flow.liquid_pressure, "LIQUID_PRESSURE"),
            (flow.liquid_head, "LIQUID_HEAD"),
            (flow.liquid_saturation, "LIQUID_SATURATION"),
            (flow.porosity, "POROSITY"),
            (flow.permeability, "PERMEABILITY"),
            (flow.permeability_x, "PERMEABILITY_X"),
            (flow.permeability_y, "PERMEABILITY_Y"),
            (flow.permeability_z, "PERMEABILITY_Z"),
        )
        if enabled
    ]


def _chemistry_variable_keywords(output, config: PFLOTRANConfig) -> list[str]:
    chemistry = config.chemistry
    variables = []
    if output.ph:
        variables.append("PH")
    if output.all_primary_species and chemistry.aqueous_species.primary_species:
        variables.append("TOTAL")
    if output.all_species:
        variables.append("ALL")
    if output.secondary_species and chemistry.aqueous_species.secondary_species:
        variables.append("SECONDARY_SPECIES")
    if chemistry.selected_minerals:
        if output.mineral_volume_fraction:
            variables.append("MINERALS")
        if output.mineral_rate and chemistry.mineral_kinetics:
            variables.append("MINERAL_RATE")
        if output.mineral_saturation_index:
            variables.append("MINERAL_SATURATION_INDEX")
    return variables
