from __future__ import annotations

import json
import uuid
from dataclasses import asdict, replace
from pathlib import Path

from PyQt5 import QtCore, QtWidgets

from fracrtm.config import BlankConfigFactory, ContinuumConfig, DfnConfig
from fracrtm.config.pflotran import PFLOTRANConfig
from fracrtm.messages import (
    current_locale,
    exception_text,
    join_text,
    prepare_json,
    record_message,
    render_text,
    state_text,
)
from fracrtm.messages import message as msg
from fracrtm.project import ArtifactState, ProjectState, ProjectSyncService
from fracrtm.project.generation_records import (
    evaluate_project_states,
    invalidate_stage,
    record_stage_generation,
    utc_now,
)
from fracrtm.project.runs import (
    begin_task,
    finish_task,
    get_run,
    list_runs,
    run_display_status,
    update_run,
)
from fracrtm.runtime.logged_process import LoggedProcess
from fracrtm.runtime.resource_limits import log_tail
from fracrtm.runtime.wsl_arguments import dfn_python_args
from fracrtm.ui.localization import diagnostic_summary, format_message, ui_call
from fracrtm.ui.qt_helpers import MessageBox


def final_output_time(task, record):
    if record.get("final_time_seconds") is not None:
        return record["final_time_seconds"]
    from fracrtm.project.completion import scan_log

    try:
        return scan_log(task.output_directory / "model.out")["last_time_seconds"]
    except (OSError, ValueError):
        return None


