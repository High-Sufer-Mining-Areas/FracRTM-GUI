from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Integral, Real

from fracrtm.messages import message as msg

LENGTH_TO_METRES = {"m": 1.0, "cm": 0.01, "mm": 0.001, "km": 1000.0}
PRESSURE_UNITS = frozenset({"Pa", "kPa", "MPa", "bar", "atm"})
FLOW_UNITS = {
    "fixed_liquid_pressure": ("Pa", "kPa", "MPa", "bar", "atm"),
    "hydrostatic_head": ("Pa", "kPa", "MPa", "bar", "atm"),
    "seepage": ("Pa", "kPa", "MPa", "bar", "atm"),
    "darcy_flux": ("m/s", "m/d", "m/day", "cm/s", "cm/d", "cm/day", "m/yr", "cm/yr", "m/y", "cm/y"),
    "scaled_volumetric_rate": ("m^3/s", "m^3/d", "m^3/day", "m3/s", "m3/d", "L/s", "L/d", "L/day"),
    "scaled_mass_rate": ("kg/s", "kg/d", "kg/day", "g/s", "g/d", "g/day"),
}


def canonical_flow_unit(value):
    return value.replace("³", "^3") if isinstance(value, str) else value


FLOW_FORMS_BY_USAGE = {
    "initial": frozenset({"fixed_liquid_pressure", "hydrostatic_head"}),
    "boundary": frozenset({"fixed_liquid_pressure", "hydrostatic_head", "darcy_flux", "seepage"}),
    "source_sink": frozenset({"scaled_volumetric_rate", "scaled_mass_rate"}),
}
TRANSPORT_USAGE = {
    "initial_solution": "initial",
    "inflow_solution": "boundary",
    "fixed_solution": "boundary",
    "zero_gradient_outflow": "boundary",
    "injection_solution": "source_sink",
}


def canonical_constraint_type(value):
    return "P" if value == "pH" else str(value).upper()


CONSTRAINT_TYPES = ("T", "P", "Z", "F", "G", "M", "L", "PE")
MINERAL_SURFACE_UNITS = (
    "m2/m3",
    "m^2/m^3",
    "cm2/cm3",
    "cm^2/cm^3",
    "m2/kg",
    "m^2/kg",
    "cm2/g",
    "cm^2/g",
    "m2/g",
    "m^2/g",
)
CURVE_MODELS = frozenset({"default", "van_genuchten", "brooks_corey"})
PERMEABILITY_FUNCTIONS = frozenset(
    {"MUALEM", "BURDINE", "MUALEM_VG_LIQ", "BURDINE_VG_LIQ", "MUALEM_BC_LIQ", "BURDINE_BC_LIQ"}
)
RATE_DISTRIBUTIONS = frozenset(
    {
        "VOLUME",
        "PERM",
        "NEIGHBOR_PERM",
        "NEIGHBOR_PERM_XY",
        "NEIGHBOR_PERM_XZ",
        "NEIGHBOR_PERM_YZ",
        "NEIGHBOR_PERM_XYZ",
    }
)


