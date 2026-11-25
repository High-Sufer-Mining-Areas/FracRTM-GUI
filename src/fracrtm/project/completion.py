from __future__ import annotations

import json
import re
from pathlib import Path

from fracrtm.messages import message as msg
from fracrtm.visualization.result_times import NUMBER, physical_time

ERROR = re.compile(
    r"^\s*(?:\[\d+\]\s*)?(?:PFLOTRAN\s+ERROR\b|PETSC\s+ERROR\b|ERROR\s*:|FATAL(?:\s+ERROR)?\s*:)",
    re.I,
)
STEP_TIME = re.compile(
    r"\bTime\s*=\s*(" + NUMBER + r")\s+Dt\s*=\s*" + NUMBER + r"\s*\[\s*([A-Za-z]+)\s*\]", re.I
)
STEADY_STEP = re.compile(
    r"^\s*Step\s+\d+\s+Time\s*=\s*("
    + NUMBER
    + r")\s*\[\s*([A-Za-z]+)\s*\]\s+snes_conv_reason:\s*([+-]?\d+)\s*$",
    re.I,
)
STEADY = re.compile(
    r"\bsteady[\s-]*(?:state)?\b.*\bconverged\b|\bCONVERGED_(?:FNORM_ABS|FNORM_RELATIVE|SNORM_RELATIVE)\b",
    re.I,
)

# PFLOTRAN reaction.F90 at fd351a49: ALL selects primary species and minerals,
# not SECONDARY_SPECIES. Only the locally audited executable is covered.
VERIFIED_ALL_OUTPUT_SOLVERS = {
    "b7874ba9c80090a8fe92f6b0c2f62fb857723c2c4235a3e1fd66421f81de401a",
}


def _check_all_output_contract(task, config):
    chemistry = config.get("output", {}).get("chemistry_variables", {})
    if not chemistry.get("all_species"):
        return
    try:
        record = json.loads(task.record_path.read_text(encoding="utf-8"))
        digest = record["execution_environment"]["tools"]["PFLOTRAN"]["sha256"]
    except (AttributeError, OSError, ValueError, KeyError, TypeError):
        digest = None
    if digest not in VERIFIED_ALL_OUTPUT_SOLVERS:
        raise ValueError(msg("repair.all_species_schema"))


def _chemical_field(name):
    # Preserve charge signs and exact species names (Tracer is not Tracer1).
    return re.sub(r"\s+", "_", name.split("[", 1)[0].strip().casefold())


def scan_log(path, *, calendar_year_seconds=None):
    result = {
        "last_time_seconds": None,
        "last_time_value": None,
        "last_time_unit": None,
        "steady_converged": False,
        "normal_end": False,
        "error": "",
        "lines": 0,
        "snapshot_times_seconds": {},
    }
    following = 0
    lines = []
    with Path(path).open("r", encoding="utf-8", errors="replace") as stream:
        for line_number, line in enumerate(stream, 1):
            result["lines"] = line_number
            if not result["error"] and ERROR.search(line):
                result["error"] = line[:16000].rstrip()
                following = 2
            elif following:
                lines.append(line[:16000].rstrip())
                following -= 1
            if re.match(r"^\s*Step\s+\d+\s+Time", line, re.I):
                steady = STEADY_STEP.match(line)
                if steady:
                    # PETSc reasons 2/3/4 satisfy residual or step-size convergence.
                    result["steady_converged"] = int(steady.group(3)) in {2, 3, 4}
                match = STEP_TIME.search(line) or steady
                if match:
                    value, unit = match.group(1, 2)
                    try:
                        result["last_time_seconds"] = physical_time(
                            value.replace("D", "E").replace("d", "e"),
                            unit,
                            calendar_year_seconds=calendar_year_seconds,
                        )
                    except (TypeError, ValueError):
                        result["last_time_seconds"] = None
                    result["last_time_value"], result["last_time_unit"] = value, unit
            if STEADY.search(line) and not re.search(
                r"not\s+converged|unconverged|diverged", line, re.I
            ):
                result["steady_converged"] = True
            match = re.search(r"write vtk output file:\s*(\S+\.vtk)\s*$", line, re.I)
            if match and result["last_time_seconds"] is not None:
                name = Path(match.group(1)).name
                result["snapshot_times_seconds"].setdefault(name, []).append(
                    result["last_time_seconds"]
                )
            if re.search(r"\bSimulation\s+complete(?:d)?\b", line, re.I) or re.fullmatch(
                r"\s*Wall Clock Time:\s*"
                + NUMBER
                + r"\s*\[sec\]\s*"
                + NUMBER
                + r"\s*\[min\]\s*"
                + NUMBER
                + r"\s*\[hr\]\s*",
                line,
            ):
                result["normal_end"] = True
    if lines:
        result["error"] += "\n" + "\n".join(lines)
    return result


