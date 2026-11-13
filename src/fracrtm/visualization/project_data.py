from __future__ import annotations

import json
import math
import re
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

from fracrtm.messages import message as msg
from fracrtm.messages import prepare_json
from fracrtm.project.simulation_mesh import DEFAULT_MODEL_SOURCE, ModelSourceProvider

from .dataset import CATEGORICAL, CONTINUOUS, VisualizationArray, VisualizationDataset
from .display_rules import extract_region_geometry, field_label, material_categories
from .result_times import time_labels
from .spatial_mapping import (
    coordinate_mapping,
    file_fingerprint,
    read_hdf5_values,
    read_uge,
    safe_resource,
)


class VisualizationMeshLoader:
    def __init__(self, pyvista_module=None):
        if pyvista_module is None:
            import pyvista as pyvista_module
        self.pv = pyvista_module

    def load_octree(self, path: str | Path):
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(source)
        return self.pv.read(source)

    def load_dfn_polygons(self, path: str | Path):
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(source)
        points = []
        faces = []
        for line in source.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
            if not line.strip():
                continue
            count = int(line.split(None, 1)[0])
            coordinates = re.findall("\\{\\s*([^}]+)\\}", line)
            if count < 3:
                raise ValueError(msg("text.dfn.polygon.must.contain.at.least.three.vertices"))
            if len(coordinates) != count:
                raise ValueError(
                    msg(
                        "diagnostic.project_data.dfn.polygon.vertex.count.mismatch.name",
                        name=source.name,
                    )
                )
            start = len(points)
            for value in coordinates:
                xyz = tuple((float(item.strip()) for item in value.split(",")))
                if len(xyz) != 3:
                    raise ValueError(msg("text.dfn.polygon.vertex.must.contain.x.y.and.z"))
                if not all((math.isfinite(item) for item in xyz)):
                    raise ValueError(msg("text.dfn.polygon.contains.nan.or.infinite.coordinates"))
                points.append(xyz)
            faces.extend([count, *range(start, start + count)])
        if not points:
            raise ValueError(msg("text.dfn.polygons.dat.contains.no.polygons"))
        try:
            mesh = self.pv.PolyData(points, faces)
        except Exception as exc:
            raise ValueError(
                msg("diagnostic.project_data.cannot.create.dfn.polygon.geometry.exc", exc=exc)
            ) from exc
        if getattr(mesh, "n_points", len(points)) <= 0:
            raise ValueError(msg("text.dfn.polygon.geometry.is.empty"))
        return mesh


class LazyPyVistaSnapshots:
    def __init__(self, paths, reader, first):
        self.paths = tuple((Path(p) for p in paths))
        self.reader = reader
        self.first = first
        self._cached_index = 0
        self._cached_snapshot = first

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple((self[i] for i in range(*index.indices(len(self)))))
        index = int(index)
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        if index == 0:
            return self.first
        if index != self._cached_index:
            try:
                snapshot = self.reader(self.paths[index])
            except Exception as exc:
                raise ValueError(
                    msg(
                        "diagnostic.project_data.unable.to.read.pflotran.snapshot.name.exc",
                        name=self.paths[index].name,
                        exc=exc,
                    )
                ) from exc
            if snapshot.n_points != self.first.n_points or not np.array_equal(
                snapshot.points, self.first.points
            ):
                raise ValueError(
                    msg(
                        "diagnostic.project_data.value.snapshot.geometry.differs.from.the.associated.run.geometry",
                        value=self.paths[index],
                    )
                )
            self._cached_index = index
            self._cached_snapshot = snapshot
        return self._cached_snapshot


