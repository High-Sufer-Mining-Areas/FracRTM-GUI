"""Map UGE control volumes to INP points by coordinate."""

from __future__ import annotations

import hashlib
import math
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

import numpy as np

from fracrtm.messages import join_text
from fracrtm.messages import message as msg


@dataclass(frozen=True)
class ControlVolumes:
    ids: np.ndarray
    points: np.ndarray
    volumes: np.ndarray
    connections: np.ndarray


@dataclass(frozen=True)
class RegionSelection:
    points: np.ndarray
    control_ids: np.ndarray
    description: str
    representation: str = "control_volume_representative_points"


def file_fingerprint(path):
    p = Path(path)
    digest = hashlib.sha256()
    with p.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(p.resolve()), "sha256": digest.hexdigest(), "size": p.stat().st_size}


def file_fingerprints(paths):
    """Hash independent result files with bounded I/O concurrency, preserving order."""
    paths = list(paths)
    if len(paths) < 4:
        return [file_fingerprint(path) for path in paths]
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="result-integrity") as pool:
        return list(pool.map(file_fingerprint, paths))


def safe_resource(project, relative):
    value = str(relative).replace("\\", "/")
    pure = PurePosixPath(value)
    if not value or pure.is_absolute() or PureWindowsPath(value).drive or (".." in pure.parts):
        raise ValueError(
            msg(
                "diagnostic.spatial_mapping.unsafe.project.relative.resource.path.relative",
                relative=f"{relative!r}",
            )
        )
    root = Path(project).resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise ValueError(
            msg(
                "diagnostic.spatial_mapping.resource.escapes.project.relative",
                relative=f"{relative!r}",
            )
        )
    from fracrtm.project.storage import resource_path

    return resource_path(root, value, path)


def validated_ids(values, label="Cell IDs", *, unique=True):
    raw = np.asarray(values)
    if raw.ndim != 1 or not len(raw):
        raise ValueError(
            msg(
                "diagnostic.spatial_mapping.label.expected.a.nonempty.one.dimensional.cell.id.array",
                label=label,
            )
        )
    if not np.issubdtype(raw.dtype, np.number) or not np.all(np.isfinite(raw)):
        raise ValueError(
            msg("diagnostic.spatial_mapping.label.cell.ids.must.be.finite.integers", label=label)
        )
    if np.any(raw <= 0) or np.any(raw > np.iinfo(np.int64).max) or np.any(raw != np.floor(raw)):
        raise ValueError(
            msg("diagnostic.spatial_mapping.label.cell.ids.must.be.positive.integers", label=label)
        )
    ids = raw.astype(np.int64)
    if unique and len(np.unique(ids)) != len(ids):
        raise ValueError(msg("diagnostic.spatial_mapping.label.duplicate.cell.ids", label=label))
    return ids


_UGE_CACHE = OrderedDict()
_UGE_CACHE_LOCK = threading.Lock()
_UGE_CACHE_BYTES = 64 * 1024 * 1024


def read_uge(path):
    """Reuse validated, immutable arrays only after a full content hash check."""
    from fracrtm.runtime.resource_limits import conversion_budget

    conversion_budget([path])
    identity = file_fingerprint(path)
    key = (identity["path"], identity["sha256"])
    with _UGE_CACHE_LOCK:
        if key in _UGE_CACHE:
            _UGE_CACHE.move_to_end(key)
            return _UGE_CACHE[key]
    volumes = _parse_uge(path)
    if file_fingerprint(path) != identity:
        raise OSError(msg("validation.uge_changed_during_read", path=path))
    arrays = (volumes.ids, volumes.points, volumes.volumes, volumes.connections)
    volumes = ControlVolumes(
        *(np.frombuffer(a.tobytes(), dtype=a.dtype).reshape(a.shape) for a in arrays)
    )
    size = sum(a.nbytes for a in arrays)
    if size <= _UGE_CACHE_BYTES:
        with _UGE_CACHE_LOCK:
            # Keep at most two meshes and 64 MiB of arrays, including historical runs.
            _UGE_CACHE[key] = volumes
            while (
                len(_UGE_CACHE) > 2
                or sum(
                    a.nbytes
                    for entry in _UGE_CACHE.values()
                    for a in (entry.ids, entry.points, entry.volumes, entry.connections)
                )
                > _UGE_CACHE_BYTES
            ):
                _UGE_CACHE.popitem(last=False)
    return volumes


