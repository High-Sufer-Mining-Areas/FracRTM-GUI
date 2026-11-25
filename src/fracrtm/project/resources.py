from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean

from fracrtm.messages import join_text
from fracrtm.messages import message as msg

SEMANTIC_BOUNDARIES = ("top", "bottom", "west", "east", "north", "south")
DFN_POLYGONS_PATH = Path("dfn/dfnGen_output/polygons.dat")
UDFM_INPUT_FILENAMES = (
    "reduced_mesh.inp",
    "normal_vectors.dat",
    "translations.dat",
    "aperture.dat",
    "params.txt",
    "poly_info.dat",
)


@dataclass(frozen=True)
class BoundaryArtifact:
    semantic: str
    relative_path: str
    face_count: int
    coordinate_axis: str
    coordinate_side: str
    coordinate_value: float


@dataclass(frozen=True)
class UdfmInputArtifacts:
    reduced_mesh: str = ""
    normal_vectors: str = ""
    translations: str = ""
    aperture: str = ""
    params: str = ""
    poly_info: str = ""

    @property
    def complete(self) -> bool:
        return all(
            (
                self.reduced_mesh,
                self.normal_vectors,
                self.translations,
                self.aperture,
                self.params,
                self.poly_info,
            )
        )

    @property
    def missing(self) -> tuple[str, ...]:
        values = (
            self.reduced_mesh,
            self.normal_vectors,
            self.translations,
            self.aperture,
            self.params,
            self.poly_info,
        )
        return tuple((name for name, value in zip(UDFM_INPUT_FILENAMES, values) if not value))


@dataclass(frozen=True)
class DfnArtifacts:
    reduced_mesh: str = ""
    normal_vectors: str = ""
    translations: str = ""
    aperture: str = ""
    params: str = ""
    poly_info: str = ""
    polygons: str = ""
    full_mesh: str = ""

    @property
    def udfm_inputs(self) -> UdfmInputArtifacts:
        return UdfmInputArtifacts(
            self.reduced_mesh,
            self.normal_vectors,
            self.translations,
            self.aperture,
            self.params,
            self.poly_info,
        )

    @property
    def udfm_ready(self) -> bool:
        return self.udfm_inputs.complete

    @property
    def geometry_ready(self) -> bool:
        return bool(self.polygons and self.normal_vectors and self.translations)


@dataclass(frozen=True)
class OctreeArtifacts:
    full_mesh: str = ""
    materials: str = ""
    permeability: str = ""
    porosity: str = ""
    boundaries: dict[str, BoundaryArtifact] = field(default_factory=dict)
    boundary_errors: tuple[str, ...] = ()
    visualization_mesh: str = ""

    @property
    def complete(self) -> bool:
        return bool(
            self.full_mesh
            and self.materials
            and self.permeability
            and self.porosity
            and set(SEMANTIC_BOUNDARIES).issubset(self.boundaries)
            and (not self.boundary_errors)
        )


@dataclass(frozen=True)
class DatabaseArtifacts:
    chemistry: tuple[str, ...] = ()


@dataclass(frozen=True)
class PflotranArtifacts:
    config: str = ""
    input: str = ""