class ProjectVisualizationRepository:
    def __init__(self, project_directory, provider: ModelSourceProvider = DEFAULT_MODEL_SOURCE):
        import pyvista as pv

        self.pv = pv
        self.root = Path(project_directory).resolve()
        self.provider = provider
        self.mesh_artifacts = provider.simulation_mesh(self.root)
        self.loader = VisualizationMeshLoader(pv)
        self._cache = {}
        self._identity = None

    def _config(self, config=None):
        if config is not None:
            return config
        path = self.root / "pflotran/model_config.json"
        if path.is_file():
            from fracrtm.config.pflotran import PFLOTRANConfig

            return PFLOTRANConfig.load_json(path)
        return None

    def _refresh_identity(self, config=None):
        config = self._config(config)
        self.mesh_artifacts = self.provider.simulation_mesh(self.root)
        files = [
            self.mesh_artifacts.visualization_geometry,
            self.mesh_artifacts.grid,
            self.mesh_artifacts.materials,
            self.mesh_artifacts.permeability,
            self.mesh_artifacts.porosity,
        ]
        if config:
            files += [safe_resource(self.root, config.grid.file)] if config.grid.file else []
            files += [safe_resource(self.root, d.file) for d in config.datasets if d.file]
            mapping = config.spatial_assignment.material_mapping
            if mapping and mapping.file_path:
                files.append(safe_resource(self.root, mapping.file_path))
        identity = json.dumps(
            prepare_json(
                {
                    "files": [
                        file_fingerprint(p)
                        for p in dict.fromkeys((p for p in files if p is not None))
                        if p.is_file()
                    ],
                    "config": config.to_dict() if config else None,
                }
            ),
            sort_keys=True,
        )
        if identity != self._identity:
            self._cache.clear()
            self._identity = identity
        return config

    def _volumes(self, config=None):
        config = self._refresh_identity(config)
        if "volumes" not in self._cache:
            path = (
                safe_resource(self.root, config.grid.file)
                if config and config.grid.file
                else self.mesh_artifacts.grid
            )
            if path is None:
                raise FileNotFoundError(msg("text.computational.uge.mesh.is.unavailable"))
            self._cache["volumes"] = read_uge(path)
        return self._cache["volumes"]

    def _mesh(self, config=None):
        volumes = self._volumes(config)
        if "mesh" not in self._cache:
            path = self.mesh_artifacts.visualization_geometry
            if path is None:
                raise FileNotFoundError(msg("text.display.geometry.is.unavailable"))
            mesh = self.loader.load_octree(path)
            mapping, error, explicit = coordinate_mapping(volumes.points, np.asarray(mesh.points))
            ids = np.empty_like(volumes.ids)
            ids[mapping] = volumes.ids
            mesh.point_data["ControlVolumeID"] = ids
            self._cache.update(mesh=mesh, mapping=mapping, mapping_error=error)
        return self._cache["mesh"]

    def _mapped_mesh(self, values, name, config=None):
        mesh = self._mesh(config).copy(deep=True)
        mapping = self._cache["mapping"]
        values = np.asarray(values)
        if len(values) != len(mapping):
            raise ValueError(
                msg("diagnostic.project_data.name.values.uge.identity.count.mismatch", name=name)
            )
        reordered = np.empty_like(values)
        reordered[mapping] = values
        mesh.point_data[name] = reordered
        return mesh

    def spatial_structure(self):
        path = self.provider.spatial_structure(self.root)
        if path is None:
            raise FileNotFoundError(msg("text.the.current.model.has.no.fracture.geometry"))
        key = ("structure", file_fingerprint(path)["sha256"])
        if key not in self._cache:
            self._cache[key] = self.loader.load_dfn_polygons(path)
        return VisualizationDataset(
            msg("spatial.fractures"),
            self._cache[key],
            metadata={"default_representation": "surface_edges"},
        )

    def mesh(self):
        return VisualizationDataset(
            msg("text.display.mesh"),
            self._mesh(),
            metadata={
                "default_representation": "surface_edges",
                "geometry_semantics": msg(
                    "text.inp.primal.display.topology.uge.control.volume.sites.correspond.to.points.not"
                ),
            },
        )

    def _material_values(self, config=None):
        config = self._config(config)
        volumes = self._volumes(config)
        mapping = config.spatial_assignment.material_mapping if config else None
        path = (
            safe_resource(self.root, mapping.file_path)
            if mapping and mapping.file_path
            else self.mesh_artifacts.materials
        )
        if path is None:
            raise FileNotFoundError(msg("text.material.mapping.file.is.unavailable"))
        values = read_hdf5_values(path, "Materials/Material Ids", "Materials/Cell Ids", volumes.ids)
        if values.ndim != 1 or np.any(values < 0) or np.any(values != np.floor(values)):
            raise ValueError(
                msg(
                    "diagnostic.project_data.path.material.ids.must.be.nonnegative.scalar.integers",
                    path=path,
                )
            )
        return values

    def material(self, config=None):
        config = self._refresh_identity(config)
        values = self._material_values(config)
        mesh = self._mapped_mesh(values, "Material", config)
        labels = {
            item.key: item.label
            for item in material_categories(values, config.materials if config else ())
        }
        return VisualizationDataset(
            msg("viewer.material_regions"),
            mesh,
            {
                "Material": VisualizationArray(
                    "Material",
                    "point",
                    CATEGORICAL,
                    labels=labels,
                    display_name=msg("viewer.material"),
                )
            },
            metadata={
                "default_array": "Material",
                "default_representation": "surface_edges",
                "association_basis": msg("text.material.cell.ids.uge.ids.validated.display.points"),
            },
        )

    def property(self, name, config=None):
        config = self._refresh_identity(config)
        volumes = self._volumes(config)
        is_perm = name.casefold().startswith("perm")
        axis = {"x": 0, "y": 1, "z": 2}.get(name[-1:].lower()) if is_perm and "_" in name else None
        array_name = (
            "Permeability" + ("_" + "XYZ"[axis] if axis is not None else "")
            if is_perm
            else "Porosity"
        )
        title = (
            msg("viewer.permeability") + ("_" + "XYZ"[axis] if axis is not None else "")
            if is_perm
            else msg("viewer.porosity")
        )

        def dataset_values(identifier):
            item = next((d for d in config.datasets if d.id == identifier), None)
            if item is None:
                raise ValueError(
                    msg(
                        "diagnostic.project_data.title.unknown.dataset.reference.identifier",
                        title=title,
                        identifier=f"{identifier!r}",
                    )
                )
            values = read_hdf5_values(
                safe_resource(self.root, item.file),
                item.hdf5_dataset_name or item.name,
                None,
                volumes.ids,
            )
            if values.ndim != 1:
                raise ValueError(
                    msg(
                        "diagnostic.project_data.title.dataset.name.is.not.scalar.use.explicit.component.datasets",
                        title=title,
                        name=item.name,
                    )
                )
            return values

        if config and config.materials:
            material_ids = self._material_values(config)
            values = np.empty(len(volumes.ids))
            defined = set()
            for material in config.materials:
                mask = material_ids == material.material_id
                defined.add(material.material_id)
                if not mask.any():
                    continue
                if not is_perm:
                    source = material.porosity
                    data = (
                        dataset_values(source.dataset_id)
                        if source.mode == "dataset"
                        else source.value
                    )
                else:
                    source = material.permeability
                    if source.mode == "dataset":
                        if source.anisotropic_dataset:
                            if axis is None:
                                raise ValueError(
                                    msg(
                                        "text.anisotropic.permeability.select.permeability.x.permeability.y.or.permeability.z"
                                    )
                                )
                            data = dataset_values(
                                [source.dataset_x, source.dataset_y, source.dataset_z][axis]
                            )
                        else:
                            data = dataset_values(source.dataset_id)
                    elif source.mode == "diagonal":
                        if axis is None and len(set(source.diagonal or [])) != 1:
                            raise ValueError(
                                msg("text.diagonal.permeability.select.an.explicit.x.y.z.component")
                            )
                        data = source.diagonal[axis if axis is not None else 0]
                    else:
                        data = source.isotropic
                if data is None:
                    raise ValueError(
                        msg(
                            "diagnostic.project_data.material.name.title.is.unset",
                            name=material.name,
                            title=title,
                        )
                    )
                values[mask] = np.asarray(data)[mask] if np.ndim(data) > 0 else data
            unknown = set(material_ids) - defined
            if unknown:
                raise ValueError(
                    msg(
                        "diagnostic.project_data.title.material.ids.lack.definitions.value",
                        title=title,
                        value=sorted(unknown),
                    )
                )
        else:
            path = self.mesh_artifacts.permeability if is_perm else self.mesh_artifacts.porosity
            if path is None:
                raise FileNotFoundError(
                    msg("diagnostic.project_data.title.resource.is.unavailable", title=title)
                )
            values = read_hdf5_values(
                path,
                "Permeability" if is_perm else "Porosity",
                "Cell Ids",
                volumes.ids,
            )
            if values.ndim == 2 and axis is not None:
                if axis >= values.shape[1]:
                    raise ValueError(
                        msg("diagnostic.project_data.title.missing.property.component", title=title)
                    )
                values = values[:, axis]
            if values.ndim != 1:
                raise ValueError(
                    msg(
                        "diagnostic.project_data.title.explicit.scalar.component.selection.is.required",
                        title=title,
                    )
                )
        if not np.isfinite(values).all():
            raise ValueError(msg("diagnostic.project_data.title.nonfinite.values", title=title))
        mesh = self._mapped_mesh(values, array_name, config)
        array = VisualizationArray(
            array_name,
            "point",
            CONTINUOUS,
            "m²" if is_perm else "",
            "log" if is_perm else "linear",
            display_name=title,
        )
        return VisualizationDataset(
            msg("viewer.permeability") if is_perm else msg("viewer.porosity"),
            mesh,
            {array_name: array},
            metadata={
                "default_array": array_name,
                "default_representation": "surface_edges",
                "association_basis": msg(
                    "text.configured.property.dataset.cell.ids.uge.ids.display.points"
                ),
            },
        )

    def simulation_results(self, run_id=None):
        from .project_files import VisualizationArtifactResolver

        artifacts = VisualizationArtifactResolver().resolve(self.root, run_id=run_id)
        collection = artifacts.result_collection
        if not collection.is_file():
            raise FileNotFoundError(
                msg("text.pflotran.visualization.time.series.has.not.been.generated")
            )
        mapping_path = collection.parent / "visualization_mapping.json"
        verified = False
        if mapping_path.is_file():
            record = json.loads(mapping_path.read_text(encoding="utf-8"))
            if record.get("schema_version") == "2.0":
                sources = record.get("sources", {})
                checks = [
                    (artifacts.uge_mesh, sources.get("uge", {})),
                    (artifacts.octree_mesh, sources.get("octree", {})),
                ]
                if "config" in sources:
                    checks.append(
                        (artifacts.project_root / "pflotran/model_config.json", sources["config"])
                    )
                checks += [
                    (safe_resource(artifacts.project_root, rel), fingerprint)
                    for rel, fingerprint in record.get("raw_sources", {}).items()
                ]
                for path, expected in checks:
                    if (
                        path is None
                        or not path.is_file()
                        or file_fingerprint(path)["sha256"] != expected.get("sha256")
                    ):
                        raise ValueError(
                            msg(
                                "diagnostic.project_data.visualization.source.changed.or.is.missing.path.regenerate.the.display.mapping.from",
                                path=path,
                            )
                        )
                verified = True
        root = ET.parse(collection).getroot()
        nodes = root.findall(".//DataSet")
        if not nodes:
            raise ValueError(msg("text.model.pvd.contains.no.snapshots"))
        records = []
        visual_root = collection.parent.resolve()
        for index, node in enumerate(nodes):
            relative = node.get("file", "")
            path = (visual_root / relative).resolve()
            if not relative or not path.is_relative_to(visual_root):
                raise ValueError(
                    msg(
                        "diagnostic.project_data.unsafe.snapshot.path.relative",
                        relative=f"{relative!r}",
                    )
                )
            if not path.is_file():
                raise FileNotFoundError(path)
            known = node.get("time_known") == "true" and node.get("time_unit") == "s"
            time = float(node.get("timestep")) if known else None
            if time is not None and (not np.isfinite(time) or time < 0):
                raise ValueError(
                    msg("diagnostic.project_data.path.invalid.physical.time", path=path)
                )
            records.append((time, path, index))
        records.sort(key=lambda r: (r[0] is None, r[0] if r[0] is not None else 0, r[2]))
        times = tuple((r[0] for r in records))
        paths = [r[1] for r in records]
        signature = json.dumps(
            prepare_json([file_fingerprint(collection), *[file_fingerprint(p) for p in paths]]),
            sort_keys=True,
        )
        key = ("results", run_id, signature)
        if key in self._cache:
            return self._cache[key]
        first = self.pv.read(paths[0])
        snapshots = LazyPyVistaSnapshots(paths, self.pv.read, first)
        arrays = {}
        for association, values in [("point", first.point_data), ("cell", first.cell_data)]:
            for name in values:
                if name in arrays:
                    continue
                compact = name.casefold().replace("_", "").replace(" ", "")
                categorical = compact in {
                    "materialid",
                    "controlvolumeid",
                    "region",
                    "assignment",
                    "usage",
                    "flowcondition",
                    "transportcondition",
                }
                unit = "Pa" if "pressure" in compact else "m²" if "permeability" in compact else ""
                arrays[name] = VisualizationArray(
                    name,
                    association,
                    CATEGORICAL if categorical else CONTINUOUS,
                    unit,
                    "log" if "permeability" in compact else "linear",
                    display_name=field_label(name),
                )
        metadata = {
            "default_array": next((n for n in arrays if n != "ControlVolumeID"), None),
            "default_representation": "surface",
            "lazy_snapshots": True,
            "snapshot_count": len(paths),
            "time_labels": time_labels(times),
            "time_sources": [
                "physical_output_or_frozen_run" if t is not None else "unknown" for t in times
            ],
            "run_id": run_id,
            "historical": run_id is not None,
            "project_context": str(artifacts.project_root),
            "mapping_verified": verified,
            "provenance_warning": ""
            if verified
            else msg("text.legacy.visualization.configuration.result.provenance.is.unverified"),
        }
        dataset = VisualizationDataset(
            msg("text.pflotran.simulation.results"), first, arrays, times, metadata, snapshots
        )
        for cached_key in list(self._cache):
            if isinstance(cached_key, tuple) and cached_key[0] == "results":
                del self._cache[cached_key]
        self._cache[key] = dataset
        return dataset

    def selected_region(self, region, *, label=None, config=None):
        context = self._mesh(config)
        geometry = extract_region_geometry(
            context, region, self.pv, np, project=self.root, volumes=self._volumes(config)
        )
        geometry.point_data["Region"] = np.ones(geometry.n_points, dtype=int)
        representation = str(geometry.field_data["representation"][0])
        description = {
            "coordinate_marker": msg(
                "text.specified.coordinate.marker.not.a.reconstructed.control.volume.boundary"
            ),
            "boundary_connection_positions": msg(
                "text.boundary.connection.positions.read.from.ex.records.not.reconstructed.boundary.faces"
            ),
            "control_volume_representative_points": msg(
                "text.control.volume.representative.positions.uge.not.inp.cells.or.reconstructed.volume.boundaries"
            ),
        }.get(representation, str(geometry.field_data["description"][0]))
        metadata = {
            "default_array": "Region",
            "default_representation": "points",
            "context_geometry": context,
            "selection_description": description,
            "representation": str(geometry.field_data["representation"][0]),
        }
        return VisualizationDataset(
            label or region.name,
            geometry,
            {
                "Region": VisualizationArray(
                    "Region",
                    "point",
                    CATEGORICAL,
                    labels={1: region.name},
                    display_name=msg("viewer.region"),
                )
            },
            metadata=metadata,
        )

    def selected_assignment(self, assignment, region, property_name="Assignment", *, config=None):
        labels = {
            "Assignment": assignment.name,
            "Usage": assignment.usage,
            "Flow Condition": assignment.flow_condition_name or msg("text.not.set"),
            "Transport Condition": assignment.transport_condition_name or msg("text.not.set"),
            "Region": assignment.region_name,
        }
        dataset = self.selected_region(region, label=assignment.name, config=config)
        dataset.geometry.point_data[property_name] = np.ones(dataset.geometry.n_points, dtype=int)
        dataset.arrays = {
            property_name: VisualizationArray(
                property_name,
                "point",
                CATEGORICAL,
                labels={1: labels[property_name]},
                display_name=field_label(property_name),
            )
        }
        dataset.metadata.update(default_array=property_name, assignment_details=labels)
        return dataset
