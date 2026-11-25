from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from fracrtm.config.pflotran import PFLOTRANConfig
from fracrtm.messages import message as msg
from fracrtm.messages import prepare_json, record_message
from fracrtm.pflotran.compiler import PFLOTRANCompiler
from fracrtm.pflotran.model_service import validate_referenced_files

from .generation_records import evaluate_stage
from .metadata import read_record
from .project_state import ArtifactState


@dataclass(frozen=True)
class PFLOTRANStateEvaluation:
    config: ArtifactState
    input: ArtifactState
    simulation: ArtifactState
    messages: tuple[str, ...] = ()


def evaluate_pflotran_states(project_directory: str | Path) -> PFLOTRANStateEvaluation:
    project = Path(project_directory).expanduser().resolve()
    pflotran = project / "pflotran"
    config_path = pflotran / "model_config.json"
    input_path = pflotran / "model.in"
    messages: list[str] = []
    try:
        manifest = read_record(project, "pflotran/model_manifest.json")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return PFLOTRANStateEvaluation(
            ArtifactState.FAILED,
            ArtifactState.STALE,
            ArtifactState.STALE,
            (msg("state.record_failed", reason=exc),),
        )
    if not config_path.is_file():
        return PFLOTRANStateEvaluation(
            ArtifactState.NOT_GENERATED,
            ArtifactState.NOT_GENERATED,
            _simulation_state(pflotran, messages),
            tuple(messages),
        )
    try:
        config = PFLOTRANConfig.load_json(config_path)
        report = config.validate(project)
    except Exception as exc:
        messages.append(msg("state.config_failed", reason=exc))
        return PFLOTRANStateEvaluation(
            ArtifactState.FAILED,
            ArtifactState.STALE,
            _simulation_state(pflotran, messages),
            tuple(messages),
        )
    if not report.valid:
        messages.extend((issue.message for issue in report.errors))
        return PFLOTRANStateEvaluation(
            ArtifactState.FAILED,
            ArtifactState.STALE,
            _simulation_state(pflotran, messages),
            tuple(messages),
        )
    config_state = ArtifactState.VALID
    if not input_path.is_file() or manifest is None:
        messages.append(msg("text.pflotran.input.file.or.generation.manifest.does.not.exist"))
        input_state = ArtifactState.NOT_GENERATED
    else:
        try:
            validate_referenced_files(project, config)
            from fracrtm.pflotran.spatial_validation import validate_spatial_resources

            validate_spatial_resources(project, config).raise_for_errors()
            actual_text = input_path.read_text(encoding="utf-8")
            actual_digest = hashlib.sha256(actual_text.encode("utf-8")).hexdigest()
            rendered = PFLOTRANCompiler().build(config, input_directory="pflotran")
            rendered_digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
            config_digest = hashlib.sha256(
                json.dumps(
                    prepare_json(config.scientific_dict()), ensure_ascii=False, sort_keys=True
                ).encode("utf-8")
            ).hexdigest()
            if (
                manifest.get("status") != "ready"
                or manifest.get("input_sha256") != actual_digest
                or rendered_digest != actual_digest
                or (manifest.get("config_sha256", config_digest) != config_digest)
            ):
                raise ValueError(
                    msg(
                        "text.model.in.does.not.match.the.current.configuration.or.generation.manifest"
                    )
                )
            if evaluate_stage(project, "pflotran_input") != ArtifactState.VALID:
                raise ValueError(
                    msg(
                        "text.the.input.has.no.generation.record.matching.the.current.configuration.and.resources.legacy.project"
                    )
                )
            input_state = ArtifactState.VALID
        except Exception as exc:
            messages.append(msg("diagnostic.pflotran_state.invalid.pflotran.input.exc", exc=exc))
            input_state = ArtifactState.STALE
    simulation_state = _simulation_state(pflotran, messages)
    if input_state != ArtifactState.VALID and simulation_state == ArtifactState.VALID:
        simulation_state = ArtifactState.STALE
    return PFLOTRANStateEvaluation(config_state, input_state, simulation_state, tuple(messages))


def _simulation_state(pflotran: Path, messages: list[str]) -> ArtifactState:
    try:
        record = read_record(pflotran.parent, "pflotran/simulation_run.json")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        messages.append(msg("state.record_failed", reason=exc))
        return ArtifactState.FAILED
    if record is None:
        return ArtifactState.NOT_GENERATED
    try:
        if not isinstance(record, dict):
            raise ValueError("Invalid simulation reference")
    except Exception as exc:
        messages.append(msg("state.record_failed", reason=exc))
        return ArtifactState.FAILED
    if not record.get("run_id"):
        messages.append(msg("state.identity_failed", reason=msg("text.no.run.records")))
        return ArtifactState.FAILED
    try:
        from .runs import (
            explicit_pflotran_output_error,
            get_run,
            run_matches_current,
            verify_run_files,
        )

        task = get_run(pflotran.parent, record["run_id"])
        record = json.loads(task.record_path.read_text(encoding="utf-8"))
        output = task.output_directory
    except (ValueError, OSError, KeyError) as exc:
        messages.append(msg("state.identity_failed", reason=exc))
        return ArtifactState.FAILED
    if record.get("status") in ("prepared", "running", "synchronizing", "awaiting_sync"):
        messages.append(msg("state.recovery", run_id=task.run_id, state=record.get("status")))
        return ArtifactState.STALE
    if record.get("status") == "unverified":
        messages.append(
            record_message(record, "error") or msg("state.not_complete", run_id=task.run_id)
        )
        return ArtifactState.UNVERIFIED
    if record.get("status") in ("cancelled", "failed") or record.get("failure_stage"):
        messages.append(
            msg(
                "state.run_issue",
                run_id=task.run_id,
                code=record.get("failure_stage") or record.get("status"),
                reason=record_message(record, "error") or "",
            )
        )
        return ArtifactState.FAILED
    if record.get("status") != "completed":
        messages.append(msg("state.not_complete", run_id=task.run_id))
        return ArtifactState.STALE
    if record.get("completion_status") != "verified":
        messages.append(msg("repair.historical_completion"))
        return ArtifactState.UNVERIFIED
    if record.get("returncode") != 0:
        messages.append(record.get("failure_stage") or msg("text.pflotran.run.failed"))
        return ArtifactState.FAILED
    if not record.get("synchronized"):
        messages.append(msg("text.pflotran.result.synchronization.failed"))
        return ArtifactState.FAILED
    if not (output / "model.out").is_file():
        messages.append(msg("text.pflotran.results.are.missing.model.out"))
        return ArtifactState.FAILED
    integrity = verify_run_files(task)
    if integrity:
        messages.extend(
            (msg("state.integrity", run_id=task.run_id, reason=message) for message in integrity)
        )
        return ArtifactState.STALE
    try:
        diagnostic = explicit_pflotran_output_error(output / "model.out")
    except OSError as exc:
        messages.append(msg("state.output_read_failed", reason=exc))
        return ArtifactState.FAILED
    if diagnostic:
        messages.append(msg("state.output_error", reason=diagnostic))
        return ArtifactState.FAILED
    if not run_matches_current(task):
        messages.append(msg("state.mismatch", run_id=task.run_id))
        return ArtifactState.STALE
    return ArtifactState.VALID