@dataclass(frozen=True)
class ProjectArtifacts:
    project_root: Path
    dfn: DfnArtifacts
    octree: OctreeArtifacts
    database: DatabaseArtifacts
    pflotran: PflotranArtifacts

    @classmethod
    def discover(cls, project_directory: str | Path) -> "ProjectArtifacts":
        root = Path(project_directory).expanduser().resolve()

        def relative_if_file(path: Path) -> str:
            try:
                return (
                    path.relative_to(root).as_posix()
                    if path.is_file() and path.stat().st_size > 0
                    else ""
                )
            except OSError:
                return ""

        dfn_dir = root / "dfn"
        octree_dir = root / "octree"
        pflotran_dir = root / "pflotran"
        mesh = relative_if_file(octree_dir / "full_mesh.uge")
        boundaries, boundary_errors = BoundaryResolver().resolve(
            root, octree_dir, root / mesh if mesh else None
        )
        try:
            chemistry = tuple(
                (
                    item.relative_to(root).as_posix()
                    for item in sorted((root / "database").glob("*.dat"))
                    if item.is_file() and item.stat().st_size > 0
                )
            )
        except OSError:
            chemistry = ()
        return cls(
            root,
            DfnArtifacts(
                reduced_mesh=relative_if_file(dfn_dir / "reduced_mesh.inp"),
                normal_vectors=relative_if_file(dfn_dir / "normal_vectors.dat"),
                translations=relative_if_file(dfn_dir / "translations.dat"),
                aperture=relative_if_file(dfn_dir / "aperture.dat"),
                params=relative_if_file(dfn_dir / "params.txt"),
                poly_info=relative_if_file(dfn_dir / "poly_info.dat"),
                polygons=relative_if_file(root / DFN_POLYGONS_PATH),
                full_mesh=relative_if_file(dfn_dir / "full_mesh.inp"),
            ),
            OctreeArtifacts(
                mesh,
                relative_if_file(octree_dir / "materials.h5"),
                relative_if_file(octree_dir / "mesh_permeability.h5"),
                relative_if_file(octree_dir / "mesh_porosity.h5"),
                boundaries,
                tuple(boundary_errors),
                relative_if_file(octree_dir / "octree_dfn.inp"),
            ),
            DatabaseArtifacts(chemistry),
            PflotranArtifacts(
                relative_if_file(pflotran_dir / "model_config.json"),
                relative_if_file(pflotran_dir / "model.in"),
            ),
        )


class BoundaryResolver:
    def resolve(
        self, project_root: Path, octree_directory: Path, mesh_path: Path | None
    ) -> tuple[dict[str, BoundaryArtifact], list[str]]:
        errors: list[str] = []
        if mesh_path is None:
            return (
                {},
                [
                    msg(
                        "text.full.mesh.uge.is.missing.external.boundaries.cannot.be.identified.from.coordinates"
                    )
                ],
            )
        try:
            from fracrtm.visualization.spatial_mapping import read_uge

            volumes = read_uge(mesh_path)
            bounds = tuple(
                (float(volumes.points[:, axis].min()), float(volumes.points[:, axis].max()))
                for axis in range(3)
            )
            owners = dict(zip(volumes.ids.tolist(), volumes.points.tolist()))
            offset = _zone2ex_offset(project_root)
            paths = sorted(octree_directory.glob("*.ex"))
        except (OSError, ValueError, TypeError) as exc:
            return ({}, [msg("resources.boundary_failed", reason=exc)])
        classified: dict[str, list[BoundaryArtifact]] = {}
        custom: dict[str, BoundaryArtifact] = {}
        for path in paths:
            try:
                face_count, coordinates = _read_ex_coordinates(path)
                relative = path.relative_to(project_root).as_posix()
                try:
                    semantic, axis, side, coordinate = _classify_ex_boundary(
                        path, coordinates, bounds, owners, offset
                    )
                    classified.setdefault(semantic, []).append(
                        BoundaryArtifact(semantic, relative, face_count, axis, side, coordinate)
                    )
                except ValueError:
                    name = _custom_boundary_name(path, set(custom))
                    custom[name] = BoundaryArtifact(
                        name, relative, face_count, "", "", float("nan")
                    )
            except (OSError, ValueError) as exc:
                errors.append(msg("resources.parse_failed", path=path.name, reason=exc))
        result: dict[str, BoundaryArtifact] = {}
        for semantic, candidates in classified.items():
            candidates.sort(key=lambda item: (-item.face_count, item.relative_path))
            if len(candidates) == 1:
                result[semantic] = candidates[0]
                continue
            errors.append(msg("repair.boundary_ambiguous", face=semantic))
            for extra in candidates:
                path = Path(extra.relative_path)
                name = _custom_boundary_name(path, set(result) | set(custom))
                custom[name] = BoundaryArtifact(
                    name,
                    extra.relative_path,
                    extra.face_count,
                    extra.coordinate_axis,
                    extra.coordinate_side,
                    extra.coordinate_value,
                )
        result.update(custom)
        missing = [name for name in SEMANTIC_BOUNDARIES if name not in result]
        if missing:
            errors.append(
                msg("text.readable.external.boundaries.are.missing") + join_text(", ", missing)
            )
        return (result, errors)


def _custom_boundary_name(path: Path, existing: set[str]) -> str:
    name = path.stem
    candidate = name
    index = 2
    while candidate in existing:
        candidate = join_text("", [name, "_", index])
        index += 1
    return candidate


