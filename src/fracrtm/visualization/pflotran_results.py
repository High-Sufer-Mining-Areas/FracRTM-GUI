from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from fracrtm.messages import join_text, prepare_json
from fracrtm.messages import message as msg

from .result_times import (
    TIME_FACTORS,
    order_snapshots,
    time_from_hdf5_group,
    time_from_text,
    time_labels,
)
from .spatial_mapping import (
    coordinate_mapping,
    file_fingerprint,
    hdf5_cell_ids,
    is_cell_id_name,
    read_uge,
    reorder_by_ids,
    validated_ids,
)


@dataclass(frozen=True)
class ResultSnapshot:
    time_seconds: float | None
    arrays: dict[str, object]
    source: Path
    value_count: int
    cell_ids: object | None = None
    time_source: str = "unknown"


class VTKResultReader:
    def read(self, path):
        import numpy as np

        source = Path(path)
        from fracrtm.runtime.resource_limits import conversion_budget

        conversion_budget([source])
        lines = source.read_text(encoding="utf-8", errors="strict").splitlines()
        count = None
        arrays = {}
        index = 0
        while index < len(lines):
            fields = lines[index].split()
            if fields and fields[0].upper() == "CELL_DATA":
                count = int(fields[1])
                index += 1
                break
            index += 1
        if count is None:
            raise ValueError(
                msg("diagnostic.pflotran_results.name.does.not.contain.cell.data", name=source.name)
            )
        while index < len(lines):
            fields = lines[index].split()
            if not fields:
                index += 1
                continue
            keyword = fields[0].upper()
            if keyword == "SCALARS":
                name = fields[1]
                components = int(fields[3]) if len(fields) > 3 else 1
                index += 1
                if index < len(lines) and lines[index].strip().upper().startswith("LOOKUP_TABLE"):
                    index += 1
                values, index = _read_numeric_values(lines, index, count * components)
                array = np.asarray(values, dtype=float)
                arrays[name] = array if components == 1 else array.reshape(count, components)
                continue
            if keyword == "VECTORS":
                name = fields[1]
                index += 1
                values, index = _read_numeric_values(lines, index, count * 3)
                arrays[name] = np.asarray(values, dtype=float).reshape(count, 3)
                continue
            index += 1
        if not arrays:
            raise ValueError(
                msg(
                    "diagnostic.pflotran_results.name.contains.no.readable.result.arrays",
                    name=source.name,
                )
            )
        time = time_from_text(join_text("\n", lines[: min(40, len(lines))]))
        return ResultSnapshot(
            time,
            arrays,
            source,
            count,
            time_source="output_metadata" if time is not None else "unknown",
        )


class HDF5ResultReader:
    def read_all(self, path, expected_count):
        import h5py
        import numpy as np

        source = Path(path)
        snapshots = []
        with h5py.File(source, "r") as stream:
            groups = [
                (n, v)
                for n, v in stream.items()
                if isinstance(v, h5py.Group)
                and n.casefold() not in {"domain", "coordinates", "geometry", "materials"}
            ] or [("", stream)]
            for group_name, group in groups:
                arrays = {}

                def collect(name, item):
                    if isinstance(item, h5py.Dataset):
                        if is_cell_id_name(name.split("/")[-1]) or name.casefold().split("/")[
                            -1
                        ] in {
                            "cell ids",
                            "cell_ids",
                            "coordinates",
                            "vertices",
                            "cells",
                            "time",
                            "time units",
                        }:
                            return
                        value = np.asarray(item)
                        if value.ndim not in (1, 2) or not np.issubdtype(value.dtype, np.number):
                            return
                        if value.shape[0] != expected_count:
                            raise ValueError(
                                msg(
                                    "diagnostic.pflotran_results.source.group.name.name.result.uge.count.mismatch.count.expected.count",
                                    source=source,
                                    group_name=group_name,
                                    name=name,
                                    count=len(value),
                                    expected_count=expected_count,
                                )
                            )
                        if not np.isfinite(value).all():
                            raise ValueError(
                                msg(
                                    "diagnostic.pflotran_results.source.group.name.name.nonfinite.result.values",
                                    source=source,
                                    group_name=group_name,
                                    name=name,
                                )
                            )
                        arrays[name] = (
                            value[:, 0] if value.ndim == 2 and value.shape[1] == 1 else value
                        )

                group.visititems(collect)
                if arrays:
                    time = time_from_hdf5_group(group_name, group)
                    ids = hdf5_cell_ids(group, stream)
                    if ids is not None:
                        validated_ids(ids, join_text("", [source, ":", group_name]))
                    snapshots.append(
                        ResultSnapshot(
                            time,
                            arrays,
                            source,
                            expected_count,
                            ids,
                            "output_metadata" if time is not None else "unknown",
                        )
                    )
        if not snapshots:
            raise ValueError(
                msg("text.hdf5.result.contains.no.arrays.matching.the.octree.node.count")
            )
        return snapshots