def finite_real(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def strict_integer(value):
    return isinstance(value, Integral) and not isinstance(value, bool)


def positive(value):
    return finite_real(value) and value > 0


@dataclass(frozen=True)
class FlowValues:
    value: float
    unit: str
    datum: tuple[float, float, float] | None = None
    distribution: str = "VOLUME"


def flow_parameter_errors(condition):
    p = condition.parameters
    if not isinstance(p, dict):
        return [("parameters", msg("repair.flow_parameters"))]
    errors = []
    hydro = condition.form in {"hydrostatic_head", "seepage"}
    key = "reference_pressure" if hydro and p.get("reference_pressure") is not None else "value"
    value = p.get(key)
    if not finite_real(value):
        errors.append((f"parameters.{key}", msg("repair.finite_number")))
    if hydro and p.get("reference_pressure") is not None and p.get("value") is not None:
        if (
            finite_real(p["reference_pressure"])
            and finite_real(p["value"])
            and p["reference_pressure"] != p["value"]
        ):
            errors.append(("parameters.reference_pressure", msg("repair.pressure_conflict")))
    unit = canonical_flow_unit(p.get("unit"))
    permitted = FLOW_UNITS.get(condition.form, ())
    if unit not in permitted:
        errors.append(("parameters.unit", msg("repair.flow_unit", form=condition.form, unit=unit)))
    if hydro:
        if p.get("length_unit", "m") not in LENGTH_TO_METRES:
            errors.append(("parameters.length_unit", msg("repair.length_unit")))
        datum = p.get("datum")
        if datum is not None:
            if (
                not isinstance(datum, (list, tuple))
                or len(datum) != 3
                or not all(finite_real(v) for v in datum)
            ):
                errors.append(("parameters.datum", msg("repair.datum")))
        else:
            z = p.get("reference_z", p.get("head_elevation"))
            if not finite_real(z):
                errors.append(("parameters.head_elevation", msg("repair.finite_number")))
            for axis in ["reference_x", "reference_y"]:
                if axis in p and not finite_real(p[axis]):
                    errors.append((f"parameters.{axis}", msg("repair.finite_number")))
    if (
        condition.form in {"scaled_mass_rate", "scaled_volumetric_rate"}
        and p.get("distribution", "VOLUME") not in RATE_DISTRIBUTIONS
    ):
        errors.append(("parameters.distribution", msg("repair.rate_distribution")))
    return errors


def flow_values(condition, config):
    errors = flow_parameter_errors(condition)
    if errors:
        raise ValueError(errors[0][1])
    p = condition.parameters
    hydro = condition.form in {"hydrostatic_head", "seepage"}
    key = "reference_pressure" if hydro and p.get("reference_pressure") is not None else "value"
    datum = None
    if hydro:
        scale = LENGTH_TO_METRES[p.get("length_unit", "m")]
        if p.get("datum") is not None:
            datum = tuple(float(v) * scale for v in p["datum"])
        else:
            bounds = next(
                (r.bounds for r in config.regions if r.name == "all" and r.bounds), (0.0,) * 6
            )
            # Grid bounds use metres; convert only user-entered coordinates.
            x = (
                float(p["reference_x"]) * scale
                if "reference_x" in p
                else (bounds[0] + bounds[3]) / 2
            )
            y = (
                float(p["reference_y"]) * scale
                if "reference_y" in p
                else (bounds[1] + bounds[4]) / 2
            )
            datum = (x, y, float(p.get("reference_z", p.get("head_elevation"))) * scale)
    return FlowValues(
        float(p[key]), canonical_flow_unit(p["unit"]), datum, p.get("distribution", "VOLUME")
    )


def numeric_type_issues(config):
    import types
    from dataclasses import fields, is_dataclass
    from typing import Union, get_args, get_origin, get_type_hints

    issues = []

    def check(value, annotation, path):
        args = get_args(annotation)
        origin = get_origin(annotation)
        if origin in {Union, types.UnionType}:
            if value is None and type(None) in args:
                return
            candidates = [part for part in args if part is not type(None)]
            if len(candidates) == 1:
                return check(value, candidates[0], path)
        if annotation is bool:
            if not isinstance(value, bool):
                issues.append((path, msg("repair.boolean_field", path=path)))
        elif annotation is str:
            if not isinstance(value, str):
                issues.append((path, msg("repair.string_field", path=path)))
        elif annotation in {float, int}:
            valid = finite_real(value) if annotation is float else strict_integer(value)
            if not valid:
                issues.append(
                    (path, msg("repair.finite_number" if annotation is float else "repair.integer"))
                )
        elif is_dataclass(value):
            hints = get_type_hints(type(value))
            for field in fields(value):
                if type(value).__name__ == "MaterialMappingSummary" and field.name not in {
                    "mode",
                    "file_path",
                }:
                    continue
                check(
                    getattr(value, field.name),
                    hints.get(field.name),
                    f"{path}.{field.name}" if path else field.name,
                )
        elif origin in {list, tuple} and not isinstance(value, (list, tuple)):
            issues.append((path, msg("repair.numeric_field", path=path)))
        elif origin in {list, tuple} and isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                item_type = (
                    args[0]
                    if origin is list or len(args) == 2 and args[-1] is Ellipsis
                    else args[min(i, len(args) - 1)]
                    if args
                    else None
                )
                check(item, item_type, f"{path}[{i}]")

    check(config, type(config), "")
    return issues


_NUMERIC_FIELDS = frozenset(
    {
        "value",
        "final_time",
        "initial_timestep",
        "initial_timestep_value",
        "maximum_timestep",
        "maximum_timestep_value",
        "head_elevation",
        "reference_pressure",
        "reference_x",
        "reference_y",
        "reference_z",
        "alpha",
        "m_or_lambda",
        "liquid_residual_saturation",
        "maximum_capillary_pressure",
        "tortuosity",
        "longitudinal_dispersivity",
        "transverse_dispersivity_horizontal",
        "transverse_dispersivity_vertical",
        "surface_area",
        "specific_surface_area",
        "volume_fraction",
        "rate_constant",
        "diffusion_coefficient",
        "rtol",
        "atol",
        "itol",
        "stol",
        "dtol",
        "interval_value",
        "p32",
        "probability",
        "radii",
        "radius",
        "aspect",
        "aspect_ratio",
        "beta",
        "dip",
        "strike",
        "theta",
        "phi",
        "trend",
        "plunge",
        "kappa",
        "log_mean",
        "log_std",
        "exp_mean",
        "min_radius",
        "max_radius",
        "constant_radius",
        "h",
        "base_cell_size",
        "matrix_permeability",
        "matrix_porosity",
        "isotropic",
        "permeability",
    }
)
_INTEGER_FIELDS = frozenset(
    {
        "material_id",
        "processes",
        "maximum_iterations",
        "timestep_interval",
        "refinement_levels",
        "n_poly",
        "number_of_points",
        "beta_distribution",
        "seed",
    }
)
_NUMERIC_ARRAYS = frozenset(
    {
        "datum",
        "vector",
        "diagonal",
        "coordinate",
        "bounds",
        "translation",
        "normal_vector",
        "domain_size",
        "domain_size_increase",
        "specified_times",
    }
)


def check_serialized_numbers(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            location = f"{path}.{key}" if path else key
            if key.endswith("_data"):
                continue
            if path.endswith(("flow_variables", "chemistry_variables")):
                if not isinstance(item, bool):
                    raise ValueError(msg("repair.boolean_field", path=location))
                continue
            if key in _NUMERIC_FIELDS and item is not None and not isinstance(item, dict):
                if not finite_real(item):
                    raise ValueError(msg("repair.numeric_field", path=location))
            if key in _INTEGER_FIELDS and item is not None and not strict_integer(item):
                raise ValueError(msg("repair.numeric_field", path=location))
            if key in _NUMERIC_ARRAYS and item is not None:
                if not isinstance(item, (list, tuple)) or any(
                    v is not None and not finite_real(v) for v in item
                ):
                    raise ValueError(msg("repair.numeric_field", path=location))
            if key == "cell_ids" and item is not None:
                if not isinstance(item, (list, tuple)) or any(not strict_integer(v) for v in item):
                    raise ValueError(msg("repair.numeric_field", path=location))
            check_serialized_numbers(item, location)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            check_serialized_numbers(item, f"{path}[{index}]")


def unassigned_boundary_placeholder(config, region):
    references = {item.region_name for item in config.spatial_assignment.condition_assignments}
    references.update(config.output.observation.regions)
    references.update(config.output.mass_balance_file.regions)
    return (
        region.name in {"top", "bottom", "west", "east", "north", "south"}
        and region.source == "auto"
        and region.region_type == "FILE"
        and not region.file_path
        and region.name not in references
    )
