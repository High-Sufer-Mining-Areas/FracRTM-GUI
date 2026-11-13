from __future__ import annotations

import json
import math
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from fracrtm.messages import (
    Message,
    canonical_record,
    prepare_json,
    record_message,
    render_record,
    restore_record,
)
from fracrtm.messages import message as msg

from .time_units import TIME_UNITS_SECONDS as TIME_UNITS_SECONDS

PFLOTRAN_SCHEMA_VERSION = "2.0"
Severity = Literal["error", "warning"]


def _compact_json_numbers(value: Any) -> Any:
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return int(value)
        return value
    if isinstance(value, dict):
        return {key: _compact_json_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_compact_json_numbers(item) for item in value]
    if isinstance(value, tuple):
        return [_compact_json_numbers(item) for item in value]
    return value


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    code: str
    path: str
    message: str | Message

    def to_dict(self):
        return render_record(asdict(self))


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...] = ()

    def to_dict(self):
        return {"valid": self.valid, "issues": [issue.to_dict() for issue in self.issues]}

    @property
    def valid(self) -> bool:
        return not any((item.severity == "error" for item in self.issues))

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple((item for item in self.issues if item.severity == "error"))

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple((item for item in self.issues if item.severity == "warning"))


@dataclass
class RuntimeConfig:
    executable: str = "/root/pflotran/src/pflotran/pflotran"
    mpi_executable: str = "mpirun"
    processes: int = 1


@dataclass
class SimulationConfig:
    task_type: str = "reactive_transport"
    flow_state: str = "transient"
    saturation_state: str = "saturated"
    flow_mode: str = "RICHARDS"
    transport_mode: str = "GIRT"

    def uses_transport(self) -> bool:
        return self.task_type in {"conservative_transport", "reactive_transport"}

    def uses_reactions(self) -> bool:
        return self.task_type == "reactive_transport"


@dataclass
class TimeControlConfig:
    final_time: float = 20.0
    final_unit: str = "y"
    initial_timestep: float = 1.0
    initial_timestep_unit: str = "h"
    maximum_timestep: float = 1.0
    maximum_timestep_unit: str = "y"


@dataclass
class NewtonSolverConfig:
    maximum_iterations: int | None = 15
    snes_type: str | None = "LINE_SEARCH"
    rtol: float | None = None
    atol: float | None = 1e-50
    itol: float | None = None
    stol: float | None = 1e-08
    dtol: float | None = 10000.0


@dataclass
class LinearSolverConfig:
    solver_type: str = "BCGS"
    preconditioner: str | None = "ILU"
    rtol: float | None = 1e-05
    atol: float | None = 1e-50
    dtol: float | None = 10000.0


@dataclass
class ProcessNumericsConfig:
    newton: NewtonSolverConfig = field(default_factory=NewtonSolverConfig)
    linear: LinearSolverConfig = field(default_factory=LinearSolverConfig)


@dataclass
class NumericalMethodsConfig:
    mode: str = "recommended"
    flow: ProcessNumericsConfig = field(default_factory=ProcessNumericsConfig)
    transport: ProcessNumericsConfig = field(default_factory=ProcessNumericsConfig)


@dataclass
class GravityConfig:
    mode: str = "standard"
    vector: list[float] = field(default_factory=lambda: [0.0, 0.0, -9.8068])


@dataclass
class GridConfig:
    grid_type: str = "unstructured_explicit"
    file: str = "octree/full_mesh.uge"
    gravity: GravityConfig = field(default_factory=GravityConfig)


@dataclass
class FluidPropertyConfig:
    diffusion_coefficient: float | None = 1e-09


@dataclass
class DatasetConfig:
    id: str
    name: str
    file: str
    hdf5_dataset_name: str | None = None

    def __post_init__(self):
        # Bind an omitted HDF5 path to its stored dataset name.
        if self.hdf5_dataset_name is None:
            self.hdf5_dataset_name = self.name


@dataclass
class ThermodynamicDatabaseConfig:
    relative_path: str = ""
    status: str = "not_loaded"
    message: str | Message = ""


@dataclass
class AqueousSpeciesConfig:
    primary_species: list[str] = field(default_factory=list)
    secondary_species: list[str] = field(default_factory=list)


@dataclass
class GasSpeciesConfig:
    passive_gases: list[str] = field(default_factory=list)


@dataclass
class MineralKineticsConfig:
    mineral_name: str = ""
    rate_constant: float = 0.0
    rate_unit: str = "mol/m^2-sec"
    surface_area_function: str = "default"
    specific_surface_area: float | None = None
    surface_area_unit: str = "m2/kg"
    prefactor_species: str | None = None
    alpha: float | None = None


@dataclass
class ChemistryConfig:
    database: ThermodynamicDatabaseConfig = field(default_factory=ThermodynamicDatabaseConfig)
    aqueous_species: AqueousSpeciesConfig = field(default_factory=AqueousSpeciesConfig)
    gas_species: GasSpeciesConfig = field(default_factory=GasSpeciesConfig)
    selected_minerals: list[str] = field(default_factory=list)
    mineral_kinetics: list[MineralKineticsConfig] = field(default_factory=list)
    log_formulation: bool = True
    activity_coefficients: str = "default"
    update_porosity: bool = False
    update_permeability: bool = False