@dataclass(frozen=True)
class PostProcessResult:
    collection: Path
    snapshots: tuple[Path, ...]
    result_count: int
    uge_count: int
    octree_node_count: int
    maximum_coordinate_error: float
    explicit_index_mapping: bool
    warnings: tuple[str, ...] = ()


def generate_run_visualization(project, run_id=None):
    """Build display files without changing the solver's completion assessment."""
    from fracrtm.project.runs import get_run, update_run
    from fracrtm.project.storage import ProjectLock

    if not run_id:
        raise ValueError(msg("text.no.run.records"))
    task = get_run(project, run_id)
    record = json.loads(task.record_path.read_text(encoding="utf-8"))
    if record.get("status") in {"prepared", "running", "synchronizing"}:
        raise ValueError(msg("text.task.in.progress"))
    with ProjectLock(task.directory, "visualization"):
        try:
            result = PFLOTRANResultPostProcessor(project, run_id=run_id).process()
        except Exception as exc:
            update_run(task, visualization_status="failed", visualization_error=str(exc))
            raise
        update_run(
            task,
            visualization_status="succeeded",
            visualization_collection=str(result.collection),
            visualization_error="",
        )
    if record.get("status") == "completed":
        from dataclasses import replace

        from fracrtm.project.compact_project import discard_task_work, publish_result

        if task.record_path.name != "result.json":
            published = publish_result(task)
            visual = published.directory / "visualization"
            result = replace(
                result,
                collection=visual / result.collection.name,
                snapshots=tuple(
                    visual / p.relative_to(result.collection.parent) for p in result.snapshots
                ),
            )
            try:
                discard_task_work(task)
            except OSError as exc:
                update_run(published, cleanup_error=str(exc))
    return result