def _zone2ex_offset(project):
    import json
    import math

    # zone2ex offsets boundary reference points by h * 1e-3.
    path = Path(project) / "dfn/dfn_config.json"
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8")).get("h")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        return None
    return value * 1e-3


def _classify_ex_boundary(path, coordinates, bounds, owners, offset):
    try:
        return _classify_boundary(coordinates, bounds)
    except ValueError:
        if offset is None:
            raise
    identities = []
    with Path(path).open("r", encoding="utf-8") as stream:
        count = int(stream.readline().split()[1])
        for _ in range(count):
            identities.append(int(stream.readline().split()[0]))
    if len(set(identities)) != len(identities) or any(i not in owners for i in identities):
        raise ValueError(msg("repair.boundary_interior"))
    points = [owners[i] for i in identities]
    semantic, axis, side, coordinate = _classify_boundary(points, bounds)
    axis_index = "xyz".index(axis)
    direction = -1 if side == "min" else 1
    for owner, reference in zip(points, coordinates):
        for i in range(3):
            expected = owner[i] + (direction * offset if i == axis_index else 0)
            # Boundary coordinates use six decimal places in scientific notation.
            tolerance = max(abs(expected) * 6e-7, offset * 1e-6, 1e-12)
            if abs(reference[i] - expected) > tolerance:
                raise ValueError(msg("repair.boundary_interior"))
    return semantic, axis, side, coordinate + direction * offset


def _read_uge_bounds(path: Path) -> tuple[tuple[float, float], ...]:
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        header = stream.readline().split()
        if len(header) != 2 or header[0].upper() != "CELLS":
            raise ValueError(msg("text.uge.is.missing.its.cells.header"))
        count = int(header[1])
        coordinates = []
        for _ in range(count):
            fields = stream.readline().split()
            if len(fields) < 4:
                raise ValueError(msg("text.uge.cell.records.are.incomplete"))
            coordinates.append(tuple((float(value) for value in fields[1:4])))
    if not coordinates:
        raise ValueError(msg("text.uge.contains.no.cells"))
    return tuple(
        (
            (min((row[index] for row in coordinates)), max((row[index] for row in coordinates)))
            for index in range(3)
        )
    )


def _read_ex_coordinates(path: Path) -> tuple[int, list[tuple[float, float, float]]]:
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        header = stream.readline().split()
        if len(header) != 2 or header[0].upper() != "CONNECTIONS":
            raise ValueError(msg("text.the.ex.file.is.missing.its.connections.header"))
        count = int(header[1])
        coordinates = []
        for _ in range(count):
            fields = stream.readline().split()
            if len(fields) < 5:
                raise ValueError(msg("text.the.ex.face.records.are.incomplete"))
            coordinates.append(tuple((float(value) for value in fields[1:4])))
    if count < 1 or len(coordinates) != count:
        raise ValueError(msg("text.the.ex.boundary.is.empty.or.its.count.is.inconsistent"))
    return (count, coordinates)


def _classify_boundary(coordinates, bounds):
    spans = [max(high - low, 1e-30) for low, high in bounds]
    spreads = [
        (max((row[index] for row in coordinates)) - min((row[index] for row in coordinates)))
        / spans[index]
        for index in range(3)
    ]
    axis_index = min(range(3), key=spreads.__getitem__)
    if spreads[axis_index] > 0.05:
        raise ValueError(msg("text.coordinates.do.not.form.a.single.external.boundary.plane"))
    coordinate = fmean((row[axis_index] for row in coordinates))
    low, high = bounds[axis_index]
    tolerance = max(1e-10 * spans[axis_index], 1e-12)
    distance = min(abs(coordinate - low), abs(coordinate - high))
    if (
        distance > tolerance
        or max(abs(row[axis_index] - coordinate) for row in coordinates) > tolerance
    ):
        raise ValueError(msg("repair.boundary_interior"))
    side = "min" if abs(coordinate - low) <= abs(coordinate - high) else "max"
    semantic = {
        (0, "min"): "west",
        (0, "max"): "east",
        (1, "min"): "south",
        (1, "max"): "north",
        (2, "min"): "bottom",
        (2, "max"): "top",
    }[axis_index, side]
    return (semantic, "xyz"[axis_index], side, coordinate)
