from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fracrtm.config.pflotran import PFLOTRANConfig
from fracrtm.config.validation_messages import format_validation_issue
from fracrtm.messages import join_text, prepare_json
from fracrtm.pflotran.compiler import PFLOTRANCompiler
from fracrtm.project.generation_records import (
    capture_stage_identity,
    collect_pflotran_dependencies,
    generation_record,
)
from fracrtm.project.simulation_mesh import DEFAULT_MODEL_SOURCE, ModelSourceProvider
from fracrtm.project.storage import FileTransaction, ProjectLock, recover_project


@dataclass(frozen=True)
class ModelPreparationResult:
    config_path: Path
    input_path: Path
    manifest_path: Path
    input_sha256: str


def prepare_pflotran_model(project_directory, config, model_source=DEFAULT_MODEL_SOURCE):
    with ProjectLock(project_directory):
        recover_project(project_directory)
        return _prepare_pflotran_model(project_directory, config, model_source)


def _prepare_pflotran_model(
    project_directory: str | Path,
    config: PFLOTRANConfig,
    model_source: ModelSourceProvider = DEFAULT_MODEL_SOURCE,
) -> ModelPreparationResult:
    project = Path(project_directory).expanduser().resolve()
    report = config.validate(project)
    if not report.valid:
        raise ValueError(
            join_text("\n", (format_validation_issue(issue, config) for issue in report.errors))
        )
    simulation_mesh = model_source.simulation_mesh(project)
    validate_referenced_files(project, config)
    from fracrtm.pflotran.spatial_validation import validate_spatial_resources

    validate_spatial_resources(project, config).raise_for_errors()
    text = PFLOTRANCompiler().build(config, input_directory="pflotran")
    target = project / "pflotran"
    target.mkdir(parents=True, exist_ok=True)
    config_path = target / "model_config.json"
    input_path = target / "model.in"
    manifest_path = target / "model_manifest.json"
    generation_identity = capture_stage_identity(project, "pflotran_input", config_override=config)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    config_digest = hashlib.sha256(
        json.dumps(
            prepare_json(config.scientific_dict()), ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
    ).hexdigest()
    manifest = {
        "schema_version": "2.0",
        "status": "ready",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": "pflotran/model_config.json",
        "input": "pflotran/model.in",
        "working_directory": "pflotran",
        "input_sha256": digest,
        "config_sha256": config_digest,
        "resource_manifest": collect_pflotran_dependencies(project, config),
        "generation_fingerprint": generation_identity["fingerprint"],
        "simulation_mesh": {
            "provider": simulation_mesh.metadata.get("provider", "unknown"),
            "grid": _relative(project, simulation_mesh.grid),
            "visualization_geometry": _relative(project, simulation_mesh.visualization_geometry),
            "materials": _relative(project, simulation_mesh.materials),
            "permeability": _relative(project, simulation_mesh.permeability),
            "porosity": _relative(project, simulation_mesh.porosity),
            "boundaries": {
                name: item.relative_path for name, item in simulation_mesh.boundaries.items()
            },
        },
    }
    transaction = FileTransaction(project, "model")
    transaction.add_json("pflotran/model_config.json", config.to_dict())
    transaction.add_text("pflotran/model.in", text)
    transaction.add_json("pflotran/model_manifest.json", manifest)
    products = [{"path": "pflotran/model.in", "size": len(text.encode("utf-8")), "sha256": digest}]
    transaction.add_json(
        "records/generation/pflotran_input.json",
        generation_record("pflotran_input", generation_identity, products),
    )
    transaction.commit()
    return ModelPreparationResult(config_path, input_path, manifest_path, digest)


def _relative(project: Path, path: Path | None) -> str:
    return path.relative_to(project).as_posix() if path else ""


def validate_referenced_files(project: Path, config: PFLOTRANConfig) -> None:
    collect_pflotran_dependencies(project, config)


def _atomic_text(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _atomic_json(path: Path, value) -> None:
    _atomic_text(path, json.dumps(prepare_json(value), ensure_ascii=False, indent=2) + "\n")
