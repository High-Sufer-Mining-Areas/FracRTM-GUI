from __future__ import annotations

from dataclasses import dataclass

from fracrtm.messages import canonical_text
from fracrtm.messages import message as msg

PALETTE = ("#2f7e79", "#e07a3f", "#5b7db1", "#b56aa0", "#d2a83a", "#6e8f4e", "#8b6d5c")


@dataclass(frozen=True)
class Category:
    key: object
    label: str
    count: int
    color: str


def material_categories(values, materials=()):
    names = {
        int(item.material_id): str(item.name).capitalize()
        if str(item.name).islower()
        else str(item.name)
        for item in materials
    }
    unique = sorted({int(value) for value in values})
    return tuple(
        (
            Category(
                value,
                names.get(value, msg("material.new_name", index=value)),
                sum((int(v) == value for v in values)),
                PALETTE[i % len(PALETTE)],
            )
            for i, value in enumerate(unique)
        )
    )


def assignment_category(assignment, mode="assignment"):
    if mode == "usage":
        return assignment.usage
    if mode == "flow":
        return assignment.flow_condition_name or msg("text.no.flow.condition")
    if mode == "transport":
        return assignment.transport_condition_name or msg("text.no.transport.condition")
    return assignment.name


def region_geometry_kind(region):
    if region.region_type == "COORDINATE":
        return "point"
    if region.region_type == "FILE":
        return "boundary_positions"
    return "control_volume_positions"


def extract_region_geometry(
    mesh, region, pyvista_module, numpy_module, *, project=None, volumes=None
):
    from .spatial_mapping import resolve_region_points

    if project is None or volumes is None:
        raise ValueError(
            msg(
                "text.region.location.requires.the.project.and.validated.uge.control.volume.identities"
            )
        )
    selection = resolve_region_points(project, region, volumes)
    geometry = pyvista_module.PolyData(selection.points)
    if len(selection.control_ids) == len(selection.points):
        geometry.point_data["ControlVolumeID"] = selection.control_ids
    geometry.field_data["representation"] = [selection.representation]
    geometry.field_data["description"] = [canonical_text(selection.description)]
    return geometry


def field_label(name):
    labels = {
        "Material": msg("viewer.material"),
        "Region": msg("viewer.region"),
        "Assignment": msg("viewer.assignment"),
        "Usage": msg("viewer.usage"),
        "Flow Condition": msg("model.flow_condition"),
        "Transport Condition": msg("model.transport_condition"),
        "Porosity": msg("viewer.porosity"),
        "Permeability": msg("viewer.permeability"),
        "liquid_pressure": msg("text.liquid.pressure"),
        "liquid_saturation": msg("text.liquid.saturation"),
        "porosity": msg("viewer.porosity"),
        "ControlVolumeID": msg("viewer.control_volume_id"),
    }
    return labels.get(name, name)