def completion_evidence(task, log):
    from fracrtm.config.time_units import CALENDAR_UNITS, run_calendar_year_seconds

    calendar = run_calendar_year_seconds(task)
    config_path = task.snapshot_directory / "pflotran/model_config.json"
    if not config_path.is_file():
        return (
            "unverified",
            {"reason": "missing_frozen_config"},
            msg("repair.completion_config_missing"),
        )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    mode = config.get("simulation", {}).get("flow_state")
    evidence = {"mode": mode, "log": log}
    output = config.get("output", {})
    format_name = str(output.get("snapshot_format", "VTK")).upper()
    suffixes = {".vtk", ".vtu"} if format_name == "VTK" else {".h5", ".hdf5"}
    snapshots = [
        p
        for p in task.output_directory.rglob("*")
        if p.is_file()
        and p.relative_to(task.output_directory).parts[0] != "visualization"
        and p.suffix.lower() in suffixes
        and not p.name.startswith("model-vel-")
        and p.stat().st_size
    ]
    evidence["snapshots_found"] = len(snapshots)
    if output.get("print_final", True) and not snapshots:
        return "incomplete", evidence, msg("repair.final_snapshot_missing")
    if snapshots:
        try:
            validate_snapshots(task, config, snapshots)
        except (OSError, ValueError, TypeError, KeyError, MemoryError) as exc:
            evidence["snapshot_issue"] = str(exc)
            return "unverified", evidence, msg("repair.snapshot_invalid", reason=exc)
    if format_name == "VTK" and (
        output.get("velocity_at_center") or output.get("flow_variables", {}).get("velocity")
    ):
        import numpy as np

        from fracrtm.visualization.pflotran_results import VTKResultReader

        try:
            for path in snapshots:
                velocity = path.with_name(path.name.replace("model-", "model-vel-", 1))
                state = VTKResultReader().read(path)
                values = VTKResultReader().read(velocity)
                if values.value_count != state.value_count or not {"Vlx", "Vly", "Vlz"}.issubset(
                    values.arrays
                ):
                    raise ValueError(msg("repair.requested_output_missing"))
                if any(not np.all(np.isfinite(value)) for value in values.arrays.values()):
                    raise ValueError(msg("repair.finite_number"))
        except FileNotFoundError:
            return "incomplete", evidence, msg("repair.requested_output_missing")
        except (OSError, ValueError):
            return "unverified", evidence, msg("repair.optional_output_unverified")
    if mode == "steady_state":
        if log["steady_converged"] and log["normal_end"]:
            try:
                validate_optional_outputs(task, output, calendar_year_seconds=calendar)
            except FileNotFoundError:
                return "incomplete", evidence, msg("repair.requested_output_missing")
            except (OSError, ValueError):
                return "unverified", evidence, msg("repair.optional_output_unverified")
            return "verified", evidence, ""
        return "unverified", evidence, msg("repair.steady_unverified")
    if mode != "transient":
        return "unverified", evidence, msg("repair.completion_config_missing")
    if not log["normal_end"]:
        return "unverified", evidence, msg("repair.normal_end_missing")
    time = config.get("time", {})
    target = time.get("final_time")
    unit = time.get("final_unit")
    if calendar is None and (unit in CALENDAR_UNITS or log.get("last_time_unit") in CALENDAR_UNITS):
        return "unverified", evidence, msg("repair.calendar_unverified")
    evidence["calendar_year_seconds"] = calendar
    try:
        target_seconds = physical_time(target, unit, calendar_year_seconds=calendar)
    except (TypeError, ValueError):
        return "unverified", evidence, msg("repair.completion_config_missing")
    observed = log["last_time_seconds"]
    evidence["target_seconds"] = target_seconds
    if observed is None:
        return "unverified", evidence, msg("repair.time_unverified")
    tolerance = max(1e-9, abs(target_seconds) * 1e-9)
    evidence["time_tolerance_seconds"] = tolerance
    if abs(observed - target_seconds) > tolerance:
        return (
            "incomplete",
            evidence,
            msg("repair.time_incomplete", expected=target_seconds, actual=observed),
        )
    if output.get("print_final", True):
        times = []
        for path in snapshots:
            try:
                times.extend(snapshot_times(path))
            except (OSError, ValueError):
                continue
        stdout = getattr(task, "directory", task.output_directory.parent) / "stdout.log"
        if format_name == "VTK" and stdout.is_file():
            trace = scan_log(stdout, calendar_year_seconds=calendar)
            if trace["error"]:
                return "incomplete", evidence, trace["error"]
            observed = {}
            for path in snapshots:
                candidates = trace["snapshot_times_seconds"].get(path.name, [])
                if len(candidates) == 1:
                    observed[path.name] = candidates[0]
                    times.append(candidates[0])
            evidence["snapshot_observed_times"] = observed
            evidence["snapshot_time_source"] = "solver_stdout_file_write_events"
        evidence["snapshot_times_seconds"] = times
        if not times:
            return "unverified", evidence, msg("repair.snapshot_time_unverified")
        if not any(abs(time - target_seconds) <= tolerance for time in times):
            return "incomplete", evidence, msg("repair.final_snapshot_missing")
    try:
        validate_optional_outputs(
            task,
            output,
            target_seconds=target_seconds,
            tolerance=tolerance,
            calendar_year_seconds=calendar,
        )
    except FileNotFoundError:
        return "incomplete", evidence, msg("repair.requested_output_missing")
    except (OSError, ValueError):
        return "unverified", evidence, msg("repair.optional_output_unverified")
    return "verified", evidence, ""