def _parse_uge(path):
    p = Path(path)
    with p.open("r", encoding="utf-8") as stream:
        header = stream.readline().split()
        if len(header) != 2 or header[0].upper() != "CELLS":
            raise ValueError(msg("diagnostic.spatial_mapping.p.missing.cells.header", p=p))
        count = int(header[1])
        rows = []
        if count <= 0:
            raise ValueError(msg("diagnostic.spatial_mapping.p.cells.must.be.positive", p=p))
        for index in range(count):
            fields = stream.readline().split()
            if len(fields) != 5:
                raise ValueError(
                    msg(
                        "diagnostic.spatial_mapping.p.incomplete.uge.cell.record.value",
                        p=p,
                        value=index + 1,
                    )
                )
            rows.append(
                [
                    int(fields[0]),
                    *[float(x.replace("D", "E").replace("d", "e")) for x in fields[1:]],
                ]
            )
        data = np.asarray(rows)
        ids = validated_ids(data[:, 0], str(p))
        if not np.isfinite(data[:, 1:]).all() or np.any(data[:, 4] <= 0):
            raise ValueError(
                msg(
                    "diagnostic.spatial_mapping.p.coordinates.and.volumes.must.be.finite.volumes.must.be.positive",
                    p=p,
                )
            )
        header = stream.readline().split()
        while not header:
            line = stream.readline()
            if not line:
                raise ValueError(
                    msg("diagnostic.spatial_mapping.p.missing.connections.header", p=p)
                )
            header = line.split()
        if len(header) != 2 or header[0].upper() != "CONNECTIONS":
            raise ValueError(msg("diagnostic.spatial_mapping.p.missing.connections.header", p=p))
        connection_count = int(header[1])
        if connection_count < 0:
            raise ValueError(msg("diagnostic.spatial_mapping.p.negative.connections.count", p=p))
        connections = []
        valid = set(ids.tolist())
        seen = set()
        for index in range(connection_count):
            fields = stream.readline().split()
            if len(fields) != 6:
                raise ValueError(
                    msg(
                        "diagnostic.spatial_mapping.p.incomplete.connection.value",
                        p=p,
                        value=index + 1,
                    )
                )
            i, j = (int(fields[0]), int(fields[1]))
            geometry = [float(x) for x in fields[2:]]
            if i not in valid or j not in valid or i == j:
                raise ValueError(
                    msg(
                        "diagnostic.spatial_mapping.p.connection.value.has.invalid.cell.ids",
                        p=p,
                        value=index + 1,
                    )
                )
            pair = tuple(sorted((i, j)))
            if pair in seen:
                raise ValueError(
                    msg("diagnostic.spatial_mapping.p.duplicate.connection.pair", p=p, pair=pair)
                )
            seen.add(pair)
            if not all(math.isfinite(value) for value in geometry) or geometry[-1] <= 0:
                raise ValueError(
                    msg("diagnostic.spatial_mapping.p.invalid.connection.geometry.area", p=p)
                )
            connections.append([i, j, *geometry])
        if any((line.strip() for line in stream)):
            raise ValueError(
                msg("diagnostic.spatial_mapping.p.unexpected.data.after.connections", p=p)
            )
    return ControlVolumes(ids, data[:, 1:4], data[:, 4], np.asarray(connections).reshape(-1, 6))