class WorkflowController:
    def __init__(self, window):
        self.window = window
        self.task = None
        self.execution = None
        self.remote_root = None
        self.remote_source = None
        self.sync = None
        self.cancel_requested = False
        self.recovery_records = []
        self._output_fragments = {False: "", True: ""}

    @property
    def busy(self):
        return self.task is not None or bool(self.window._workers)

    def status(self, text):
        w = self.window
        ui_call(w.bottom_status, "setText", text)
        w._logs.append(text)
        if w.workspace is not None:
            try:
                path = w.workspace.local_logs_dir / "gui.log"
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(str(join_text("", [utc_now(), " ", text, "\n"])))
            except OSError as exc:
                warning = format_message("log.write_failed", reason=exc)
                w._logs.append(warning)
                ui_call(w.bottom_status, "setText", join_text("", [text, " | ", warning]))

    def preserve_recovery(self, task, stage, error, code):
        record = {
            "run_id": task.run_id if task else None,
            "failure_stage": stage,
            "error": str(error),
            "returncode": code,
            "time": utc_now(),
            "run_directory": str(task.directory) if task else None,
            "execution": getattr(self.execution, "record", {}),
            "gui_log_tail": self.window._logs[-200:],
        }
        if self.execution and hasattr(self.execution, "pending_log_bytes"):
            record["unpersisted_logs"] = {
                k: v.decode("utf-8", errors="replace")
                for k, v in self.execution.pending_log_bytes().items()
            }
        self.recovery_records.append(record)
        try:
            root = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.GenericDataLocation)
            if not root:
                raise OSError(msg("text.no.user.data.directory.available"))
            directory = Path(root) / "FracRTM-GUI/recovery"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / join_text("", [uuid.uuid4().hex, ".json"])
            with path.open("x", encoding="utf-8") as stream:
                json.dump(prepare_json(record), stream, ensure_ascii=False, indent=2)
            self.window._logs.append(format_message("run.diagnostic_saved", path=path))
        except OSError as exc:
            self.window._logs.append(format_message("log.memory_only", reason=exc))

    def scan(self):
        w = self.window
        if w.workspace is None:
            w.state = ProjectState()
        else:
            try:
                w.state = evaluate_project_states(w.workspace.local_root)
            except (OSError, ValueError) as exc:
                w.state = ProjectState()
                w.state.invalidate_from_dfn()
                self.status(format_message("project.state_failed", reason=exc))
        if self.task:
            attr = {
                "dfn": "dfn_state",
                "octree": "octree_state",
                "upscale": "upscale_state",
                "pflotran": "simulation_state",
            }[self.task.stage]
            setattr(w.state, attr, ArtifactState.RUNNING)

    def load_models(self):
        w = self.window
        specs = [
            ("_dfn_config", "dfn/dfn_config.json", DfnConfig, BlankConfigFactory.dfn),
            (
                "_continuum_config",
                "octree/continuum_config.json",
                ContinuumConfig,
                BlankConfigFactory.continuum,
            ),
            (
                "_pflotran_config",
                "pflotran/model_config.json",
                PFLOTRANConfig,
                BlankConfigFactory.pflotran,
            ),
        ]
        for name, relative, cls, blank in specs:
            value = blank()
            if w.workspace is not None:
                path = w.workspace.local_root / relative
                if path.is_file():
                    try:
                        value = cls.load_json(path)
                    except Exception as exc:
                        message = format_message("config.load_failed", path=relative, reason=exc)
                        w._logs.append(message)
                        log = w.workspace.local_logs_dir / "configuration_errors.log"
                        log.parent.mkdir(parents=True, exist_ok=True)
                        with log.open("a", encoding="utf-8") as stream:
                            stream.write(join_text("", [utc_now(), " ", message, "\n"]))
            setattr(w, name, value)

    def changed(self, stage):
        w = self.window
        if w.workspace is not None:
            if stage in ("octree", "upscale") and (not w._continuum_config.validate()):
                w._continuum_config.save_json(
                    w.workspace.local_octree_dir / "continuum_config.json"
                )
            invalidate_stage(w.workspace.local_root, stage)
            self.scan()
        w._refresh()

    def background(self, function, success, failure, message):
        from .qt_helpers import BackgroundCall

        w = self.window
        worker = BackgroundCall(function)
        w._workers.add(worker)

        def done(value):
            w._workers.discard(worker)
            try:
                success(value)
            except Exception as exc:
                from fracrtm.runtime.diagnostics import FailureDiagnostic

                self.record_diagnostic(FailureDiagnostic.capture(exc))
                failure(exception_text(exc))

        def failed(error):
            w._workers.discard(worker)
            if worker.last_failure is not None:
                self.record_diagnostic(worker.last_failure)
            failure(error)

        worker.signals.succeeded.connect(done)
        worker.signals.failed.connect(failed)
        self.status(message)
        w._buttons()
        QtCore.QThreadPool.globalInstance().start(worker)

    def record_diagnostic(self, diagnostic):
        from fracrtm.project.generation_records import atomic_json

        record = diagnostic.to_record()
        self.recovery_records.append(record)
        if self.window.workspace is not None:
            directory = self.window.workspace.local_logs_dir / "diagnostics"
        else:
            directory = (
                Path(
                    QtCore.QStandardPaths.writableLocation(
                        QtCore.QStandardPaths.GenericDataLocation
                    )
                )
                / "FracRTM-GUI/diagnostics"
            )
        try:
            atomic_json(directory / (uuid.uuid4().hex + ".json"), record)
        except OSError as exc:
            self.status(format_message("log.memory_only", reason=exc))

    def run_dfn(self):
        w = self.window
        if not w._require_project() or self.busy:
            return
        errors = w._dfn_config.validate()
        if errors:
            return w._warn(msg("dialog.dfn_configuration.title"), join_text("\n", errors))
        w._dfn_config.save_json(w.workspace.local_dfn_dir / "dfn_config.json")
        self.launch("dfn", w.dfn_cpu.value())

    def run_continuum(self, stage):
        w = self.window
        if not w._require_project() or self.busy:
            return
        self.scan()
        upstream = w.state.dfn_state if stage == "octree" else w.state.octree_state
        if upstream != ArtifactState.VALID:
            return w._warn(
                msg("text.upstream.resource.is.stale"),
                msg(
                    "text.regenerate.the.upstream.stage.legacy.files.remain.available.for.inspection.but.do.not.prove"
                ),
            )
        try:
            config = replace(
                w._continuum_config,
                base_cell_size=w.base_cell.value(),
                refinement_levels=w.refinement.value(),
                matrix_permeability=float(w.matrix_perm.text()),
                matrix_porosity=w.matrix_porosity.value(),
            )
            errors = config.validate()
            if errors:
                raise ValueError(join_text("\n", errors))
        except Exception as exc:
            return w._warn(msg("dialog.udfm_configuration.title"), exception_text(exc))
        w._continuum_config = config
        config.save_json(w.workspace.local_octree_dir / "continuum_config.json")
        self.launch(stage, w.dfn_cpu.value())

    def prepare_input(self):
        w = self.window
        if not w._require_project() or self.busy:
            return
        self.scan()
        if w.state.upscale_state != ArtifactState.VALID:
            return w._warn(
                msg("text.udfm.provenance.is.stale"),
                msg("text.regenerate.dfn.udfm.before.preparing.a.verified.current.model"),
            )
        from fracrtm.pflotran.model_service import prepare_pflotran_model

        try:
            result = prepare_pflotran_model(
                w.workspace.local_root, w._pflotran_config, w.model_source
            )
            self.status(format_message("input.generated", path=result.input_path))
        except Exception as exc:
            self.status(format_message("input.failed", reason=exc))
            w._warn(msg("text.input.generation.failed"), exception_text(exc))
        self.scan()
        w._refresh()

    def check_model(self):
        w = self.window
        if not w._require_project():
            return
        from fracrtm.pflotran.spatial_validation import validate_spatial_resources

        report = w._pflotran_config.validate(w.workspace.local_root)
        space = validate_spatial_resources(w.workspace.local_root, w._pflotran_config)
        from fracrtm.config.validation_messages import format_validation_issue

        messages = [
            format_validation_issue(i, w._pflotran_config) for i in report.errors + report.warnings
        ]
        messages += [
            format_validation_issue(i, w._pflotran_config) for i in space.errors + space.warnings
        ]
        MessageBox.information(
            w,
            msg("text.model.checks"),
            join_text("\n", messages)
            or msg(
                "text.no.structural.errors.found.scientific.validity.remains.the.modeller.s.responsibility"
            ),
        )

    def run_pflotran(self):
        w = self.window
        if not w._require_project() or self.busy:
            return
        self.scan()
        if w.state.pflotran_input_state != ArtifactState.VALID:
            return w._warn(
                msg("text.pflotran.input.is.stale"),
                msg("text.check.current.configuration.and.resources.then.regenerate.model.in"),
            )
        if w._available_processors is None:
            w._refresh_available_processors(start_after=True)
            return
        self.launch("pflotran", w.pf_processes.value())

    def launch(self, stage, ncpu):
        w = self.window
        self.cancel_requested = False
        self.remote_source = None
        try:
            self.task = begin_task(w.workspace.local_root, stage, runtime=w.settings, ncpu=ncpu)
        except Exception as exc:
            self.status(format_message("run.snapshot_failed", reason=exc))
            return w._warn(msg("text.launch.preparation.failed"), exception_text(exc))
        task = self.task
        settings = type(w.settings)(**asdict(w.settings))
        workspace = w.workspace
        repo_root = w._repo_root
        oversubscribe = w.allow_oversubscribe.isChecked()
        available_processors = w._available_processors
        self.sync = ProjectSyncService(
            workspace, settings.distribution, settings.wsl_user, task=task
        )
        self.remote_root = join_text("", [workspace.wsl_job_dir, "/runs/", task.run_id])
        remote = self.remote_root
        self.scan()
        w._refresh()

        def prepare():
            sync = self.sync
            sync.push_run_inputs(task, remote)
            from fracrtm.runtime.execution_identity import freeze_execution_code

            repo = freeze_execution_code(task, sync, repo_root, remote, settings)
            if stage == "pflotran":
                from fracrtm.pflotran.run_service import build_pflotran_run_context

                context = build_pflotran_run_context(
                    workspace,
                    settings,
                    ncpu,
                    available_processors=available_processors,
                    oversubscribe=oversubscribe,
                    task=task,
                    remote_root=remote,
                )
                args = list(context.args)
                source = remote + "/pflotran"
            else:
                if stage == "dfn":
                    source = remote + "/work"
                    script = [
                        "-B",
                        "-m",
                        "fracrtm.dfn.generate_dfn",
                        "--language",
                        current_locale(),
                        "--config",
                        remote + "/dfn/dfn_config.json",
                        "--job-dir",
                        source,
                        "--ncpu",
                        str(ncpu),
                    ]
                else:
                    config = ContinuumConfig.load_json(
                        task.snapshot_directory / "octree/continuum_config.json"
                    )
                    source = remote + "/dfn/" + config.output_directory_name
                    if stage == "upscale":
                        # Preserve connections.p for the upscaling stage.
                        sync._run("/bin/mkdir", "-p", source)
                        sync._run("/bin/cp", "-a", remote + "/octree/.", source + "/")
                    script = [
                        "-B",
                        "-m",
                        "fracrtm.dfn.generate_continuum",
                        "--language",
                        current_locale(),
                        "--config",
                        remote + "/octree/continuum_config.json",
                        "--job-dir",
                        remote + "/dfn",
                        "--ncpu",
                        str(ncpu),
                        "--stage",
                        stage,
                    ]
                args = dfn_python_args(settings, script, pythonpath=repo)
            split = args.index("--")
            prefix, payload = (args[: split + 1], args[split + 1 :])
            from fracrtm.runtime.supervisor_snapshot import prepare_supervisor

            supervisor = prepare_supervisor(task, sync, repo_root, remote)
            control = remote + "/task_control.json"
            supervised = prefix + [
                settings.dfn_python,
                "-B",
                supervisor,
                "--control",
                control,
                "--token",
                task.run_id,
                "--",
                *payload,
            ]
            cancel = [
                "wsl.exe",
                "-d",
                settings.distribution,
                "-u",
                settings.wsl_user,
                "--",
                settings.dfn_python,
                "-B",
                supervisor,
                "--control",
                control,
                "--token",
                task.run_id,
                "--cancel",
            ]
            update_run(
                task,
                command=["wsl.exe", *supervised],
                remote_root=remote,
                remote_output=source,
                cancellation_command=cancel,
                prepared_at=utc_now(),
            )
            return (supervised, cancel, source)

        self.background(
            prepare,
            self.start_prepared,
            lambda error: self.fail("preparation", error),
            format_message("run.preparing", stage=stage, run_id=task.run_id),
        )

    def start_prepared(self, prepared):
        if self.cancel_requested:
            return self.fail(
                "cancelled", msg("text.cancelled.before.starting.the.external.program")
            )
        args, cancel, self.remote_source = prepared
        w, task = (self.window, self.task)
        self.execution = LoggedProcess(
            task.directory, "wsl.exe", args, cancel_command=cancel, parent=w
        )
        self.execution.output.connect(self.read_output)
        self.execution.completed.connect(self.finished)
        self.execution.cancellation_failed.connect(
            lambda error: self.status(format_message("run.cancel_unconfirmed", reason=error))
        )
        self.execution.persistence_failed.connect(
            lambda error: self.status(format_message("log.failed", reason=error))
        )
        w._process = self.execution.process
        w._active_task_type = task.stage
        update_run(task, status="running", started_at=utc_now(), solver_status="running")
        self.status(format_message("run.running", run_id=task.run_id))
        w._buttons()
        self.execution.start()

    def read_output(self, text, error):
        buffer = self._output_fragments[error] + text
        lines = buffer.split("\n")
        self._output_fragments[error] = lines.pop()
        while len(self._output_fragments[error]) > 16000:
            lines.append(self._output_fragments[error][:16000])
            self._output_fragments[error] = self._output_fragments[error][16000:]
        for line in lines:
            self._output_line(line.rstrip("\r"), error)

    def _output_line(self, line, error):
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            record = None
        if isinstance(record, dict) and record.get("message_data"):
            value = record_message(record)
            self.window._logs.append(value)
            if record.get("event") == "progress":
                ui_call(self.window.bottom_status, "setText", value)
        else:
            self.window._logs.append(("[stderr] " if error else "") + line)

    def _flush_output(self):
        for error, value in self._output_fragments.items():
            if value:
                self._output_line(value, error)
        self._output_fragments = {False: "", True: ""}

    def finished(self, code, reason):
        self._flush_output()
        w, task = (self.window, self.task)
        w._process = None
        w._active_task_type = None
        if reason != "succeeded":
            try:
                details = (task.directory / "stderr.log").read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError as exc:
                details = exception_text(exc)
            if task.stage == "pflotran" and reason != "cancelled":
                from fracrtm.pflotran.run_service import classify_run_failure
                from fracrtm.runtime.resource_limits import log_tail

                try:
                    stdout = log_tail(task.directory / "stdout.log")
                except OSError:
                    stdout = ""
                _, details = classify_run_failure(stdout, details)
            if not details and self.execution:
                details = self.execution.record.get("error", "")
            if reason == "log_failed" and self.execution:
                details += "\n" + join_text(
                    "; ",
                    (str(item) for item in self.execution.record.get("persistence_errors", [])),
                )
                self.preserve_recovery(task, reason, details, code)
            return self.fail(reason, details, code)
        try:
            update_run(task, status="synchronizing", returncode=code, solver_status="succeeded")
        except OSError as exc:
            return self.fail("recording", exception_text(exc), code)

        def finalize():
            if task.stage == "pflotran":
                self.sync.sync_run_outputs(task, self.remote_source)
                record = finish_task(task, returncode=code, synchronized=True)
                if record["status"] != "completed":
                    return False
                time = final_output_time(task, record)
                if time is not None:
                    update_run(
                        task,
                        final_time_seconds=time,
                        final_time_source=msg("text.model.out.output.metadata"),
                    )
                try:
                    from fracrtm.visualization import PFLOTRANResultPostProcessor

                    result = PFLOTRANResultPostProcessor(
                        task.project_directory, run_id=task.run_id
                    ).process()
                    update_run(
                        task,
                        visualization_status="succeeded",
                        visualization_collection=str(result.collection),
                    )
                except Exception as exc:
                    update_run(
                        task, visualization_status="failed", visualization_error=exception_text(exc)
                    )
                return True
            self.sync.sync_stage_run_outputs(task, self.remote_source)
            promoted = self.sync.promote_stage_outputs(task)
            if promoted:
                launch = json.loads(task.record_path.read_text(encoding="utf-8"))["launch_identity"]
                record_stage_generation(task.project_directory, task.stage, launch, task.run_id)
            finish_task(task, returncode=code, synchronized=True)
            update_run(task, promoted_to_current=promoted)
            return promoted

        self.background(
            finalize,
            self.completed,
            lambda error: self.fail("synchronization", error, code),
            format_message("run.syncing", run_id=task.run_id),
        )

    def completed(self, promoted):
        w, task = (self.window, self.task)
        record = json.loads(task.record_path.read_text(encoding="utf-8"))
        self.task = None
        self.execution = None
        self.scan()
        if record["status"] != "completed":
            self.status(
                join_text(
                    "",
                    [
                        task.run_id,
                        ": ",
                        state_text(record["failure_stage"]),
                        ": ",
                        record_message(record, "error"),
                    ],
                )
            )
            w._warn(
                msg("repair.completion_unverified_title")
                if record["status"] == "unverified"
                else msg("text.task.failed"),
                join_text(
                    "", [record_message(record, "error"), msg("text.logs") + ": ", task.directory]
                ),
            )
        elif record.get("visualization_status") == "failed":
            self.status(
                format_message(
                    "run.visualization_failed",
                    run_id=task.run_id,
                    reason=record.get("visualization_error", ""),
                )
            )
        else:
            self.status(
                format_message(
                    "run.completed" if promoted else "run.completed_historical", run_id=task.run_id
                )
            )
        w._rebuild_results_workspace()
        w._refresh()
        if task.stage == "octree" and getattr(w, "_udfm_pipeline_requested", False) and promoted:
            QtCore.QTimer.singleShot(0, lambda: w.run_continuum("upscale"))
        elif task.stage in ("octree", "upscale"):
            w._udfm_pipeline_requested = False

    def fail(self, stage, error, code=-1):
        w, task = (self.window, self.task)
        if task:
            try:
                finish_task(
                    task, returncode=code, synchronized=False, failure_stage=stage, error=str(error)
                )
            except Exception as record_error:
                try:
                    update_run(
                        task,
                        status="failed",
                        returncode=code,
                        synchronized=False,
                        finished_at=utc_now(),
                        failure_stage=stage,
                        error=format_message(
                            "run.manifest_failed", reason=error, detail=record_error
                        ),
                    )
                except Exception as persistence_error:
                    self.preserve_recovery(
                        task, stage, join_text("", [error, "; ", persistence_error]), code
                    )
        self.task = None
        self.execution = None
        w._process = None
        w._active_task_type = None
        w._udfm_pipeline_requested = False
        self.scan()
        if task:
            setattr(
                w.state,
                {
                    "dfn": "dfn_state",
                    "octree": "octree_state",
                    "upscale": "upscale_state",
                    "pflotran": "simulation_state",
                }[task.stage],
                ArtifactState.FAILED,
            )
        self.status(
            format_message(
                "run.failed",
                stage=state_text(stage),
                reason=diagnostic_summary(error or msg("text.task.did.not.complete")),
            )
        )
        try:
            w._refresh()
        except Exception as exc:
            self.status(format_message("project.refresh_failed", reason=exc))
            for name in ("run_button", "udfm_button", "generate_pf", "run_pf", "stop_pf"):
                button = getattr(w, name, None)
                if button is not None:
                    button.setEnabled(False)
        if stage != "cancelled":
            w._warn(
                msg("text.task.failed"),
                format_message(
                    "run.failure_details",
                    stage=stage,
                    reason=error,
                    logs=task.directory if task else "—",
                ),
            )

    def stop(self):
        if self.execution:
            self.status(format_message("run.stopping", run_id=self.task.run_id))
            self.execution.stop()
        elif self.task:
            self.cancel_requested = True
            self.status(
                msg(
                    "text.cancellation.requested.input.preparation.will.finish.without.launching.a.solver"
                )
            )

    def regenerate_visualization(self):
        w = self.window
        if not w._require_project() or self.busy:
            return
        panel = getattr(w, "results_workspace", None)
        run_id = panel.selected_run_id() if panel is not None else None
        project = w.workspace.local_root

        def generate():
            from fracrtm.visualization import PFLOTRANResultPostProcessor

            try:
                result = PFLOTRANResultPostProcessor(project, run_id=run_id).process()
                if run_id:
                    update_run(
                        get_run(project, run_id),
                        visualization_status="succeeded",
                        visualization_collection=str(result.collection),
                    )
                return result
            except Exception as exc:
                if run_id:
                    update_run(
                        get_run(project, run_id),
                        visualization_status="failed",
                        visualization_error=exception_text(exc),
                    )
                raise

        def generated(result):
            w._rebuild_results_workspace()
            w._refresh()
            self.status(
                format_message(
                    "results.regenerated",
                    run_id=run_id or msg("text.legacy.unverified.results"),
                    path=result.collection,
                )
            )

        def failed(error):
            self.status(msg("results.failure_prefix") + str(error))
            w._buttons()
            w._warn(msg("text.visualization.failed"), str(error))

        self.background(
            generate,
            generated,
            failed,
            format_message("run.reading", run_id=run_id or "legacy / unverified"),
        )

    def resync(self, stage, run_id=None):
        w = self.window
        if not w._require_project() or self.busy:
            return
        candidates = [
            r
            for r in list_runs(w.workspace.local_root, stage=stage)
            if r.get("solver_status") == "succeeded"
            and (not r.get("synchronized"))
            and r.get("remote_output")
            and (run_id is None or r["run_id"] == run_id)
        ]
        if not candidates:
            return w._warn(
                msg("text.no.identified.result.to.synchronize"),
                msg(
                    "text.unrecorded.legacy.remote.files.cannot.be.promoted.as.verified.current.results"
                ),
            )
        record = candidates[0]
        try:
            task = self.task = get_run(w.workspace.local_root, record["run_id"])
            self.remote_source = record["remote_output"]
            self.sync = ProjectSyncService(
                w.workspace,
                record["runtime"]["distribution"],
                record["runtime"]["wsl_user"],
                task=task,
            )
            staging = task.directory / "sync_staging"
            if staging.exists():
                staging.rename(
                    task.directory / ("sync_staging_previous_" + utc_now().replace(":", "-"))
                )
            if task.output_directory.exists() and any(task.output_directory.iterdir()):
                task.output_directory.rename(
                    task.directory / ("output_previous_" + utc_now().replace(":", "-"))
                )
                task.output_directory.mkdir(parents=True)
            self.finished(0, "succeeded")
        except Exception as exc:
            self.fail("synchronization", exception_text(exc), 0)

    def choose_resync(self):
        w = self.window
        if not w._require_project() or self.busy:
            return
        rows = [
            r
            for r in list_runs(w.workspace.local_root, stage=None)
            if r.get("solver_status") == "succeeded"
            and (not r.get("synchronized"))
            and r.get("remote_output")
        ]
        if not rows:
            return w._warn(
                msg("text.no.pending.synchronization"),
                msg("text.there.are.no.identified.successful.tasks.awaiting.synchronization"),
            )
        labels = [join_text("", [state_text(r["stage"]), ": ", r["run_id"]]) for r in rows]
        labels = [render_text(label) for label in labels]
        selected, ok = QtWidgets.QInputDialog.getItem(
            w,
            render_text(msg("text.retry.synchronization")),
            render_text(msg("text.recorded.task")),
            labels,
            0,
            False,
        )
        if ok:
            row = rows[labels.index(selected)]
            self.resync(row["stage"], row["run_id"])

    def choose_recovery(self):
        w = self.window
        if not w._require_project() or self.busy:
            return
        rows = [
            r
            for r in list_runs(w.workspace.local_root, stage=None)
            if r.get("cancellation_command")
            and (not (r.get("status") == "completed" and r.get("synchronized")))
        ]
        if not rows:
            return w._warn(
                msg("text.no.interrupted.task"),
                msg("text.no.identified.unfinished.remote.task.requires.recovery"),
            )
        labels = [
            join_text("", [r["run_id"], " (", state_text(run_display_status(r)), ")"]) for r in rows
        ]
        labels = [render_text(label) for label in labels]
        selected, ok = QtWidgets.QInputDialog.getItem(
            w,
            render_text(msg("text.check.interrupted.task")),
            render_text(msg("text.recorded.task")),
            labels,
            0,
            False,
        )
        if ok:
            task = get_run(w.workspace.local_root, rows[labels.index(selected)]["run_id"])
            self.check_remote_task(task)

    def check_remote_task(self, task, cancel=False):
        from fracrtm.project.recovery import cancel_remote_run, inspect_remote_run

        w = self.window
        record = json.loads(task.record_path.read_text(encoding="utf-8"))
        sync = ProjectSyncService(
            w.workspace, record["runtime"]["distribution"], record["runtime"]["wsl_user"], task=task
        )
        action = cancel_remote_run if cancel else inspect_remote_run

        def show(result):
            self.scan()
            w._refresh()
            self.status(
                format_message(
                    "recovery.summary",
                    run_id=task.run_id,
                    state=state_text(result.state),
                    reason=result.message,
                )
            )
            box = MessageBox(w)
            ui_call(box, "setWindowTitle", msg("text.task.recovery"))
            ui_call(
                box,
                "setText",
                format_message(
                    "recovery.summary",
                    run_id=task.run_id,
                    state=state_text(result.state),
                    reason=diagnostic_summary(result.message),
                ),
            )
            box.setDetailedText(
                str(result.message)
                + "\n\n"
                + json.dumps(prepare_json(result.details), ensure_ascii=False, indent=2)
            )
            box.setStandardButtons(QtWidgets.QMessageBox.Close)
            synchronize = (
                ui_call(
                    box,
                    "addButton",
                    msg("text.synchronize.this.run"),
                    QtWidgets.QMessageBox.ActionRole,
                )
                if result.can_resync
                else None
            )
            stop = (
                ui_call(
                    box,
                    "addButton",
                    msg("text.stop.this.task"),
                    QtWidgets.QMessageBox.DestructiveRole,
                )
                if result.can_cancel
                else None
            )
            box.exec_()
            if synchronize is not None and box.clickedButton() == synchronize:
                self.resync(task.stage, task.run_id)
            elif stop is not None and box.clickedButton() == stop:
                self.check_remote_task(task, cancel=True)

        def failed(error):
            self.status(format_message("recovery.failed", reason=error))
            w._buttons()
            w._warn(msg("text.task.recovery.failed"), str(error))

        self.background(
            lambda: action(task, sync),
            show,
            failed,
            format_message("run.stop_verified" if cancel else "run.checking", run_id=task.run_id),
        )

    def show_recent_log(self):
        from .qt_helpers import RunLogDialog

        task = getattr(self.window, "_recent_task", None)
        if task is None:
            return
        if not any(((task.directory / name).is_file() for name in ("stdout.log", "stderr.log"))):
            return self.window._warn(
                msg("text.view.log"), msg("text.the.logs.for.this.run.are.unavailable")
            )
        sections = []
        for name in ("run.json", "stdout.log", "stderr.log", "execution.json"):
            path = task.directory / name
            if path.is_file():
                sections.append(join_text("", [path, "\n", log_tail(path)]))
        RunLogDialog(join_text("\n\n", sections), self.window).exec_()

    def open_recent_results(self):
        from PyQt5.QtGui import QDesktopServices

        task = getattr(self.window, "_recent_task", None)
        if task is None:
            return
        if not task.output_directory.is_dir():
            return self.window._warn(
                msg("text.open.results"),
                msg("text.the.results.directory.for.this.run.is.unavailable"),
            )
        if not QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(task.output_directory))):
            self.window._warn(
                msg("text.open.results"),
                msg("text.the.results.directory.for.this.run.is.unavailable"),
            )

    def show_task_log(self):
        from .qt_helpers import RunLogDialog

        w = self.window
        rows = list_runs(w.workspace.local_root, stage=None) if w.workspace else []
        if not rows:
            return RunLogDialog(join_text("\n", w._logs), w).exec_()
        labels = [
            join_text("", [r["run_id"], " (", state_text(run_display_status(r)), ")"]) for r in rows
        ]
        labels = [render_text(label) for label in labels]
        chosen, ok = QtWidgets.QInputDialog.getItem(
            w,
            render_text(msg("text.task.logs")),
            render_text(msg("text.recorded.task")),
            labels,
            0,
            False,
        )
        if not ok:
            return
        task = get_run(w.workspace.local_root, rows[labels.index(chosen)]["run_id"])
        sections = []
        for name in (
            "run.json",
            "execution.json",
            "stdout.log",
            "stderr.log",
            "sync_commands.jsonl",
        ):
            path = task.directory / name
            if path.is_file():
                sections.append(join_text("", [path, "\n", log_tail(path)]))
        RunLogDialog(join_text("\n\n", sections), w).exec_()