def snapshot_times(path):
    from fracrtm.visualization.result_times import time_from_hdf5_group, time_from_text

    path = Path(path)
    if path.suffix.lower() in {".vtk", ".vtu"}:
        lines = []
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for _ in range(40):
                line = stream.readline()
                if not line:
                    break
                lines.append(line)
        time = time_from_text("".join(lines))
        return [] if time is None else [time]
    import h5py

    times = []
    with h5py.File(path, "r") as stream:
        for name, group in stream.items():
            if isinstance(group, h5py.Group):
                time = time_from_hdf5_group(name, group)
                if time is not None:
                    times.append(time)
    return times


def validate_snapshots(task, config, paths):
    import numpy as np

    from fracrtm.runtime.resource_limits import conversion_budget
    from fracrtm.visualization.pflotran_results import HDF5ResultReader, VTKResultReader
    from fracrtm.visualization.spatial_mapping import read_uge, safe_resource

    conversion_budget(paths)
    _check_all_output_contract(task, config)
    grid = config.get("grid", {}).get("file")
    count = None
    if grid:
        count = len(read_uge(safe_resource(task.snapshot_directory, grid)).ids)
    for path in paths:
        if path.suffix.lower() == ".vtk":
            snapshots = [VTKResultReader().read(path)]
        elif path.suffix.lower() in {".h5", ".hdf5"}:
            if count is None:
                raise ValueError(msg("repair.completion_config_missing"))
            snapshots = HDF5ResultReader().read_all(path, count)
        else:
            raise ValueError(msg("repair.snapshot_time_unverified"))
        for snapshot in snapshots:
            fields = {
                re.sub(r"[^a-z0-9]+", "_", name.split("[", 1)[0].lower()).strip("_")
                for name in snapshot.arrays
            }
            requested = config.get("output", {}).get("flow_variables", {})
            missing = [
                name
                for name, enabled in requested.items()
                if enabled
                and name != "velocity"
                and not any(field == name or field.startswith(name + "_") for field in fields)
            ]
            if path.suffix.lower() in {".h5", ".hdf5"} and (
                config.get("output", {}).get("velocity_at_center") or requested.get("velocity")
            ):
                missing.extend(
                    "liquid_" + axis + "_velocity"
                    for axis in "xyz"
                    if "liquid_" + axis + "_velocity" not in fields
                )
            chemistry = config.get("output", {}).get("chemistry_variables", {})
            chemical_fields = {_chemical_field(name) for name in snapshot.arrays}
            if config.get("simulation", {}).get("task_type") in {
                "conservative_transport",
                "reactive_transport",
            }:
                if chemistry.get("ph") and "ph" not in fields:
                    missing.append("pH")
                if chemistry.get("all_primary_species"):
                    for species in (
                        config.get("chemistry", {})
                        .get("aqueous_species", {})
                        .get("primary_species", [])
                    ):
                        name = "total_" + _chemical_field(species)
                        if name not in chemical_fields:
                            missing.append(name)
                for species in (
                    config.get("chemistry", {})
                    .get("aqueous_species", {})
                    .get("secondary_species", [])
                ):
                    if chemistry.get("secondary_species"):
                        name = _chemical_field(species)
                        if (
                            name not in chemical_fields
                            and "secondary_" + name not in chemical_fields
                        ):
                            missing.append(name)
                for mineral in config.get("chemistry", {}).get("selected_minerals", []):
                    name = _chemical_field(mineral)
                    kinetic = any(
                        item.get("mineral_name") == mineral
                        for item in config.get("chemistry", {}).get("mineral_kinetics", [])
                    )
                    for setting, endings in [
                        ("mineral_volume_fraction", ("vf", "volume_fraction")),
                        ("mineral_rate", ("rate", "mineral_rate")),
                        ("mineral_saturation_index", ("si", "saturation_index")),
                    ]:
                        required = chemistry.get(setting) or (
                            chemistry.get("all_species")
                            and setting == "mineral_volume_fraction"
                            and kinetic
                        )
                        if required and not any(
                            name + "_" + ending in chemical_fields
                            or ending + "_" + name in chemical_fields
                            for ending in endings
                        ):
                            missing.append(name + ":" + setting)
            if missing:
                raise ValueError(msg("repair.snapshot_fields_missing", fields=", ".join(missing)))
            if snapshot.value_count < 1 or count is not None and snapshot.value_count != count:
                raise ValueError(msg("repair.hdf5_id_count"))
            if not snapshot.arrays or any(
                not np.all(np.isfinite(value)) for value in snapshot.arrays.values()
            ):
                raise ValueError(msg("repair.finite_number"))