@dataclass
class PropertySource:
    mode: str = "constant"
    value: float | None = None
    dataset_id: str | None = None


@dataclass
class PermeabilitySource:
    mode: str = "isotropic"
    isotropic: float | None = None
    diagonal: list[float] | None = None
    dataset_id: str | None = None
    anisotropic_dataset: bool = False
    dataset_x: str | None = None
    dataset_y: str | None = None
    dataset_z: str | None = None


@dataclass
class MaterialConfig:
    id: str
    name: str
    material_id: int
    porosity: PropertySource = field(default_factory=lambda: PropertySource(value=0.1))
    permeability: PermeabilitySource = field(
        default_factory=lambda: PermeabilitySource(isotropic=1e-18)
    )
    tortuosity: float | None = 1.0
    longitudinal_dispersivity: float = 0.0
    transverse_dispersivity_horizontal: float = 0.0
    transverse_dispersivity_vertical: float = 0.0
    characteristic_curve_id: str | None = None


@dataclass
class CharacteristicCurveConfig:
    id: str
    name: str
    model: str = "default"
    alpha: float | None = None
    m_or_lambda: float | None = None
    liquid_residual_saturation: float = 0.0
    maximum_capillary_pressure: float | None = None
    permeability_function: str = "MUALEM"
    smooth: bool = False


@dataclass
class RegionConfig:
    name: str
    region_type: str
    source: str = "user"
    status: str = "valid"
    coordinate: tuple[float, float, float] | None = None
    bounds: tuple[float, float, float, float, float, float] | None = None
    cell_ids: list[int] = field(default_factory=list)
    file_path: str = ""
    file_region_kind: str = ""
    coordinate_unit: str = "m"
    message: str | Message = ""

    @property
    def id(self) -> str:
        return self.name

    @id.setter
    def id(self, value: str) -> None:
        self.name = value

    @property
    def geometry_type(self) -> str:
        return {
            "COORDINATE": "point",
            "COORDINATES": "box",
            "LIST": "cell_list",
            "FILE": "boundary_file",
        }.get(self.region_type, self.region_type.lower())

    @property
    def coordinates(self) -> list[float]:
        return list(self.coordinate or self.bounds or ())

    @property
    def file(self) -> str | None:
        return self.file_path or None


@dataclass
class FlowConditionConfig:
    name: str
    usage: str
    form: str
    parameters: dict[str, Any] = field(default_factory=dict)
    direction_semantics: str = "unknown"

    @property
    def id(self) -> str:
        return self.name

    @id.setter
    def id(self, value: str) -> None:
        self.name = value

    @property
    def datum(self) -> list[float]:
        return list(self.parameters.get("datum", []))

    @datum.setter
    def datum(self, value: list[float]) -> None:
        self.parameters["datum"] = list(value)


@dataclass
class ConcentrationConstraint:
    species: str
    value: float
    constraint_type: str = "T"
    associated_species: str | None = None


@dataclass
class MineralConstraint:
    mineral_id: str
    volume_fraction: float
    surface_area: float
    surface_area_unit: str = "m^2/m^3"

    @property
    def specific_surface_area(self) -> float:
        return self.surface_area


@dataclass
class ChemicalConstraintConfig:
    id: str
    name: str
    concentrations: list[ConcentrationConstraint] = field(default_factory=list)
    minerals: list[MineralConstraint] = field(default_factory=list)


@dataclass
class TransportConditionConfig:
    name: str
    usage: str
    form: str
    solution_components: list[ConcentrationConstraint] = field(default_factory=list)
    reference_solution: str = ""
    constraint_id: str = ""

    @property
    def id(self) -> str:
        return self.name

    @id.setter
    def id(self, value: str) -> None:
        self.name = value


@dataclass
class SpatialConditionAssignment:
    name: str
    usage: str
    region_name: str = ""
    flow_condition_name: str | None = None
    transport_condition_name: str | None = None

    @property
    def id(self) -> str:
        return self.name


@dataclass
class MaterialMappingItem:
    material_id: int
    material_name: str
    cell_count: int
    property_status: str
    message: str | Message = ""


@dataclass
class MaterialMappingSummary:
    mode: str = "material_file"
    file_path: str = ""
    active_cell_count: int = 0
    mapped_cell_count: int = 0
    unmapped_cell_count: int = 0
    items: list[MaterialMappingItem] = field(default_factory=list)
    status: str = "not_loaded"
    message: str | Message = ""


@dataclass
class SpatialAssignmentConfig:
    condition_assignments: list[SpatialConditionAssignment] = field(default_factory=list)
    material_mapping: MaterialMappingSummary | None = None


@dataclass
class OutputScheduleConfig:
    mode: str = "periodic_time"
    interval_value: float = 1.0
    interval_unit: str = "d"
    specified_times: list[float] = field(default_factory=list)
    specified_times_unit: str = "d"
    timestep_interval: int = 10


@dataclass
class FlowOutputVariables:
    liquid_pressure: bool = True
    liquid_head: bool = True
    liquid_saturation: bool = True
    velocity: bool = False
    porosity: bool = False
    permeability: bool = False
    permeability_x: bool = False
    permeability_y: bool = False
    permeability_z: bool = False