def coordinate_mapping(source, target, tolerance=None):
    """Return a bijection; reject missing or ambiguous coordinate matches."""
    from scipy.spatial import cKDTree

    source = np.asarray(source)
    target = np.asarray(target)
    if (
        source.shape != target.shape
        or source.ndim != 2
        or source.shape[1] != 3
        or (not len(source))
    ):
        raise ValueError(
            msg(
                "diagnostic.spatial_mapping.uge.display.coordinate.shape.mismatch.shape.shape.2",
                shape=source.shape,
                shape_2=target.shape,
            )
        )
    if not np.isfinite(source).all() or not np.isfinite(target).all():
        raise ValueError(msg("text.nonfinite.uge.display.coordinates"))
    scale = max(float(np.ptp(target, axis=0).max()), 1.0)
    epsilon = np.finfo(target.dtype).eps if np.issubdtype(target.dtype, np.floating) else 0.0
    tolerance = float(
        tolerance if tolerance is not None else max(1e-10, scale * 1e-09, scale * epsilon * 4)
    )
    if not np.isfinite(tolerance) or tolerance <= 0:
        raise ValueError(msg("text.coordinate.tolerance.must.be.finite.and.positive"))
    for label, points in [("UGE", source), ("display", target)]:
        if len(np.unique(points, axis=0)) != len(points):
            raise ValueError(
                msg(
                    "diagnostic.spatial_mapping.label.duplicate.coordinates.are.ambiguous",
                    label=label,
                )
            )
    candidates = cKDTree(target).query_ball_point(source, tolerance)
    for i, matches in enumerate(candidates):
        if not matches:
            raise ValueError(
                msg(
                    "diagnostic.spatial_mapping.no.display.point.matches.uge.record.value.within.tolerance.m",
                    value=i + 1,
                    tolerance=f"{tolerance:g}",
                )
            )
        if len(matches) != 1:
            raise ValueError(
                msg(
                    "diagnostic.spatial_mapping.uge.record.value.ambiguous.coordinate.match.count.candidates.within.tolerance.m",
                    value=i + 1,
                    count=len(matches),
                    tolerance=f"{tolerance:g}",
                )
            )
    mapping = np.array([m[0] for m in candidates], dtype=int)
    if len(np.unique(mapping)) != len(mapping):
        raise ValueError(
            msg("text.coordinate.mapping.is.not.one.to.one.duplicate.ambiguous.targets")
        )
    maximum = float(np.linalg.norm(source - target[mapping], axis=1).max())
    return (mapping, maximum, not np.array_equal(mapping, np.arange(len(mapping))))


def reorder_by_ids(values, ids, target_ids, label="dataset"):
    ids = validated_ids(ids, label)
    target = validated_ids(target_ids, "UGE")
    values = np.asarray(values)
    if values.ndim not in (1, 2) or values.shape[0] != len(ids):
        raise ValueError(
            msg("diagnostic.spatial_mapping.label.values.cell.ids.dimension.mismatch", label=label)
        )
    unknown = sorted(set(ids) - set(target))
    missing = sorted(set(target) - set(ids))
    if unknown or missing:
        raise ValueError(
            msg(
                "diagnostic.spatial_mapping.label.unknown.cell.ids.value.missing.cell.ids.value.2",
                label=label,
                value=unknown[:8],
                value_2=missing[:8],
            )
        )
    lookup = {int(value): i for i, value in enumerate(ids)}
    return values[[lookup[int(value)] for value in target]]


CELL_ID_ALIASES = frozenset({"cellids", "cellid"})


def is_cell_id_name(name):
    return (
        str(name).casefold().replace(" ", "").replace("_", "").replace("-", "") in CELL_ID_ALIASES
    )


def hdf5_cell_ids(group, root=None):
    import h5py

    found = []
    for container in [group] + ([root] if root is not None and root.name != group.name else []):
        for name, value in container.items():
            if is_cell_id_name(name) and isinstance(value, h5py.Dataset):
                found.append(validated_ids(np.asarray(value), value.name))
    if not found:
        return None
    if any(not np.array_equal(found[0], item) for item in found[1:]):
        raise ValueError(msg("repair.hdf5_id_conflict"))
    return found[0]