def validate_optional_outputs(
    task, output, target_seconds=None, tolerance=1e-9, calendar_year_seconds=None
):
    import math

    for setting, pattern in (("mass_balance_file", "model*mas*"), ("observation", "model*obs*")):
        specification = output.get(setting, {})
        if not specification.get("enabled", False):
            continue
        paths = [
            path
            for path in task.output_directory.glob(pattern)
            if path.is_file() and path.stat().st_size
        ]
        if not paths:
            raise FileNotFoundError(msg("repair.requested_output_missing"))
        for path in paths:
            unit = None
            previous_time = None
            columns = None
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if columns is None:
                        match = re.search(r"Time\s*[\[(]\s*([A-Za-z]+)\s*[\])]", line, re.I)
                        if match:
                            unit = match.group(1)
                    if not line.strip() or line.lstrip().startswith("#"):
                        continue
                    try:
                        numbers = [
                            float(value.replace("D", "E").replace("d", "e"))
                            for value in line.replace(",", " ").split()
                        ]
                    except ValueError:
                        if columns is not None:
                            raise ValueError(msg("repair.optional_output_unverified")) from None
                        continue
                    if (
                        not numbers
                        or not all(math.isfinite(value) for value in numbers)
                        or unit is None
                    ):
                        raise ValueError(msg("repair.optional_output_unverified"))
                    if columns is not None and columns != len(numbers):
                        raise ValueError(msg("repair.optional_output_unverified"))
                    columns = len(numbers)
                    current = physical_time(
                        numbers[0], unit, calendar_year_seconds=calendar_year_seconds
                    )
                    if previous_time is not None and current < previous_time:
                        raise ValueError(msg("repair.optional_output_unverified"))
                    previous_time = current
            if (
                previous_time is None
                or target_seconds is not None
                and specification.get("print_final", True)
                and abs(previous_time - target_seconds) > tolerance
            ):
                raise ValueError(msg("repair.optional_output_unverified"))