class PFLOTRANResultPostProcessor:
    def __init__(self, project_directory, run_id=None):
        from fracrtm.project.runs import get_run, run_matches_current

        from .project_files import VisualizationArtifactResolver

        if not run_id:
            raise ValueError(msg("text.no.run.records"))
        self.run_id = run_id
        self.task = get_run(project_directory, run_id)
        if self.task.record_path.name == "result.json" and not run_matches_current(self.task):
            raise ValueError(msg("results.inputs_not_retained"))
        self.artifacts = VisualizationArtifactResolver().resolve(project_directory, run_id=run_id)

    def process(self):
        import numpy as np
        import pyvista as pv

        a = self.artifacts
        if a.uge_mesh is None or a.octree_mesh is None:
            raise ValueError(
                msg("text.pflotran.visualization.requires.full.mesh.uge.and.octree.dfn.inp")
            )
        raw_paths = sorted(
            (
                p
                for p in a.raw_result_directory.glob("model-*.vtk")
                if not p.name.startswith("model-vel-")
            )
        )
        from fracrtm.runtime.resource_limits import conversion_budget

        conversion_budget(
            (raw_paths or list(a.raw_result_directory.glob("model*.h5"))) + [a.uge_mesh],
            a.octree_mesh,
        )
        volumes = read_uge(a.uge_mesh)
        uge_points = volumes.points
        mesh = pv.read(a.octree_mesh)
        mapping_path = a.visualization_directory / "visualization_mapping.json"
        mapping, max_error, explicit = _coordinate_mapping(uge_points, np.asarray(mesh.points))
        snapshots = []
        if raw_paths:
            snapshots = [VTKResultReader().read(p) for p in raw_paths]
        else:
            hdf5_paths = sorted(a.raw_result_directory.glob("model*.h5"))
            for path in hdf5_paths:
                snapshots.extend(HDF5ResultReader().read_all(path, len(uge_points)))
        if not snapshots:
            raise ValueError(
                msg("text.no.readable.raw.pflotran.vtk.or.hdf5.state.results.were.found")
            )
        count = snapshots[0].value_count
        if count != len(uge_points) or count != mesh.n_points:
            raise ValueError(
                msg(
                    "diagnostic.pflotran_results.result.uge.octree.count.mismatch.count.count.2.n.points",
                    count=count,
                    count_2=len(uge_points),
                    n_points=mesh.n_points,
                )
            )
        if any((s.value_count != count for s in snapshots)):
            raise ValueError(
                msg("text.pflotran.snapshots.do.not.have.a.consistent.cell.data.count")
            )
        # Infer times only from verified completion and a matching output schedule.
        inferred = _snapshot_times(
            a.project_root, len(snapshots), sources=[s.source for s in snapshots], task=self.task
        )
        if any(
            (
                s.time_seconds is not None
                and inferred[i] is not None
                and (not np.isclose(s.time_seconds, inferred[i], rtol=1e-10, atol=1e-08))
                for i, s in enumerate(snapshots)
            )
        ):
            inferred = [None] * len(snapshots)
        snapshots = [
            ResultSnapshot(
                s.time_seconds if s.time_seconds is not None else inferred[i],
                s.arrays,
                s.source,
                s.value_count,
                s.cell_ids,
                s.time_source
                if s.time_seconds is not None
                else "frozen_run_schedule"
                if inferred[i] is not None
                else "unknown",
            )
            for i, s in enumerate(snapshots)
        ]
        snapshots = order_snapshots(snapshots)
        times = [s.time_seconds for s in snapshots]
        signatures = {
            source.relative_to(a.project_root).as_posix(): _file_signature(source)
            for source in dict.fromkeys(s.source for s in snapshots)
        }
        generation = hashlib.sha256(
            json.dumps(
                prepare_json(
                    {
                        "raw": signatures,
                        "uge": _file_signature(a.uge_mesh),
                        "mesh": _file_signature(a.octree_mesh),
                        "times": times,
                    }
                ),
                sort_keys=True,
            ).encode()
        ).hexdigest()[:20]
        destination = a.visualization_directory / join_text("", ["generation_", generation])
        destination.mkdir(parents=True, exist_ok=True)
        outputs = []
        for index, snapshot in enumerate(snapshots):
            output = mesh.copy(deep=True)
            for name, values in snapshot.arrays.items():
                array = np.asarray(values)
                if snapshot.cell_ids is not None:
                    array = reorder_by_ids(
                        array,
                        snapshot.cell_ids,
                        volumes.ids,
                        join_text("", [snapshot.source, ":", name]),
                    )
                reordered = np.empty_like(array)
                reordered[mapping] = array
                output.point_data[name] = reordered
            cv_ids = np.empty_like(volumes.ids)
            cv_ids[mapping] = volumes.ids
            output.point_data["ControlVolumeID"] = cv_ids
            target = destination / join_text("", ["model-", f"{index:03d}", ".vtu"])
            output.save(target, binary=True)
            outputs.append(target)
        _write_pvd(a.result_collection, outputs, times)
        source_identity = {
            "uge": _file_signature(a.uge_mesh),
            "octree": _file_signature(a.octree_mesh),
        }
        if (a.project_root / "pflotran/model_config.json").is_file():
            source_identity["config"] = _file_signature(
                a.project_root / "pflotran/model_config.json"
            )
        mapping_path.write_text(
            json.dumps(
                prepare_json(
                    {
                        "schema_version": "2.0",
                        "run_id": self.run_id
                        or (
                            a.project_root.parent.name
                            if a.project_root.name == "snapshot"
                            else None
                        ),
                        "result_count": count,
                        "uge_control_volumes": len(uge_points),
                        "octree_nodes": mesh.n_points,
                        "maximum_coordinate_error": max_error,
                        "explicit_index_mapping": explicit,
                        "bijection_verified": True,
                        "sources": source_identity,
                        "raw_sources": signatures,
                        "frames": [
                            {
                                "display": p.relative_to(a.visualization_directory).as_posix(),
                                "raw_source": s.source.relative_to(a.project_root).as_posix(),
                                "time_seconds": s.time_seconds,
                                "time_source": s.time_source,
                            }
                            for p, s in zip(outputs, snapshots)
                        ],
                        "association": "point",
                        "ordering_basis": msg(
                            "text.pflotran.explicit.grid.state.records.follow.uge.record.order.hdf5.cell.ids"
                        ),
                        "uge_cell_ids": volumes.ids.tolist(),
                        "uge_to_octree_point": mapping.tolist(),
                        "time_values": times,
                        "time_labels": time_labels(times),
                        "time_sources": [s.time_source for s in snapshots],
                    }
                ),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        warnings = [
            msg(
                "text.velocity.remains.raw.because.face.connection.semantics.require.separate.mapping"
            )
        ]
        if any((t is None for t in times)):
            warnings.append(
                msg(
                    "text.some.snapshots.have.unknown.physical.time.displayed.as.snapshot.indices.without.units"
                )
            )
        if len([t for t in times if t is not None]) != len(
            set((t for t in times if t is not None))
        ):
            warnings.append(
                msg("text.duplicate.physical.times.retained.as.separate.snapshots.in.stable.order")
            )
        if self.task and self.task.record_path.name == "result.json":
            from fracrtm.project.compact_project import _convert_mapping, manifest
            from fracrtm.project.runs import update_run

            _convert_mapping(self.task.directory, self.task.run_id)
            update_run(
                self.task,
                visualization_files=manifest(
                    self.task.directory, a.visualization_directory.rglob("*")
                ),
            )
        return PostProcessResult(
            a.result_collection,
            tuple(outputs),
            count,
            len(uge_points),
            mesh.n_points,
            max_error,
            explicit,
            tuple(warnings),
        )


def _read_numeric_values(lines, index, required):
    values = []
    while index < len(lines) and len(values) < required:
        fields = lines[index].split()
        if fields and fields[0].upper() in {
            "SCALARS",
            "VECTORS",
            "FIELD",
            "CELL_DATA",
            "POINT_DATA",
        }:
            break
        values.extend((float(v.replace("D", "E").replace("d", "e")) for v in fields))
        index += 1
    if len(values) != required:
        raise ValueError(msg("results.value_count", expected=required, actual=len(values)))
    if any((not __import__("math").isfinite(v) for v in values)):
        raise ValueError(msg("text.nonfinite.pflotran.result.values"))
    return (values, index)


def _read_uge_centers(path):
    volumes = read_uge(path)
    return (volumes.ids, volumes.points)


def _coordinate_mapping(uge_points, octree_points):
    return coordinate_mapping(uge_points, octree_points)


def _file_signature(path):
    return file_fingerprint(path)


def _load_cached_mapping(path, uge_path, octree_path, uge_count, octree_count):
    import numpy as np

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not data.get("bijection_verified") or data.get("sources") != {
            "uge": _file_signature(uge_path),
            "octree": _file_signature(octree_path),
        }:
            return None
        values = np.asarray(data["uge_to_octree_point"], dtype=int)
        if (
            len(values) != uge_count
            or uge_count != octree_count
            or set(values.tolist()) != set(range(octree_count))
        ):
            return None
        return (
            values,
            float(data["maximum_coordinate_error"]),
            bool(data["explicit_index_mapping"]),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _snapshot_times(project, count, sources=None, task=None):
    from fracrtm.config.pflotran import PFLOTRANConfig

    factors = TIME_FACTORS
    try:
        project = Path(project)
        path = task.record_path if task else project.parent / "run.json"
        if (not task and project.name != "snapshot") or not path.is_file():
            return [None] * count
        record = json.loads(path.read_text(encoding="utf-8"))
        if (
            record.get("returncode") != 0
            or not record.get("synchronized")
            or record.get("completion_status") != "verified"
        ):
            return [None] * count
        frozen_config = project / "pflotran/model_config.json"
        expected = next(
            (
                f.get("sha256")
                for f in record.get("input_files", [])
                if f.get("path") == "pflotran/model_config.json"
            ),
            None,
        )
        if not expected or _file_signature(frozen_config)["sha256"] != expected:
            return [None] * count
        if not sources or any((p.suffix.lower() != ".vtk" for p in sources)):
            return [None] * count
        numbers = [int(re.fullmatch("model-(\\d+)\\.vtk", p.name).group(1)) for p in sources]
        if numbers not in (list(range(count)), list(range(1, count + 1))):
            return [None] * count
        c = PFLOTRANConfig.load_json(project / "pflotran" / "model_config.json")
        from fracrtm.config.time_units import CALENDAR_UNITS

        if any(
            unit in CALENDAR_UNITS
            for unit in (
                c.time.final_unit,
                c.output.schedule.interval_unit,
                c.output.schedule.specified_times_unit,
            )
        ):
            return [None] * count
        final = c.time.final_time * factors[c.time.final_unit]
        s = c.output.schedule
        values = []
        if c.output.print_initial:
            values.append(0.0)
        if s.mode == "periodic_time":
            interval = s.interval_value * factors[s.interval_unit]
            step = interval
            while step < final - max(1e-09, final * 1e-12):
                values.append(step)
                step += interval
        elif s.mode == "specified_times":
            values.extend((v * factors[s.specified_times_unit] for v in s.specified_times))
        if c.output.print_final and (
            not values or abs(values[-1] - final) > max(1e-09, final * 1e-12)
        ):
            values.append(final)
        if len(values) == count and values == sorted(values):
            return values
    except Exception:
        pass
    return [None for i in range(count)]


def _write_pvd(path, snapshots, times):
    root = ET.Element("VTKFile", type="Collection", version="0.1", byte_order="LittleEndian")
    collection = ET.SubElement(root, "Collection")
    for index, (snapshot, time) in enumerate(zip(snapshots, times)):
        attributes = {
            "group": "",
            "part": "0",
            "file": snapshot.relative_to(Path(path).parent).as_posix(),
            "snapshot_index": str(index),
            "time_known": "true" if time is not None else "false",
        }
        if time is not None:
            attributes["timestep"] = f"{time:.16g}"
            attributes["time_unit"] = "s"
        ET.SubElement(collection, "DataSet", **attributes)
    ET.indent(root, space="  ")
    temporary = Path(path).with_suffix(".pvd.tmp")
    ET.ElementTree(root).write(temporary, encoding="utf-8", xml_declaration=True)
    temporary.replace(path)
