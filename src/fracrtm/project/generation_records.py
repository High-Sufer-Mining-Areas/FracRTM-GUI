from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from fracrtm.messages import Message, join_text, prepare_json
from fracrtm.messages import message as msg

from .project_state import ArtifactState, ProjectState

STAGES = ("dfn", "octree", "upscale", "pflotran_input")
DFN_INPUTS = (
    "reduced_mesh.inp",
    "normal_vectors.dat",
    "translations.dat",
    "aperture.dat",
    "params.txt",
    "poly_info.dat",
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    stored = dict(value) if isinstance(value, dict) else value
    if isinstance(stored, dict):
        for key, item in value.items():
            if isinstance(item, Message):
                stored[key] = item.render()
                stored[key + "_data"] = item.to_record()
    temporary.write_text(
        json.dumps(prepare_json(stored), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def digest_json(value):
    return hashlib.sha256(
        json.dumps(
            prepare_json(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_resource(project, relative):
    text = str(relative).replace("\\", "/")
    p = PurePosixPath(text)
    if not text or p.is_absolute() or PureWindowsPath(text).drive or (".." in p.parts):
        raise ValueError(
            msg(
                "diagnostic.generation_records.unsafe.project.relative.resource.path.relative",
                relative=f"{relative!r}",
            )
        )
    root = Path(project).resolve()
    result = root.joinpath(*p.parts).resolve()
    try:
        result.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            msg(
                "diagnostic.generation_records.resource.resolves.outside.project.relative",
                relative=f"{relative!r}",
            )
        ) from exc
    from .storage import resource_path

    return resource_path(root, text, result)


def file_manifest(project, paths):
    root = Path(project).resolve()
    rows = []
    for relative in sorted(set((str(p).replace("\\", "/") for p in paths))):
        p = safe_resource(root, relative)
        empty_boundary_list = relative in {
            join_text("", ["dfn/dfnGen_output/", face, ".dat"])
            for face in ("front", "back", "left", "right", "top", "bottom")
        }
        if not p.is_file() or (p.stat().st_size == 0 and (not empty_boundary_list)):
            raise ValueError(
                msg(
                    "diagnostic.generation_records.required.resource.is.missing.or.empty.relative",
                    relative=relative,
                )
            )
        rows.append(
            {
                "path": str(relative).replace("\\", "/"),
                "size": p.stat().st_size,
                "sha256": file_digest(p),
            }
        )
    return rows


def collect_pflotran_dependencies(project, config):
    paths = [config.grid.file]
    if config.simulation.uses_transport():
        paths.append(config.chemistry.database.relative_path)
    paths.extend((d.file for d in config.datasets))
    from fracrtm.config.contracts import unassigned_boundary_placeholder

    paths.extend(
        r.file_path
        for r in config.regions
        if r.region_type == "FILE" and not unassigned_boundary_placeholder(config, r)
    )
    mapping = config.spatial_assignment.material_mapping
    if mapping and mapping.file_path:
        paths.append(mapping.file_path)
    return file_manifest(project, paths)


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _stage_products(project, stage):
    root = Path(project)
    if stage == "dfn":
        paths = ["dfn/" + n for n in DFN_INPUTS]
        polygons = root / "dfn/dfnGen_output/polygons.dat"
        if polygons.is_file():
            paths.append("dfn/dfnGen_output/polygons.dat")
    elif stage == "octree":
        paths = ["octree/full_mesh.uge", "octree/octree_dfn.inp", "octree/connections.p"]
        paths += [p.relative_to(root).as_posix() for p in (root / "octree").rglob("*.ex")]
        paths += [p.relative_to(root).as_posix() for p in (root / "octree").rglob("*.zone")]
        if (root / "octree/boundary_conversion.json").is_file():
            paths.append("octree/boundary_conversion.json")
    elif stage == "upscale":
        paths = ["octree/materials.h5", "octree/mesh_porosity.h5", "octree/mesh_permeability.h5"]
    elif stage == "pflotran_input":
        paths = ["pflotran/model.in"]
    else:
        raise ValueError(
            msg("diagnostic.generation_records.unknown.generation.stage.stage", stage=stage)
        )
    return paths


def capture_stage_identity(project, stage, *, config_override=None):
    root = Path(project).resolve()
    resources = []
    if stage == "dfn":
        config = _json(root / "dfn/dfn_config.json")
    elif stage in ("octree", "upscale"):
        from fracrtm.config.continuum_config import ContinuumConfig

        c = _json(root / "octree/continuum_config.json")
        config = {
            k: c.get(k) for k in ("base_cell_size", "refinement_levels", "output_directory_name")
        }
        config.update(ContinuumConfig.from_dict(c).boundary_identity())
        if stage == "upscale":
            config.update({k: c.get(k) for k in ("matrix_permeability", "matrix_porosity")})
        config["dfn"] = _json(root / "dfn/dfn_config.json")
        dfn_paths = ["dfn/" + n for n in DFN_INPUTS]
        dfn_paths += [
            p.relative_to(root).as_posix()
            for p in (root / "dfn/dfnGen_output").rglob("*")
            if p.is_file()
        ]
        resources = file_manifest(root, dfn_paths)
        if stage == "upscale":
            resources += file_manifest(root, _stage_products(root, "octree"))
    elif stage == "pflotran_input":
        from fracrtm.config.pflotran import PFLOTRANConfig

        c = config_override or PFLOTRANConfig.load_json(root / "pflotran/model_config.json")
        config = c.scientific_dict()
        resources = collect_pflotran_dependencies(root, c)
        continuum = root / "octree/continuum_config.json"
        if continuum.is_file():
            from fracrtm.config.continuum_config import ContinuumConfig

            config["continuum"] = ContinuumConfig.load_json(continuum).scientific_dict()
        for rel in ("dfn/dfn_config.json",):
            if (root / rel).is_file():
                resources += file_manifest(root, [rel])
    else:
        raise ValueError(
            msg("diagnostic.generation_records.unknown.generation.stage.stage", stage=stage)
        )
    invalidations = {}
    for ancestor in STAGES[: STAGES.index(stage) + 1]:
        invalidation = root / "records/invalidation" / (ancestor + ".json")
        if invalidation.is_file():
            invalidations[ancestor] = _json(invalidation).get("token")
    body = {
        "stage": stage,
        "configuration": config,
        "resources": resources,
        "invalidation_tokens": invalidations,
    }
    return {**body, "fingerprint": digest_json(body)}


def invalidate_stage(project, stage, reason=msg("text.configuration.edited")):
    if stage not in STAGES:
        raise ValueError(msg("diagnostic.generation_records.unknown.stage.stage", stage=stage))
    value = {"stage": stage, "reason": str(reason), "at": utc_now(), "token": uuid.uuid4().hex}
    atomic_json(Path(project) / "records/invalidation" / (stage + ".json"), value)
    return value


def record_stage_generation(project, stage, launch_identity, run_id=None):
    if launch_identity.get("stage") != stage:
        raise ValueError(msg("text.generation.identity.belongs.to.another.stage"))
    root = Path(project).resolve()
    products = file_manifest(root, _stage_products(root, stage))
    record = generation_record(stage, launch_identity, products, run_id)
    atomic_json(root / "records/generation" / (stage + ".json"), record)
    return record


def generation_record(stage, launch_identity, products, run_id=None):
    record = {
        "schema_version": "1.0",
        "stage": stage,
        "generated_at": utc_now(),
        "generation_id": uuid.uuid4().hex,
        "run_id": run_id,
        "launch_identity": launch_identity,
        "products": products,
        "product_fingerprint": digest_json(products),
    }
    return record


def evaluate_stage(project, stage):
    root = Path(project).resolve()
    path = root / "records/generation" / (stage + ".json")
    if not path.is_file():
        return (
            ArtifactState.STALE
            if any(((root / p).is_file() for p in _stage_products(root, stage)))
            else ArtifactState.NOT_GENERATED
        )
    try:
        record = _json(path)
        if (
            record["launch_identity"]["fingerprint"]
            != capture_stage_identity(root, stage)["fingerprint"]
        ):
            return ArtifactState.STALE
        products = file_manifest(root, _stage_products(root, stage))
        if digest_json(products) != record["product_fingerprint"]:
            return ArtifactState.STALE
        return ArtifactState.VALID
    except (OSError, ValueError, KeyError, TypeError):
        return ArtifactState.STALE


def evaluate_project_states(project):
    from .pflotran_state import evaluate_pflotran_states

    states = {s: evaluate_stage(project, s) for s in STAGES[:3]}
    if states["dfn"] != ArtifactState.VALID and states["octree"] == ArtifactState.VALID:
        states["octree"] = ArtifactState.STALE
    if states["octree"] != ArtifactState.VALID and states["upscale"] == ArtifactState.VALID:
        states["upscale"] = ArtifactState.STALE
    pf = evaluate_pflotran_states(project)
    input_state = pf.input
    simulation = pf.simulation
    if states["upscale"] != ArtifactState.VALID:
        if input_state == ArtifactState.VALID:
            input_state = ArtifactState.STALE
        if simulation == ArtifactState.VALID:
            simulation = ArtifactState.STALE
    return ProjectState(
        states["dfn"], states["octree"], states["upscale"], input_state, simulation, pf.config
    )