@dataclass
class ChemistryOutputVariables:
    ph: bool = True
    all_primary_species: bool = True
    all_species: bool = False
    secondary_species: bool = False
    mineral_volume_fraction: bool = True
    mineral_rate: bool = True
    mineral_saturation_index: bool = True


@dataclass
class MassBalanceFileConfig:
    enabled: bool = False
    schedule: OutputScheduleConfig = field(
        default_factory=lambda: OutputScheduleConfig(mode="periodic_timestep", timestep_interval=1)
    )
    regions: list[str] = field(default_factory=list)
    print_initial: bool = True
    print_final: bool = True


@dataclass
class ObservationFileConfig:
    enabled: bool = False
    regions: list[str] = field(default_factory=list)
    schedule: OutputScheduleConfig = field(
        default_factory=lambda: OutputScheduleConfig(mode="periodic_timestep", timestep_interval=1)
    )
    print_initial: bool = True
    print_final: bool = True
    flow_variables: FlowOutputVariables = field(
        default_factory=lambda: FlowOutputVariables(
            liquid_pressure=True, liquid_head=False, liquid_saturation=False
        )
    )
    chemistry_variables: ChemistryOutputVariables = field(
        default_factory=lambda: ChemistryOutputVariables(
            ph=False,
            all_primary_species=False,
            all_species=False,
            secondary_species=False,
            mineral_volume_fraction=False,
            mineral_rate=False,
            mineral_saturation_index=False,
        )
    )


@dataclass
class OutputConfig:
    snapshot_format: str = "VTK"
    schedule: OutputScheduleConfig = field(default_factory=OutputScheduleConfig)
    print_initial: bool = True
    print_final: bool = True
    velocity_at_center: bool = False
    save_final_input: bool = True
    flow_variables: FlowOutputVariables = field(default_factory=FlowOutputVariables)
    chemistry_variables: ChemistryOutputVariables = field(default_factory=ChemistryOutputVariables)
    mass_flowrate: bool = True
    mass_balance: bool = True
    mass_balance_file: MassBalanceFileConfig = field(default_factory=MassBalanceFileConfig)
    observation: ObservationFileConfig = field(default_factory=ObservationFileConfig)