def read_hdf5_values(path, dataset, id_dataset, target_ids, component=None, *, order_basis=None):
    import h5py

    from fracrtm.runtime.resource_limits import conversion_budget

    conversion_budget([path])
    with h5py.File(path, "r") as stream:
        key = str(dataset).lstrip("/")
        if key not in stream or not isinstance(stream[key], h5py.Dataset):
            raise ValueError(
                msg(
                    "diagnostic.spatial_mapping.path.missing.hdf5.dataset.key",
                    path=path,
                    key=f"{key!r}",
                )
            )
        values = np.asarray(stream[key])
        if (
            values.ndim not in (1, 2)
            or not np.issubdtype(values.dtype, np.number)
            or (not np.isfinite(values).all())
        ):
            raise ValueError(
                msg(
                    "diagnostic.spatial_mapping.path.key.expected.finite.numeric.rank.1.rank.2.array",
                    path=path,
                    key=key,
                )
            )
        if id_dataset and not is_cell_id_name(PurePosixPath(str(id_dataset)).name):
            if id_dataset not in stream:
                raise ValueError(msg("repair.hdf5_ids_missing"))
            ids = validated_ids(np.asarray(stream[id_dataset]), str(id_dataset))
        else:
            ids = hdf5_cell_ids(stream[key].parent, stream)
        if ids is None:
            if order_basis != "documented_uge_record_order":
                raise ValueError(msg("repair.hdf5_ids_missing"))
            if len(values) != len(target_ids):
                raise ValueError(msg("repair.hdf5_id_count"))
        else:
            values = reorder_by_ids(values, ids, target_ids, f"{path}:{key}")
    if component is not None:
        if values.ndim != 2 or not 0 <= int(component) < values.shape[1]:
            raise ValueError(
                msg(
                    "diagnostic.spatial_mapping.path.key.invalid.component.component",
                    path=path,
                    key=key,
                    component=component,
                )
            )
        values = values[:, int(component)]
    elif values.ndim == 2 and values.shape[1] == 1:
        values = values[:, 0]
    return values


