from __future__ import annotations

import math
import threading
from dataclasses import dataclass

import numpy as np

from fracrtm.messages import join_text, render_text
from fracrtm.messages import message as msg

from .dataset import CATEGORICAL, VisualizationDataset
from .display_rules import PALETTE

REPRESENTATIONS = {
    "surface": ("surface", False),
    "surface_edges": ("surface", True),
    "wireframe": ("wireframe", False),
    "points": ("points", False),
}


@dataclass(frozen=True)
class VisualizationCameraPreset:
    name: str = "ENGINEERING_ISO"
    direction_from_focal: tuple[float, float, float] = (-1.0, -1.0, 0.8)
    view_up: tuple[float, float, float] = (0.0, 0.0, 1.0)

    def camera_position(self, bounds) -> tuple[tuple[float, float, float], ...]:
        xmin, xmax, ymin, ymax, zmin, zmax = map(float, bounds)
        focal = ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)
        diagonal = math.sqrt((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2)
        distance = max(diagonal, 1.0) * 1.35
        direction = self.direction_from_focal
        norm = math.sqrt(sum((v * v for v in direction)))
        position = tuple((focal[i] + distance * direction[i] / norm for i in range(3)))
        return (position, focal, self.view_up)


ENGINEERING_ISO = VisualizationCameraPreset()


def apply_default_engineering_camera(plotter, bounds=None) -> None:
    plotter.camera_position = ENGINEERING_ISO.camera_position(bounds or plotter.bounds)
    plotter.reset_camera()


class VisualizationEngine:
    def __init__(self, plotter):
        self.plotter = plotter
        self.dataset = None
        self._camera_initialized = False
        self._owner_thread = threading.get_ident()
        self._rendering = False
        self._actors = []
        self._pending_actors = []

    def set_dataset(self, dataset: VisualizationDataset, *, reset_camera: bool = True):
        self.dataset = dataset
        if reset_camera:
            self._camera_initialized = False

    def render(
        self,
        array_name=None,
        time_index=0,
        representation=None,
        opacity=1.0,
        show_legend=True,
        log_scale=None,
        selection=None,
    ):
        self._assert_render_thread()
        if self._rendering:
            raise RuntimeError(msg("text.recursive.vtk.render.request.was.blocked"))
        if self.dataset is None:
            raise ValueError(msg("text.no.visualization.dataset.is.selected"))
        self._rendering = True
        self._pending_actors = []
        try:
            dataset = self.dataset
            mesh = self._mesh_for_time(time_index)
            self._validate_mesh(mesh, msg("text.selected.geometry"))
            context = dataset.metadata.get("context_geometry")
            if context is not None:
                self._validate_mesh(context, msg("text.context.geometry"))
            self._clear_scene()
            if context is not None:
                self._add_mesh(context, color="#AAB5B7", opacity=0.1, show_edges=False)
            representation = representation or dataset.default_representation
            style, edges = REPRESENTATIONS.get(representation, REPRESENTATIONS["surface"])
            descriptor = dataset.arrays.get(array_name or dataset.default_array)
            kwargs = {
                "style": style,
                "show_edges": edges,
                "opacity": opacity,
                "edge_color": "#73878B",
            }
            if style == "points":
                kwargs["point_size"] = 6
            if descriptor is None:
                self._add_mesh(mesh, color="#69A7A4", **kwargs)
            elif descriptor.kind == CATEGORICAL:
                self._categorical(mesh, descriptor, kwargs, show_legend, selection)
            else:
                scale = (
                    descriptor.preferred_scale
                    if log_scale is None
                    else "log"
                    if log_scale
                    else "linear"
                )
                self._add_mesh(
                    mesh,
                    scalars=descriptor.name,
                    cmap="viridis",
                    log_scale=scale == "log",
                    preference=descriptor.association,
                    show_scalar_bar=show_legend,
                    scalar_bar_args={"title": render_text(descriptor.label)},
                    **kwargs,
                )
            self.plotter.add_axes(xlabel="X", ylabel="Y", zlabel="Z")
            if not self._camera_initialized:
                apply_default_engineering_camera(self.plotter, mesh.bounds)
                self._camera_initialized = True
            self.plotter.render()
            self._actors = self._pending_actors
            return mesh
        except Exception:
            self._remove_actors(self._pending_actors)
            self._actors = []
            raise
        finally:
            self._pending_actors = []
            self._rendering = False

    def _assert_render_thread(self):
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError(
                msg("text.vtk.rendering.must.run.on.the.thread.that.created.the.plotter")
            )

    @staticmethod
    def _validate_mesh(mesh, label):
        if mesh is None:
            raise ValueError(msg("diagnostic.engine.label.is.empty", label=label))
        points = getattr(mesh, "points", None)
        if points is not None:
            try:
                array = np.asarray(points, dtype=float)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    msg("diagnostic.engine.label.has.invalid.point.coordinates", label=label)
                ) from exc
            if array.ndim != 2 or array.shape[1] != 3 or array.shape[0] == 0:
                raise ValueError(
                    msg("diagnostic.engine.label.must.contain.an.n.x.3.point.array", label=label)
                )
            if not np.isfinite(array).all():
                raise ValueError(
                    msg("diagnostic.engine.label.contains.nan.or.infinite.coordinates", label=label)
                )
        elif hasattr(mesh, "n_points") and int(mesh.n_points) <= 0:
            raise ValueError(msg("diagnostic.engine.label.contains.no.points", label=label))
        bounds = getattr(mesh, "bounds", None)
        if bounds is None:
            raise ValueError(msg("diagnostic.engine.label.has.no.bounds", label=label))
        try:
            bounds_array = np.asarray(bounds, dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                msg("diagnostic.engine.label.has.invalid.bounds", label=label)
            ) from exc
        if bounds_array.size != 6 or not np.isfinite(bounds_array).all():
            raise ValueError(
                msg("diagnostic.engine.label.has.invalid.or.non.finite.bounds", label=label)
            )

    def _add_mesh(self, mesh, **kwargs):
        actor = self.plotter.add_mesh(mesh, **kwargs)
        if actor is not None:
            self._pending_actors.append(actor)
        return actor

    def _clear_scene(self):
        self._remove_actors(self._actors)
        self._actors = []
        self.plotter.clear()

    def _remove_actors(self, actors):
        remover = getattr(self.plotter, "remove_actor", None)
        if remover is None:
            return
        for actor in tuple(actors):
            try:
                remover(actor, render=False)
            except TypeError:
                try:
                    remover(actor)
                except Exception:
                    pass
            except Exception:
                pass

    def _mesh_for_time(self, index):
        d = self.dataset
        if not d.snapshots:
            return d.geometry
        index = max(0, min(int(index), len(d.snapshots) - 1))
        snapshot = d.snapshots[index]
        return snapshot if hasattr(snapshot, "points") else d.geometry

    def _categorical(self, mesh, descriptor, kwargs, show_legend, selection):
        preference = descriptor.association
        values = getattr(mesh, join_text("", [preference, "_data"])).get(descriptor.name)
        if values is None:
            raise ValueError(
                msg(
                    "diagnostic.engine.geometry.has.no.categorical.array.name", name=descriptor.name
                )
            )
        keys = sorted({int(value) for value in values})
        for index, key in enumerate(keys):
            if preference == "point":
                # Point categories identify control-volume sites, not display tetrahedra.
                import pyvista as pv

                mask = np.asarray(values) == key
                part = pv.PolyData(np.asarray(mesh.points)[mask])
                for name in mesh.point_data:
                    part.point_data[name] = np.asarray(mesh.point_data[name])[mask]
            else:
                part = mesh.extract_cells(np.flatnonzero(np.asarray(values) == key))
            selected = selection is None or key in set(
                selection if isinstance(selection, (list, tuple, set)) else [selection]
            )
            local = dict(kwargs)
            if preference == "point":
                local.update(style="points", point_size=6, show_edges=False)
            local["opacity"] = kwargs["opacity"] if selected else min(0.12, kwargs["opacity"])
            self._validate_mesh(part, msg("diagnostic.engine.categorical.subset.key", key=key))
            self._add_mesh(
                part,
                color=PALETTE[index % len(PALETTE)],
                label=render_text(descriptor.labels.get(key, str(key))),
                **local,
            )
        if show_legend:
            self.plotter.add_legend(bcolor="white", face="rectangle")

    def reset_camera(self):
        if self.dataset is not None:
            mesh = self._mesh_for_time(0)
            apply_default_engineering_camera(self.plotter, mesh.bounds)
            self._camera_initialized = True

    def clear(self):
        self._assert_render_thread()
        self._remove_actors(self._pending_actors)
        self._pending_actors = []
        self._clear_scene()
        self.dataset = None
        self._camera_initialized = False
        self.plotter.render()