@dataclass
class PFLOTRANConfig:
    schema_version: str = PFLOTRAN_SCHEMA_VERSION
    template_id: str = "blank_reactive_transport"
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    time: TimeControlConfig = field(default_factory=TimeControlConfig)
    numerical_methods: NumericalMethodsConfig = field(default_factory=NumericalMethodsConfig)
    grid: GridConfig = field(default_factory=GridConfig)
    fluid_properties: FluidPropertyConfig = field(default_factory=FluidPropertyConfig)
    datasets: list[DatasetConfig] = field(default_factory=list)
    chemistry: ChemistryConfig = field(default_factory=ChemistryConfig)
    materials: list[MaterialConfig] = field(default_factory=list)
    characteristic_curves: list[CharacteristicCurveConfig] = field(default_factory=list)
    regions: list[RegionConfig] = field(default_factory=list)
    flow_conditions: list[FlowConditionConfig] = field(default_factory=list)
    chemical_constraints: list[ChemicalConstraintConfig] = field(default_factory=list)
    transport_conditions: list[TransportConditionConfig] = field(default_factory=list)
    spatial_assignment: SpatialAssignmentConfig = field(default_factory=SpatialAssignmentConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("runtime", None)
        return _compact_json_numbers(canonical_record(data))

    def scientific_dict(self) -> dict[str, Any]:
        data = self.to_dict()
        data.pop("template_id", None)
        from .contracts import unassigned_boundary_placeholder

        data["regions"] = [
            item
            for region, item in zip(self.regions, data["regions"])
            if not unassigned_boundary_placeholder(self, region)
        ]
        database = data["chemistry"]["database"]
        data["chemistry"]["database"] = {"relative_path": database["relative_path"]}
        for section in ("regions",):
            for item in data[section]:
                for key in ("status", "message", "message_data", "source"):
                    item.pop(key, None)
        for section in ("datasets", "materials", "characteristic_curves", "chemical_constraints"):
            for item in data[section]:
                item.pop("name", None)
        mapping = data["spatial_assignment"].get("material_mapping")
        if mapping is not None:
            data["spatial_assignment"]["material_mapping"] = {
                "mode": mapping["mode"],
                "file_path": mapping["file_path"],
            }
        output = data["output"]
        legacy_velocity = output["flow_variables"].pop("velocity", False)
        output["velocity_at_center"] = output["velocity_at_center"] or legacy_velocity
        data["output"].pop("save_final_input", None)
        # Exclude inactive variants from the scientific fingerprint.
        for material in data["materials"]:
            porosity = material["porosity"]
            material["porosity"] = {
                key: porosity[key]
                for key in ("mode", "dataset_id" if porosity["mode"] == "dataset" else "value")
            }
            permeability = material["permeability"]
            mode = permeability["mode"]
            keys = ["mode"]
            if mode == "dataset":
                keys += ["anisotropic_dataset"] + (
                    ["dataset_x", "dataset_y", "dataset_z"]
                    if permeability["anisotropic_dataset"]
                    else ["dataset_id"]
                )
            else:
                keys += ["diagonal" if mode == "diagonal" else "isotropic"]
            material["permeability"] = {key: permeability[key] for key in keys}
        for curve in data["characteristic_curves"]:
            if curve["model"] == "default":
                for key in tuple(curve):
                    if key not in {"id", "model"}:
                        curve.pop(key)
        for region in data["regions"]:
            fields = {
                "FILE": ("file_path",),
                "LIST": ("cell_ids",),
                "COORDINATE": ("coordinate", "coordinate_unit"),
                "COORDINATES": ("bounds", "coordinate_unit"),
            }.get(region["region_type"], ())
            for key in tuple(region):
                if key not in {"name", "region_type", *fields}:
                    region.pop(key)
        from .contracts import canonical_constraint_type, flow_parameter_errors, flow_values

        for constraint in data["chemical_constraints"]:
            for value in constraint["concentrations"]:
                value["constraint_type"] = canonical_constraint_type(value["constraint_type"])

        for condition, stored in zip(self.flow_conditions, data["flow_conditions"]):
            stored.pop("direction_semantics", None)
            if not flow_parameter_errors(condition):
                try:
                    values = flow_values(condition, self)
                except (ValueError, TypeError, KeyError):
                    # Preserve unresolved draft values for validation.
                    continue
                parameters = {"value": values.value, "unit": values.unit}
                if values.datum is not None:
                    parameters["datum"] = list(values.datum)
                    parameters["length_unit"] = "m"
                if condition.form in {"scaled_mass_rate", "scaled_volumetric_rate"}:
                    parameters["distribution"] = values.distribution
                stored["parameters"] = parameters

        def schedule(value):
            mode = value["mode"]
            keys = {
                "periodic_time": ("interval_value", "interval_unit"),
                "specified_times": ("specified_times", "specified_times_unit"),
                "periodic_timestep": ("timestep_interval",),
            }.get(mode, ())
            return {key: value[key] for key in ("mode", *keys)}

        output["schedule"] = schedule(output["schedule"])
        for key in ("observation", "mass_balance_file"):
            if not output[key]["enabled"]:
                output[key] = {"enabled": False}
            else:
                output[key]["schedule"] = schedule(output[key]["schedule"])
        if not self.simulation.uses_transport():
            for key in ("chemistry", "chemical_constraints", "transport_conditions"):
                data.pop(key, None)
            output.pop("chemistry_variables", None)
            output["observation"].pop("chemistry_variables", None)
        return data

    def save_json(self, path: str | Path) -> None:
        from fracrtm.project.storage import atomic_json

        atomic_json(Path(path), prepare_json(canonical_record(self.to_dict())))

    @classmethod
    def load_json(cls, path: str | Path) -> "PFLOTRANConfig":
        with Path(path).open("r", encoding="utf-8") as stream:
            return cls.from_dict(json.load(stream))

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PFLOTRANConfig":
        from .contracts import check_serialized_numbers

        check_serialized_numbers(data or {})
        d = restore_record(data or {})
        nm = d.get("numerical_methods", {})
        output = d.get("output", {})
        return cls(
            schema_version=str(d.get("schema_version", PFLOTRAN_SCHEMA_VERSION)),
            template_id=str(d.get("template_id", "blank_reactive_transport")),
            runtime=RuntimeConfig(**d.get("runtime", {})),
            simulation=SimulationConfig(**d.get("simulation", {})),
            time=_time_control(d.get("time"), nm),
            numerical_methods=NumericalMethodsConfig(
                mode=str(nm.get("mode", "recommended")),
                flow=_process_numerics(nm.get("flow")),
                transport=_process_numerics(nm.get("transport")),
            ),
            grid=_grid_config(d.get("grid")),
            fluid_properties=FluidPropertyConfig(**d.get("fluid_properties", {})),
            datasets=[DatasetConfig(**x) for x in d.get("datasets", [])],
            chemistry=_chemistry(d.get("chemistry"), d.get("minerals", [])),
            materials=[_material(x) for x in d.get("materials", [])],
            characteristic_curves=[
                CharacteristicCurveConfig(**x) for x in d.get("characteristic_curves", [])
            ],
            regions=[_region(x) for x in d.get("regions", [])],
            flow_conditions=_flow_conditions(d),
            chemical_constraints=[
                _chemical_constraint(x) for x in d.get("chemical_constraints", [])
            ],
            transport_conditions=_transport_conditions(d),
            spatial_assignment=_spatial_assignment(d),
            output=_output_config(output, d),
        )

    def validate(self, project_directory: str | Path | None = None) -> ValidationReport:
        return PFLOTRANReferenceValidator().validate(self, project_directory)


class PFLOTRANReferenceValidator:
    def validate(
        self, c: PFLOTRANConfig, project_directory: str | Path | None = None
    ) -> ValidationReport:
        # Import here to avoid a configuration type cycle.
        from .pflotran_validation import ReferenceValidator

        return ReferenceValidator().validate(c, project_directory)


def _process_numerics(data: dict[str, Any] | None) -> ProcessNumericsConfig:
    d = data or {}
    old_newton = d.get("newton", {}) or {}
    old_linear = d.get("linear", {}) or {}
    solver_type = str(old_linear.get("solver_type", old_linear.get("solver", "BCGS"))).upper()
    if solver_type == "BICGSTAB":
        solver_type = "BCGS"
    return ProcessNumericsConfig(
        newton=NewtonSolverConfig(
            maximum_iterations=_optional_int(old_newton.get("maximum_iterations", 15)),
            snes_type=_optional_upper(old_newton.get("snes_type", "LINE_SEARCH")),
            rtol=_optional_float(old_newton.get("rtol")),
            atol=_optional_float(old_newton.get("atol", 1e-50)),
            itol=_optional_float(old_newton.get("itol")),
            stol=_optional_float(
                old_newton.get(
                    "stol",
                    old_newton.get("update_tolerance", old_newton.get("relative_tolerance", 1e-08)),
                )
            ),
            dtol=_optional_float(old_newton.get("dtol", 10000.0)),
        ),
        linear=LinearSolverConfig(
            solver_type=solver_type,
            preconditioner=_optional_upper(old_linear.get("preconditioner", "ILU")),
            rtol=_optional_float(
                old_linear.get("rtol", old_linear.get("relative_tolerance", 1e-05))
            ),
            atol=_optional_float(old_linear.get("atol", 1e-50)),
            dtol=_optional_float(old_linear.get("dtol", 10000.0)),
        ),
    )


def _grid_config(data: dict[str, Any] | None) -> GridConfig:
    d = data or {}
    gravity_data = d.get("gravity", {})
    if isinstance(gravity_data, str):
        gravity_data = {"mode": gravity_data}
    return GridConfig(
        grid_type=str(d.get("grid_type", "unstructured_explicit")),
        file=str(d.get("file", "octree/full_mesh.uge")),
        gravity=GravityConfig(
            mode=str(gravity_data.get("mode", "standard")),
            vector=[float(value) for value in gravity_data.get("vector", [0.0, 0.0, -9.8068])],
        ),
    )


def _time_control(
    data: dict[str, Any] | None, numerical_methods: dict[str, Any]
) -> TimeControlConfig:
    d = data or {}
    old_timestep = (numerical_methods.get("flow") or {}).get("timestepper") or {}

    def number(value, fallback):
        return fallback if value is None else float(value)

    return TimeControlConfig(
        final_time=number(d.get("final_time"), None),
        final_unit=d.get("final_unit", "y"),
        initial_timestep=number(
            d.get(
                "initial_timestep",
                d.get("initial_timestep_value", old_timestep.get("initial_size", 1.0)),
            ),
            None,
        ),
        initial_timestep_unit=d.get("initial_timestep_unit", old_timestep.get("initial_unit", "h")),
        maximum_timestep=number(
            d.get(
                "maximum_timestep",
                d.get("maximum_timestep_value", old_timestep.get("maximum_size", 1.0)),
            ),
            None,
        ),
        maximum_timestep_unit=d.get("maximum_timestep_unit", old_timestep.get("maximum_unit", "y")),
    )


def _chemistry(
    data: dict[str, Any] | None, legacy_minerals: list[dict[str, Any]]
) -> ChemistryConfig:
    d = data or {}
    database_value = d.get("database", {})
    if isinstance(database_value, dict):
        database = ThermodynamicDatabaseConfig(
            relative_path=str(database_value.get("relative_path", "")),
            status=str(database_value.get("status", "not_loaded")),
            message=record_message(database_value),
        )
    else:
        database = ThermodynamicDatabaseConfig(
            relative_path=str(database_value),
            status="parsed" if database_value else "not_loaded",
            message="",
        )
    aqueous_value = d.get("aqueous_species", {}) or {}
    gas_value = d.get("gas_species", {}) or {}
    primary = list(aqueous_value.get("primary_species", d.get("primary_species", [])))
    secondary = list(aqueous_value.get("secondary_species", d.get("secondary_species", [])))
    passive = list(gas_value.get("passive_gases", d.get("passive_gas_species", [])))
    selected_minerals = list(d.get("selected_minerals", []))
    kinetics = []
    for item in d.get("mineral_kinetics", []):
        values = dict(item)
        values.setdefault(
            "surface_area_function",
            "mineral_mass" if values.get("specific_surface_area") is not None else "default",
        )
        kinetics.append(MineralKineticsConfig(**values))
    if legacy_minerals and (not selected_minerals):
        selected_minerals = [str(item.get("name", item.get("id", ""))) for item in legacy_minerals]
    if legacy_minerals and (not kinetics):
        for item in legacy_minerals:
            prefactors = item.get("prefactors", [])
            if not prefactors:
                continue
            prefactor = prefactors[0]
            terms = prefactor.get("species_terms", [])
            term = terms[0] if terms else {}
            kinetics.append(
                MineralKineticsConfig(
                    mineral_name=str(item.get("name", item.get("id", ""))),
                    rate_constant=float(prefactor.get("rate_constant", 0.0)),
                    rate_unit=str(prefactor.get("rate_unit", "mol/m^2-sec")),
                    surface_area_function="mineral_mass"
                    if prefactor.get("specific_surface_area") is not None
                    else "default",
                    specific_surface_area=_optional_float(prefactor.get("specific_surface_area")),
                    surface_area_unit=str(prefactor.get("surface_area_unit", "m2/kg")),
                    prefactor_species=term.get("species") or None,
                    alpha=float(term["alpha"]) if term.get("alpha") is not None else None,
                )
            )
    return ChemistryConfig(
        database=database,
        aqueous_species=AqueousSpeciesConfig(primary, secondary),
        gas_species=GasSpeciesConfig(passive),
        selected_minerals=selected_minerals,
        mineral_kinetics=kinetics,
        log_formulation=bool(d.get("log_formulation", True)),
        activity_coefficients=str(d.get("activity_coefficients", "default")),
        update_porosity=bool(d.get("update_porosity", False)),
        update_permeability=bool(d.get("update_permeability", False)),
    )


def _material(d: dict[str, Any]) -> MaterialConfig:
    tortuosity = d.get("tortuosity", 1.0)
    return MaterialConfig(
        id=d["id"],
        name=d["name"],
        material_id=int(d["material_id"]),
        porosity=PropertySource(**d.get("porosity", {})),
        permeability=PermeabilitySource(**d.get("permeability", {})),
        tortuosity=None if tortuosity is None else float(tortuosity),
        longitudinal_dispersivity=float(d.get("longitudinal_dispersivity", 0.0)),
        transverse_dispersivity_horizontal=float(d.get("transverse_dispersivity_horizontal", 0.0)),
        transverse_dispersivity_vertical=float(d.get("transverse_dispersivity_vertical", 0.0)),
        characteristic_curve_id=d.get("characteristic_curve_id"),
    )


def _chemical_constraint(d: dict[str, Any]) -> ChemicalConstraintConfig:
    minerals = []
    for item in d.get("minerals", d.get("initial_minerals", [])):
        values = dict(item)
        if "surface_area" not in values:
            values["surface_area"] = values.pop("specific_surface_area", 0.0)
        minerals.append(MineralConstraint(**values))
    return ChemicalConstraintConfig(
        d["id"],
        d["name"],
        [ConcentrationConstraint(**x) for x in d.get("concentrations", [])],
        minerals,
    )


def _transport_condition(
    d: dict[str, Any], constraints=None, usage="boundary"
) -> TransportConditionConfig:
    if "form" in d:
        return TransportConditionConfig(
            name=str(d.get("name", d.get("id", ""))),
            usage=str(d.get("usage", "boundary")),
            form=str(d.get("form", "inflow_solution")),
            constraint_id=str(d.get("constraint_id", "")),
            solution_components=[
                ConcentrationConstraint(**x) for x in d.get("solution_components", [])
            ],
            reference_solution=str(d.get("reference_solution", "")),
        )
    condition_type = str(d.get("condition_type", "ZERO_GRADIENT"))
    form = {
        "ZERO_GRADIENT": "zero_gradient_outflow",
        "DIRICHLET": "fixed_solution",
        "DIRICHLET_ZERO_GRADIENT": "inflow_solution",
    }.get(condition_type, "inflow_solution")
    components = []
    schedule = d.get("schedule", [])
    if schedule and constraints:
        constraint = constraints.get(schedule[0].get("constraint_id"))
        if constraint:
            components = deepcopy(constraint.concentrations)
    if usage == "initial":
        form = "initial_solution"
    elif usage == "source_sink":
        form = "injection_solution"
    constraint_id = str(schedule[0].get("constraint_id", "")) if schedule else ""
    return TransportConditionConfig(
        str(d.get("name", d.get("id", ""))), usage, form, components, "", constraint_id
    )


def _region(d: dict[str, Any]) -> RegionConfig:
    if "region_type" in d:
        values = dict(d)
        if values.get("message_data"):
            values["message"] = record_message(values)
            values.pop("message_data", None)
        if values.get("coordinate") is not None:
            values["coordinate"] = tuple(values["coordinate"])
        if values.get("bounds") is not None:
            values["bounds"] = tuple(values["bounds"])
        return RegionConfig(**values)
    old_type = str(d.get("geometry_type", "entire_domain"))
    mapping = {
        "point": "COORDINATE",
        "box": "COORDINATES",
        "cell_list": "LIST",
        "boundary_file": "FILE",
        "entire_domain": "COORDINATES",
    }
    coordinates = list(d.get("coordinates", []))
    return RegionConfig(
        name=str(d.get("name") or d.get("id") or ""),
        region_type=mapping.get(old_type, "COORDINATES"),
        source="system"
        if old_type == "entire_domain"
        else "auto"
        if old_type == "boundary_file"
        else "user",
        coordinate=tuple(coordinates[:3])
        if old_type == "point" and len(coordinates) >= 3
        else None,
        bounds=tuple(coordinates[:6])
        if old_type in {"box", "entire_domain"} and len(coordinates) >= 6
        else None,
        cell_ids=[int(value) for value in d.get("cell_ids", [])],
        file_path=str(d.get("file") or ""),
        file_region_kind="boundary_surface" if old_type == "boundary_file" else "",
    )


def _flow_condition(d: dict[str, Any]) -> FlowConditionConfig:
    if "form" in d:
        return FlowConditionConfig(
            str(d.get("name", d.get("id", ""))),
            str(d.get("usage", "boundary")),
            str(d["form"]),
            dict(d.get("parameters", {})),
            str(d.get("direction_semantics", "unknown")),
        )
    old = str(d.get("condition_type", "pressure"))
    form = {
        "pressure": "fixed_liquid_pressure",
        "hydrostatic": "hydrostatic_head",
        "liquid_flux": "darcy_flux",
        "volumetric_rate": "scaled_volumetric_rate",
        "scaled_volumetric_rate": "scaled_volumetric_rate",
    }.get(old, "fixed_liquid_pressure")
    parameters = {"value": d.get("value"), "unit": d.get("unit")}
    if d.get("datum"):
        parameters["datum"] = list(d["datum"])
    if d.get("scaling"):
        parameters["distribution"] = d["scaling"]
    return FlowConditionConfig(str(d.get("name", d.get("id", ""))), "boundary", form, parameters)


def direction_semantics(
    condition: FlowConditionConfig, reference_pressure: float | None = None
) -> str:
    explicit = condition.direction_semantics
    if explicit in {"inward", "outward", "expected_inward", "expected_outward"}:
        return explicit
    value = condition.parameters.get("value")
    if condition.form in {
        "darcy_flux",
        "scaled_volumetric_rate",
        "scaled_mass_rate",
    } and isinstance(value, (int, float)):
        return "inward" if value > 0 else "outward" if value < 0 else "unknown"
    if (
        condition.form == "fixed_liquid_pressure"
        and isinstance(value, (int, float))
        and (reference_pressure is not None)
    ):
        return (
            "expected_inward"
            if value > reference_pressure
            else "expected_outward"
            if value < reference_pressure
            else "unknown"
        )
    return "unknown"


def _flow_conditions(root: dict[str, Any]) -> list[FlowConditionConfig]:
    usage = {
        str(item.get("flow_condition_id")): str(item.get("coupler_type", "boundary"))
        for item in root.get("couplers", [])
        if item.get("flow_condition_id")
    }
    result = []
    for value in root.get("flow_conditions", []):
        condition = _flow_condition(value)
        if "usage" not in value:
            condition.usage = usage.get(str(value.get("id")), "boundary")
        result.append(condition)
    return result


def _transport_conditions(root: dict[str, Any]) -> list[TransportConditionConfig]:
    constraints = {
        _value.id: _value
        for _value in (_chemical_constraint(x) for x in root.get("chemical_constraints", []))
    }
    usage = {
        str(item.get("transport_condition_id")): str(item.get("coupler_type", "boundary"))
        for item in root.get("couplers", [])
        if item.get("transport_condition_id")
    }
    return [
        _transport_condition(value, constraints, usage.get(str(value.get("id")), "boundary"))
        for value in root.get("transport_conditions", [])
    ]


def _spatial_assignment(root: dict[str, Any]) -> SpatialAssignmentConfig:
    current = root.get("spatial_assignment") or {}
    if current:
        mapping = current.get("material_mapping")
        material_mapping = None
        if mapping:
            material_mapping = MaterialMappingSummary(
                mode=str(mapping.get("mode", "material_file")),
                file_path=str(mapping.get("file_path", "")),
                active_cell_count=int(mapping.get("active_cell_count", 0)),
                mapped_cell_count=int(mapping.get("mapped_cell_count", 0)),
                unmapped_cell_count=int(mapping.get("unmapped_cell_count", 0)),
                items=[MaterialMappingItem(**item) for item in mapping.get("items", [])],
                status=str(mapping.get("status", "not_loaded")),
                message=record_message(mapping),
            )
        return SpatialAssignmentConfig(
            [
                SpatialConditionAssignment(**item)
                for item in current.get("condition_assignments", [])
            ],
            material_mapping,
        )
    assignments = []
    for item in root.get("couplers", []):
        assignments.append(
            SpatialConditionAssignment(
                name=str(item.get("name") or item.get("id") or ""),
                usage=str(item.get("coupler_type", "initial")),
                region_name=str(item.get("region_id", "")),
                flow_condition_name=item.get("flow_condition_id") or None,
                transport_condition_name=item.get("transport_condition_id") or None,
            )
        )
    legacy_strata = root.get("strata", [])
    material_file = next(
        (
            item.get("material_file")
            for item in legacy_strata
            if item.get("mode") == "material_file" and item.get("material_file")
        ),
        "",
    )
    discarded = [item for item in legacy_strata if item.get("mode") != "material_file"]
    message = msg("config.legacy_assignments", count=len(discarded)) if discarded else ""
    mapping = (
        MaterialMappingSummary(
            file_path=str(material_file),
            status="warning" if discarded else "not_loaded",
            message=message,
        )
        if material_file or discarded
        else None
    )
    return SpatialAssignmentConfig(assignments, mapping)


def _output_config(d: dict[str, Any], root: dict[str, Any]) -> OutputConfig:
    if "schedule" in d or "flow_variables" in d or "chemistry_variables" in d:
        schedule = _schedule_config(d.get("schedule", {}))
        flow_variables = FlowOutputVariables(**d.get("flow_variables", {}))
        chemistry_variables = ChemistryOutputVariables(**d.get("chemistry_variables", {}))
        mass_file = d.get("mass_balance_file", {})
        observation = d.get("observation", {})
        legacy_observation_regions = list(d.get("observation_regions", []))
        observation_flow = observation.get("flow_variables", d.get("flow_variables", {}))
        observation_chemistry = observation.get(
            "chemistry_variables", d.get("chemistry_variables", {})
        )
        return OutputConfig(
            snapshot_format=_output_format(d.get("snapshot_format", "VTK")),
            schedule=schedule,
            print_initial=bool(d.get("print_initial", True)),
            print_final=bool(d.get("print_final", True)),
            velocity_at_center=bool(d.get("velocity_at_center", flow_variables.velocity)),
            mass_flowrate=bool(d.get("mass_flowrate", True)),
            mass_balance=bool(d.get("mass_balance", True)),
            mass_balance_file=MassBalanceFileConfig(
                enabled=bool(mass_file.get("enabled", False)),
                schedule=_schedule_config(mass_file.get("schedule", {}), "periodic_timestep", 1),
                regions=list(mass_file.get("regions", [])),
                print_initial=bool(mass_file.get("print_initial", True)),
                print_final=bool(mass_file.get("print_final", True)),
            ),
            observation=ObservationFileConfig(
                enabled=bool(observation.get("enabled", legacy_observation_regions)),
                regions=list(observation.get("regions", legacy_observation_regions)),
                schedule=_schedule_config(observation.get("schedule", d.get("schedule", {}))),
                print_initial=bool(observation.get("print_initial", True)),
                print_final=bool(observation.get("print_final", True)),
                flow_variables=FlowOutputVariables(**observation_flow),
                chemistry_variables=ChemistryOutputVariables(**observation_chemistry),
            ),
            save_final_input=bool(d.get("save_final_input", True)),
            flow_variables=flow_variables,
            chemistry_variables=chemistry_variables,
        )
    snapshot = d.get("snapshot", {})
    old_schedule = snapshot.get("schedule", {})
    mode = {"times": "specified_times"}.get(
        str(old_schedule.get("mode", d.get("output_plan", "periodic_time"))),
        str(old_schedule.get("mode", d.get("output_plan", "periodic_time"))),
    )
    old_flow = set(
        snapshot.get(
            "variables", d.get("result_variables", ["LIQUID_PRESSURE", "LIQUID_SATURATION"])
        )
    )
    old_chem = set(d.get("chemistry_variables", ["PH", "TOTAL", "MINERALS"]))
    observation = d.get("observation", {})
    save_input = d.get(
        "save_input_record", root.get("simulation", {}).get("input_record_file", True)
    )
    legacy_flow = FlowOutputVariables(
        "LIQUID_PRESSURE" in old_flow,
        True,
        "LIQUID_SATURATION" in old_flow,
        False,
        "POROSITY" in old_flow,
        "PERMEABILITY" in old_flow,
    )
    legacy_chemistry = ChemistryOutputVariables(
        ph="PH" in old_chem,
        all_primary_species="TOTAL" in old_chem,
        all_species="ALL" in old_chem,
        secondary_species="SECONDARY_SPECIES" in old_chem,
        mineral_volume_fraction="MINERALS" in old_chem,
        mineral_rate="REACTION_RATES" in old_chem,
        mineral_saturation_index="SATURATION_INDEX" in old_chem,
    )
    legacy_regions = list(observation.get("region_ids", d.get("observation_regions", [])))
    return OutputConfig(
        snapshot_format=_output_format(snapshot.get("format", d.get("snapshot_format", "VTK"))),
        schedule=OutputScheduleConfig(
            mode=mode,
            interval_value=float(old_schedule.get("interval", d.get("output_interval", 1.0))),
            interval_unit=str(old_schedule.get("unit", d.get("time_unit", "d"))),
            specified_times=[
                float(x) for x in old_schedule.get("times", d.get("specified_times", []))
            ],
            specified_times_unit=str(old_schedule.get("unit", d.get("time_unit", "d"))),
            timestep_interval=max(
                1, int(old_schedule.get("interval", d.get("timestep_interval", 10)))
            )
            if mode == "periodic_timestep"
            else 10,
        ),
        velocity_at_center="VELOCITY" in old_flow,
        mass_flowrate=bool(d.get("mass_flowrate", True)),
        mass_balance=bool(d.get("mass_balance", True)),
        observation=ObservationFileConfig(
            enabled=bool(legacy_regions),
            regions=legacy_regions,
            schedule=_schedule_config(old_schedule),
            flow_variables=deepcopy(legacy_flow),
            chemistry_variables=deepcopy(legacy_chemistry),
        ),
        save_final_input=bool(save_input),
        flow_variables=legacy_flow,
        chemistry_variables=legacy_chemistry,
    )


def _schedule_config(
    value: dict[str, Any] | None, default_mode: str = "periodic_time", default_timestep: int = 10
) -> OutputScheduleConfig:
    current = value or {}
    return OutputScheduleConfig(
        mode=str(current.get("mode", default_mode)),
        interval_value=float(current.get("interval_value", current.get("interval", 1.0))),
        interval_unit=str(current.get("interval_unit", current.get("unit", "d"))),
        specified_times=[
            float(x) for x in current.get("specified_times", current.get("times", []))
        ],
        specified_times_unit=str(current.get("specified_times_unit", current.get("unit", "d"))),
        timestep_interval=max(
            1, int(current.get("timestep_interval", current.get("interval", default_timestep)))
        ),
    )


def _output_format(value: Any) -> str:
    normalized = str(value).upper().replace(" ", "_")
    return normalized if normalized in {"VTK", "HDF5"} else "VTK"


def _optional_float(value: Any) -> float | None:
    return None if value is None or value == "" else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None or value == "" else int(value)


def _optional_upper(value: Any) -> str | None:
    return None if value is None or str(value).strip() == "" else str(value).upper()