def resolve_region_points(project, region, volumes):
    label = msg("region.named_label", name=region.name)
    ids = volumes.ids
    points = volumes.points
    lookup = {int(v): i for i, v in enumerate(ids)}

    def select(requested):
        chosen = validated_ids(requested, label)
        missing = [int(x) for x in chosen if int(x) not in lookup]
        if missing:
            raise ValueError(
                msg(
                    "diagnostic.spatial_mapping.label.unknown.cell.ids.value",
                    label=label,
                    value=missing[:8],
                )
            )
        return (points[[lookup[int(x)] for x in chosen]], chosen)

    kind = region.region_type
    unit = getattr(region, "coordinate_unit", "m")
    factors = {"m": 1.0, "cm": 0.01, "km": 1000.0}
    if kind in ("COORDINATE", "COORDINATES") and unit not in factors:
        raise ValueError(
            msg(
                "diagnostic.spatial_mapping.label.unsupported.coordinate.unit.unit",
                label=label,
                unit=f"{unit!r}",
            )
        )
    if kind == "LIST":
        selected, chosen = select(region.cell_ids)
    elif kind == "COORDINATES":
        bounds = np.asarray(region.bounds, dtype=float) * factors[unit]
        if bounds.shape != (6,) or not np.isfinite(bounds).all() or np.any(bounds[:3] > bounds[3:]):
            raise ValueError(
                msg("diagnostic.spatial_mapping.label.invalid.coordinate.bounds", label=label)
            )
        mask = np.all((points >= bounds[:3]) & (points <= bounds[3:]), axis=1)
        selected, chosen = (points[mask], ids[mask])
    elif kind == "COORDINATE":
        coordinate = np.asarray(region.coordinate, dtype=float) * factors[unit]
        if coordinate.shape != (3,) or not np.isfinite(coordinate).all():
            raise ValueError(
                msg("diagnostic.spatial_mapping.label.invalid.coordinate", label=label)
            )
        return RegionSelection(
            coordinate.reshape(1, 3),
            np.array([], dtype=int),
            msg("text.specified.coordinate.marker.not.a.reconstructed.control.volume.boundary"),
            "coordinate_marker",
        )
    elif kind == "FILE":
        path = safe_resource(project, region.file_path)
        lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and (not line.lstrip().startswith("#"))
        ]
        if not lines:
            raise ValueError(
                msg(
                    "diagnostic.spatial_mapping.label.empty.region.file.path",
                    label=label,
                    path=path,
                )
            )
        head = lines[0].split()
        if head[0].upper() == "CONNECTIONS":
            count = int(head[1])
            rows = [line.split() for line in lines[1:]]
            if count <= 0 or len(rows) != count or any((len(row) != 5 for row in rows)):
                raise ValueError(
                    msg(
                        "diagnostic.spatial_mapping.label.invalid.connections.records.in.path",
                        label=label,
                        path=path,
                    )
                )
            chosen = validated_ids(np.array([int(row[0]) for row in rows]), label, unique=False)
            missing = [int(x) for x in chosen if int(x) not in lookup]
            geometry = np.array([[float(x) for x in row[1:]] for row in rows])
            if missing or not np.isfinite(geometry).all() or np.any(geometry[:, 3] <= 0):
                raise ValueError(
                    msg(
                        "diagnostic.spatial_mapping.label.invalid.ids.coordinates.areas.in.path.unknown.ids.value",
                        label=label,
                        path=path,
                        value=missing[:8],
                    )
                )
            if len(set((tuple(row) for row in rows))) != count:
                raise ValueError(
                    msg(
                        "diagnostic.spatial_mapping.label.duplicate.boundary.connection.records.in.path",
                        label=label,
                        path=path,
                    )
                )
            return RegionSelection(
                geometry[:, :3],
                chosen,
                msg(
                    "text.boundary.connection.positions.read.from.ex.records.not.reconstructed.boundary.faces"
                ),
                "boundary_connection_positions",
            )
        if head[0].lower() == "zone":
            markers = [i for i, line in enumerate(lines) if line.lower() == "nnum"]
            if len(markers) != 1:
                raise ValueError(
                    msg(
                        "diagnostic.spatial_mapping.label.expected.one.nnum.zone.in.path",
                        label=label,
                        path=path,
                    )
                )
            i = markers[0]
            count = int(lines[i + 1])
            tokens = join_text(" ", lines[i + 2 :]).split()
            clean = []
            for token in tokens:
                if token.lower() in ("stop", "end"):
                    break
                clean.append(int(token))
            if len(clean) != count:
                raise ValueError(
                    msg(
                        "diagnostic.spatial_mapping.label.zone.count.mismatch.in.path",
                        label=label,
                        path=path,
                    )
                )
            selected, chosen = select(clean)
        else:
            # Plain region lists have no count prefix.
            tokens = join_text(" ", lines).split()
            declared = None
            if head[0].upper() in ("LIST", "CELL_IDS"):
                declared = int(head[1]) if len(head) > 1 else None
                tokens = join_text(" ", lines[1:]).split()
            try:
                requested = [int(token) for token in tokens]
            except ValueError as exc:
                raise ValueError(
                    msg(
                        "diagnostic.spatial_mapping.label.unsupported.cell.region.file.format.path",
                        label=label,
                        path=path,
                    )
                ) from exc
            if declared is not None and declared != len(requested):
                raise ValueError(
                    msg(
                        "diagnostic.spatial_mapping.label.declared.id.count.mismatch.in.path",
                        label=label,
                        path=path,
                    )
                )
            selected, chosen = select(requested)
    else:
        raise ValueError(
            msg(
                "diagnostic.spatial_mapping.label.unsupported.region.type.kind",
                label=label,
                kind=f"{kind!r}",
            )
        )
    if not len(chosen):
        raise ValueError(
            msg(
                "diagnostic.spatial_mapping.label.selection.contains.no.control.volumes",
                label=label,
            )
        )
    return RegionSelection(
        selected,
        chosen,
        msg(
            "text.control.volume.representative.positions.uge.not.inp.cells.or.reconstructed.volume.boundaries"
        ),
    )
