from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

from fracrtm.messages import exception_text, join_text
from fracrtm.messages import message as msg
from fracrtm.pflotran.run_service import probe_available_processors
from fracrtm.project import ArtifactState, ProjectState, ProjectSyncService, ProjectWorkspace
from fracrtm.project.runs import get_run, list_runs
from fracrtm.project.simulation_mesh import DEFAULT_MODEL_SOURCE
from fracrtm.runtime import RuntimeSettings
from fracrtm.runtime.settings import settings_store
from fracrtm.ui.dialogs import DfnGenerationDialog, PFLOTRANConfigDialog
from fracrtm.ui.localization import (
    SUPPORTED_LOCALES,
    UiLanguageController,
    configure_startup_locale,
    format_message,
    render_text,
    set_locale,
    ui_call,
)
from fracrtm.ui.qt_helpers import MessageBox
from fracrtm.ui.results_viewer import ResultsVisualizationWorkspace
from fracrtm.ui.styles import APP_STYLE
from fracrtm.ui.workflow_controller import WorkflowController

from .main_window_pages import MainWindowPages
from .qt_helpers import BackgroundCall, RunLogDialog
from .workflow_controller import final_output_time


class MainWindow(MainWindowPages, QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        ui_call(self, "setWindowTitle", "FracRTM-GUI")
        self.resize(1280, 820)
        self.setStyleSheet(APP_STYLE)
        self._repo_root = Path(__file__).resolve().parents[2]
        self.settings = RuntimeSettings.load()
        self.workspace = None
        self.current_project = None
        self.model_source = DEFAULT_MODEL_SOURCE
        from fracrtm.runtime.resource_limits import BoundedLog

        self._logs = BoundedLog()
        self._project_lease = None
        self.sync = None
        self.state = ProjectState()
        self._process = None
        self._active_task_type = None
        self._workers = set()
        self._available_processors = None
        self._processor_probe_running = False
        self._workflow_controller = WorkflowController(self)
        self._load_project_models()
        self._build_ui()
        self._language_controller = UiLanguageController(self).install()
        self._scan_states()
        self._refresh()
        self._language_controller.retranslate()

    def _new_sync_service(self):
        if self.workspace is None:
            return None
        return ProjectSyncService(
            self.workspace,
            self.settings.distribution,
            self.settings.wsl_user,
            warning=lambda message: self._logs.append(msg("text.wsl.warning") + message),
        )

    def _save_settings(self):
        try:
            self.settings = RuntimeSettings(
                distribution=self.wsl_distribution.text(),
                wsl_user=self.wsl_user.text(),
                dfn_python=self.wsl_python.text(),
                dfnworks_home=self.dfn_home.text(),
                pflotran_executable=self.pf_executable.text(),
                mpi_launcher=self.mpi_launcher.text(),
                wsl_workspace_root=self.workspace_root.text(),
            )
        except ValueError as exc:
            return self._warn(msg("validation.invalid.runtime.settings"), exception_text(exc))
        self.settings.save()
        if self.workspace is not None:
            from dataclasses import replace

            self.workspace = replace(
                self.workspace, wsl_workspace_root=self.settings.wsl_workspace_root.rstrip("/")
            )
            self.current_project = self.workspace
        self.sync = self._new_sync_service()
        self._available_processors = None
        if hasattr(self, "available_processors_label"):
            ui_call(self.available_processors_label, "setText", msg("text.not.checked"))
        self._status(msg("text.runtime.settings.saved"))

    def _choose_project(self):
        initial_directory = (
            self.workspace.local_root
            if self.workspace is not None
            else self._repo_root / "projects"
        )
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            render_text(msg("text.choose.project.directory")),
            str(initial_directory),
            options=QtWidgets.QFileDialog.DontUseNativeDialog,
        )
        if directory:
            self._activate_project(directory)

    def _require_project(self):
        if self.workspace is not None:
            return True
        self._warn(
            msg("text.no.project.open"),
            msg("text.create.or.open.a.project.before.generating.or.running.a.model"),
        )
        return False

    def _rebuild_results_workspace(self):
        if self.workspace is None:
            return
        placeholder = getattr(self, "_no_project_visual_message", None)
        if placeholder is not None:
            self.visual_layout.removeWidget(placeholder)
            placeholder.deleteLater()
            self._no_project_visual_message = None
        old = getattr(self, "results_workspace", None)
        if old:
            self.visual_layout.removeWidget(old)
            old.canvas.close()
            old.deleteLater()
        try:
            self.results_workspace = ResultsVisualizationWorkspace(
                self.workspace.local_root, self._pflotran_config, self
            )
            self.visual_layout.addWidget(self.results_workspace, 1)
        except Exception as exc:
            self.results_workspace = None
            self._logs.append(format_message("results.init_failed", reason=exc))

    def _reload_visualization_artifacts(self):
        workspace = getattr(self, "results_workspace", None)
        if workspace is None:
            return
        try:
            workspace.reload_artifacts(self._pflotran_config)
        except Exception as exc:
            self._logs.append(format_message("results.refresh_failed", reason=exc))

    def open_dfn_dialog(self):
        dialog = DfnGenerationDialog(self._dfn_config, self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self._dfn_config = dialog.get_config()
            if self.workspace is not None:
                self._dfn_config.save_json(self.workspace.local_dfn_dir / "dfn_config.json")
                self.state.invalidate_from_dfn()
            self._refresh()

    def open_pflotran_dialog(self):
        if self.workspace is None:
            self._warn(
                msg("text.pflotran.is.not.configured"),
                msg("text.create.or.open.a.project.before.configuring.pflotran"),
            )
            return
        project_directory = self.workspace.local_root if self.workspace is not None else None
        dialog = PFLOTRANConfigDialog(
            self._pflotran_config,
            self,
            project_directory=project_directory,
            model_source=self.model_source,
        )
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            config = dialog.get_config()
            try:
                dialog.commit_project_edit(config)
            except (OSError, ValueError, RuntimeError) as exc:
                self._warn(msg("text.cannot.export"), exception_text(exc))
                return
            self._pflotran_config = config
            if self.workspace is not None:
                self.state.invalidate_from_pflotran_config()
                self.state.pflotran_config_state = (
                    ArtifactState.VALID
                    if self._pflotran_config.validate(self.workspace.local_root).valid
                    else ArtifactState.FAILED
                )
                self._rebuild_results_workspace()
            self._refresh()

    def _refresh_available_processors(self, start_after=False):
        if self._processor_probe_running:
            return
        self._processor_probe_running = True
        ui_call(self.available_processors_label, "setText", msg("text.checking"))
        worker = BackgroundCall(lambda: probe_available_processors(self.settings))
        self._workers.add(worker)

        def succeeded(value, w=worker):
            self._workers.discard(w)
            self._processor_probe_running = False
            self._available_processors = value
            ui_call(self.available_processors_label, "setText", str(value))
            self._logs.append(format_message("runtime.processors", count=value))
            self._buttons()
            if start_after:
                QtCore.QTimer.singleShot(0, self.run_pflotran)

        def failed(message, w=worker):
            self._workers.discard(w)
            self._processor_probe_running = False
            ui_call(self.available_processors_label, "setText", msg("text.check.failed"))
            self._buttons()
            self._warn(msg("text.unable.to.detect.wsl.resources"), message)

        worker.signals.succeeded.connect(succeeded)
        worker.signals.failed.connect(failed)
        QtCore.QThreadPool.globalInstance().start(worker)
        self._buttons()

    def open_visualization(self, kind):
        self._switch(4)
        if not self.results_workspace:
            return
        tab = 0 if kind == "dfn" else 2 if kind == "pflotran" else 1
        self.results_workspace.tabs.setCurrentIndex(tab)
        data = {"octree": "mesh", "properties": "permeability", "materials": "material"}.get(kind)
        if data:
            index = self.results_workspace.data.findData(data)
            if index >= 0:
                self.results_workspace.data.setCurrentIndex(index)

    def _show_resource_details(self):
        mesh = self.model_source.simulation_mesh(self.workspace.local_root)
        lines = []
        for label, path in (
            (msg("viewer.computational_mesh"), mesh.grid),
            (msg("text.display.geometry"), mesh.visualization_geometry),
            (msg("viewer.material_regions"), mesh.materials),
            (msg("viewer.permeability"), mesh.permeability),
            (msg("viewer.porosity"), mesh.porosity),
        ):
            lines.append(
                join_text(
                    "",
                    [
                        label,
                        ": ",
                        path.relative_to(self.workspace.local_root).as_posix() if path else "—",
                    ],
                )
            )
        lines.extend(
            (
                format_message("resources.boundary", name=name, path=item.relative_path)
                for name, item in mesh.boundaries.items()
            )
        )
        RunLogDialog(join_text("\n", lines), self, translate_content=True).exec_()

    @staticmethod
    def _mark(label, valid):
        return (msg("text.ready.5") if valid else msg("text.not.generated.4")) + label

    def _warn(self, title, text):
        MessageBox.warning(self, title, text)

    def _load_project(self, directory):
        from fracrtm.project.storage import ProjectLock, recover_project

        same_project = (
            self.workspace is not None and self.workspace.local_root == Path(directory).resolve()
        )
        lease = (
            self._project_lease
            if same_project and self._project_lease is not None
            else ProjectLock(directory, "session").acquire()
        )
        try:
            recover_project(directory)
            workspace = ProjectWorkspace.open(directory, self.settings.wsl_workspace_root)
        except BaseException:
            if lease is not self._project_lease:
                lease.release()
            raise
        if self._project_lease is not None and self._project_lease is not lease:
            self._project_lease.release()
        self._project_lease = lease
        self.destroyed.connect(lambda _obj=None, held=lease: held.release())
        self.workspace = workspace
        self.current_project = self.workspace
        self.sync = self._new_sync_service()
        self._load_project_models()
        widgets = (self.base_cell, self.refinement, self.matrix_perm, self.matrix_porosity)
        blockers = [QtCore.QSignalBlocker(widget) for widget in widgets]
        self.base_cell.setSpecialValueText(
            "\u200b" if self._continuum_config.base_cell_size is None else ""
        )
        self.base_cell.setValue(
            self.base_cell.minimum()
            if self._continuum_config.base_cell_size is None
            else self._continuum_config.base_cell_size
        )
        self.refinement.setSpecialValueText(
            "\u200b" if self._continuum_config.refinement_levels is None else ""
        )
        self.refinement.setValue(
            self.refinement.minimum()
            if self._continuum_config.refinement_levels is None
            else self._continuum_config.refinement_levels
        )
        ui_call(
            self.matrix_perm,
            "setText",
            ""
            if self._continuum_config.matrix_permeability is None
            else f"{self._continuum_config.matrix_permeability:g}",
        )
        self.matrix_porosity.setSpecialValueText(
            "\u200b" if self._continuum_config.matrix_porosity is None else ""
        )
        self.matrix_porosity.setValue(
            self.matrix_porosity.minimum()
            if self._continuum_config.matrix_porosity is None
            else self._continuum_config.matrix_porosity
        )
        del blockers
        self._load_boundary_area_controls()
        self._scan_states()
        self._rebuild_results_workspace()
        self._refresh()

    def _load_boundary_area_controls(self):
        blockers = [
            QtCore.QSignalBlocker(w) for w in (self.boundary_area_mode, self.boundary_area_value)
        ]
        config = self._continuum_config
        self.boundary_area_mode.setCurrentIndex(
            self.boundary_area_mode.findData(config.boundary_area_mode)
        )
        self.boundary_area_value.setText(
            "" if config.boundary_cell_area is None else repr(config.boundary_cell_area)
        )
        self.boundary_area_value.setEnabled(config.boundary_area_mode == "custom")
        del blockers

    def _boundary_area_changed(self, *args):
        def change():
            config = self._continuum_config
            before = config.boundary_identity()
            config.boundary_area_mode = self.boundary_area_mode.currentData()
            if self.sender() is self.boundary_area_value:
                try:
                    config.boundary_cell_area = float(self.boundary_area_value.text())
                except ValueError:
                    config.boundary_cell_area = None
            self.boundary_area_value.setEnabled(config.boundary_area_mode == "custom")
            if config.boundary_identity() != before:
                self.state.invalidate_from_octree()
                self._workflow().changed("octree")

        self._run_action(change)

    def _refresh_boundary_area_record(self):
        text = msg("boundary_area.unknown")
        if self.workspace is not None:
            path = self.workspace.local_octree_dir / "boundary_conversion.json"
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                stats = record.get("written_area_statistics", {})
                values = {s["min"] for s in stats.values() if s.get("count", 0)}
                if (
                    record.get("parameter_source") == "tool_default"
                    and len(values) == 1
                    and all(s.get("count", 0) == 0 or s["min"] == s["max"] for s in stats.values())
                ):
                    text = msg("boundary_area.previous", value=repr(next(iter(values))))
                else:
                    from fracrtm.config.contracts import positive

                    default = record.get("default_boundary_cell_area")
                    if positive(default) and record.get("source_sha256"):
                        text = msg("boundary_area.previous_signature", value=repr(default))
            except (OSError, ValueError, KeyError, TypeError):
                pass
        ui_call(self.boundary_area_default, "setText", text)

    def _update_octree_parameters(self, *args):
        sender = self.sender()
        if sender is self.base_cell:
            self._continuum_config.base_cell_size = self.base_cell.value()
            self.base_cell.setSpecialValueText("")
        elif sender is self.refinement:
            self._continuum_config.refinement_levels = self.refinement.value()
            self.refinement.setSpecialValueText("")
        self.state.invalidate_from_octree()
        self._refresh()

    def _update_upscale_parameters(self, *args):
        sender = self.sender()
        if sender is self.matrix_perm:
            try:
                self._continuum_config.matrix_permeability = float(self.matrix_perm.text())
            except ValueError:
                self._continuum_config.matrix_permeability = None
        elif sender is self.matrix_porosity:
            self._continuum_config.matrix_porosity = self.matrix_porosity.value()
            self.matrix_porosity.setSpecialValueText("")
        self.state.invalidate_from_upscale()
        self._refresh()

    def _close_viewers(self, event):
        if getattr(self, "results_workspace", None):
            self.results_workspace.canvas.close()
        QtWidgets.QMainWindow.closeEvent(self, event)

    def _change_ui_language(self, index):
        locale = self.language_combo.itemData(index)
        if locale not in SUPPORTED_LOCALES:
            return
        settings_store().setValue("display_language", locale)
        set_locale(locale)
        if self.results_workspace is not None:
            self.results_workspace.refresh_language()
        if hasattr(self, "_language_controller"):
            self._language_controller.retranslate()
        self._refresh()
        if hasattr(self, "_language_controller"):
            self._language_controller.retranslate()

    def _test_runtime_environment(self):
        if getattr(self, "_environment_probe_running", False):
            return
        distribution = self.wsl_distribution.text().strip()
        user = self.wsl_user.text().strip()
        python = self.wsl_python.text().strip().strip("\"'")
        dfn_home = self.dfn_home.text().strip().strip("\"'")
        pflotran = self.pf_executable.text().strip().strip("\"'")
        mpi = self.mpi_launcher.text().strip().strip("\"'")
        if not all((distribution, user, python, dfn_home, pflotran, mpi)):
            ui_call(
                self.environment_status,
                "setText",
                msg("text.complete.the.wsl.dfnworks.pflotran.and.mpi.paths.first"),
            )
            return
        self._environment_probe_running = True
        ui_call(self.environment_status, "setText", msg("text.checking.runtime.environment"))

        def run_probe(command):
            return subprocess.run(command, capture_output=True, text=True, timeout=20)

        def probe():
            prefix = ["wsl.exe", "-d", distribution, "-u", user, "--"]
            checks = (
                (
                    "Python / pydfnWorks",
                    prefix
                    + [
                        "env",
                        join_text("", ["HOME=", dfn_home]),
                        python,
                        "-c",
                        "import pydfnworks; print('OK')",
                    ],
                ),
                (msg("text.dfnworks.home"), prefix + ["test", "-d", dfn_home]),
                ("PFLOTRAN", prefix + ["test", "-x", pflotran]),
                (msg("text.mpi.launcher"), prefix + ["which", mpi]),
            )
            failed = []
            warnings = []
            for label, command in checks:
                result = run_probe(command)
                if result.returncode != 0:
                    failed.append(
                        join_text(
                            "",
                            [
                                label,
                                ": ",
                                (result.stderr or result.stdout).strip() or msg("text.unavailable"),
                            ],
                        )
                    )
                elif result.stderr.strip():
                    warnings.append(join_text("", [label, ": ", result.stderr.strip()]))
            return (failed, warnings)

        worker = BackgroundCall(probe)
        self._workers.add(worker)

        def done(result):
            self._workers.discard(worker)
            self._environment_probe_running = False
            failures, warnings = result
            if failures:
                self._logs.extend(failures)
                ui_call(
                    self.environment_status,
                    "setText",
                    msg("text.environment.check.failed.see.the.log.for.details"),
                )
            else:
                ui_call(
                    self.environment_status,
                    "setText",
                    msg("text.environment.available") + (msg("text.warnings") if warnings else ""),
                )
            self._buttons()

        def failed(message):
            self._workers.discard(worker)
            self._environment_probe_running = False
            ui_call(
                self.environment_status,
                "setText",
                format_message("runtime.probe_failed", reason=message),
            )
            self._buttons()

        worker.signals.succeeded.connect(done)
        worker.signals.failed.connect(failed)
        QtCore.QThreadPool.globalInstance().start(worker)

    def _open_current_project_directory(self):
        if self.workspace is None:
            self._require_project()
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.workspace.local_root)))

    def _workflow(self):
        return self._workflow_controller

    def _run_action(self, action, *args):
        try:
            return action(*args)
        except Exception as exc:
            return self._workflow().fail("interface", exception_text(exc))

    def _run_udfm(self):
        self._workflow().scan()
        self._udfm_pipeline_requested = True
        stage = "upscale" if self.state.octree_state == ArtifactState.VALID else "octree"
        self._run_action(self._workflow().run_continuum, stage)

    def _buttons(self):
        self._update_page_buttons()
        workflow = self._workflow()
        active = workflow.task is not None and (
            workflow.execution is not None or not workflow.remote_source
        )
        self.stop_pf.setVisible(active)
        self.stop_pf.setEnabled(active)
        ui_call(self.stop_pf, "setText", msg("text.stop.current.task"))
        self.check_pf.setEnabled(self.workspace is not None and (not workflow.busy))
        if hasattr(self, "retry_sync_button"):
            self.retry_sync_button.setEnabled(self.workspace is not None and (not workflow.busy))
        if hasattr(self, "recovery_button"):
            self.recovery_button.setEnabled(self.workspace is not None and (not workflow.busy))
        if hasattr(self, "regenerate_visualization_button"):
            self.regenerate_visualization_button.setEnabled(
                self.workspace is not None and (not workflow.busy)
            )
        if self.workspace and list_runs(self.workspace.local_root, stage=None):
            self.run_log_button.setEnabled(True)

    def _octree_changed(self, *args):

        def change():
            self._update_octree_parameters(*args)
            self._workflow().changed("octree")

        self._run_action(change)

    def _upscale_changed(self, *args):

        def change():
            self._update_upscale_parameters(*args)
            self._workflow().changed("upscale")

        self._run_action(change)

    def _activate_project(self, directory):
        if self._workflow().busy:
            return self._warn(
                msg("text.task.in.progress"),
                msg("text.wait.for.completion.or.stop.this.task.before.switching.project"),
            )
        try:
            self.recent_details_button.setChecked(False)
            self._load_project(directory)
            log = self.workspace.local_logs_dir / "gui.log"
            from fracrtm.runtime.resource_limits import BoundedLog, log_tail

            self._logs = BoundedLog(log_tail(log).splitlines() if log.is_file() else [])
            self._workflow().status(
                format_message("project.opened", name=self.workspace.display_name)
            )
            self._buttons()
        except Exception as exc:
            self._workflow().status(format_message("project.open_failed", reason=exc))
            self._warn(msg("text.project.open.failed"), exception_text(exc))

    def _refresh(self):
        self._refresh_boundary_area_record()
        for name in (
            "project_name",
            "project_path",
            "recent_results_path",
            "recent_run_id",
            "run_input_file",
            "octree_location",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setProperty("fracrtm_user_content", True)
        self._refresh_page_state()
        if self.workspace is not None:
            ui_call(self.project_name, "setText", self.workspace.display_name)
            if self._available_processors is not None:
                ui_call(
                    self.run_available_processors_label, "setText", str(self._available_processors)
                )
            simulation = self._pflotran_config.simulation
            if not simulation.uses_transport():
                ui_call(self.pf_reaction_summary, "setText", msg("status.not_enabled"))
                ui_call(self.pf_chemistry_summary, "setText", msg("status.not_enabled"))
            rows = list_runs(self.workspace.local_root)
            if rows:
                record = rows[0]
                task = get_run(self.workspace.local_root, record["run_id"])
                from fracrtm.project.runs import run_display_status, run_matches_current

                match = run_matches_current(task)
                from .run_summary import format_duration, format_simulation_time, summary_status

                self._recent_task = task
                ui_call(self.recent_run_id, "setText", task.run_id)
                ui_call(
                    self.recent_status, "setText", summary_status(run_display_status(record), match)
                )
                ui_call(self.recent_status, "setToolTip", "")
                self.recent_log_button.setEnabled(
                    any(((task.directory / n).is_file() for n in ("stdout.log", "stderr.log")))
                )
                self.recent_results_button.setEnabled(task.output_directory.is_dir())
                self.recent_details_button.setEnabled(True)
                self.recent_status.setMaximumWidth(16777215)
                self.recent_status.setSizePolicy(
                    QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred
                )
                self.recent_status.setWordWrap(True)
                duration = record.get("duration_seconds")
                elapsed = "—" if duration is None else join_text("", [f"{duration:.3f}", " s"])
                ui_call(self.recent_runtime, "setText", format_duration(duration))
                ui_call(self.recent_finished_at, "setText", record.get("finished_at") or "—")
                ui_call(self.run_runtime, "setText", elapsed)
                ui_call(
                    self.recent_results_path,
                    "setText",
                    str(task.output_directory.relative_to(self.workspace.local_root)),
                )
                self.recent_results_path.setWordWrap(True)
                ui_call(self.recent_results_path, "setToolTip", str(task.output_directory))
                ui_call(
                    self.run_input_file,
                    "setText",
                    str(
                        (task.snapshot_directory / "pflotran/model.in").relative_to(
                            self.workspace.local_root
                        )
                    ),
                )
                ui_call(
                    self.run_input_file,
                    "setToolTip",
                    str(task.snapshot_directory / "pflotran/model.in"),
                )
                self.run_input_file.setWordWrap(True)
                time = final_output_time(task, record)
                final_time = (
                    msg("text.time.unavailable")
                    if time is None
                    else join_text("", [f"{time:g}", " s"])
                )
                ui_call(self.run_final_time, "setText", final_time)
                try:
                    frozen = json.loads(
                        (task.snapshot_directory / "pflotran/model_config.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    unit = frozen.get("time", {}).get("final_unit", "s")
                except (OSError, ValueError):
                    unit = "s"
                ui_call(self.recent_final_time, "setText", format_simulation_time(time, unit))
            else:
                from .run_summary import summary_status

                self._recent_task = None
                ui_call(self.recent_run_id, "setText", "—")
                self.recent_log_button.setEnabled(False)
                self.recent_results_button.setEnabled(False)
                self.recent_details_button.setChecked(False)
                self.recent_details_button.setEnabled(False)
                ui_call(self.recent_status, "setText", summary_status(None, False))
                ui_call(self.recent_results_path, "setText", "—")
                ui_call(self.recent_runtime, "setText", "—")
                ui_call(self.recent_finished_at, "setText", "—")
                ui_call(self.run_runtime, "setText", "—")
                ui_call(self.recent_final_time, "setText", "—")
                ui_call(self.run_final_time, "setText", msg("text.time.unavailable"))
                candidate = self.workspace.local_pflotran_dir / "model.in"
                ui_call(
                    self.run_input_file, "setText", str(candidate) if candidate.is_file() else "—"
                )
        if hasattr(self, "retry_sync_button"):
            self.retry_sync_button.setEnabled(
                self.workspace is not None and (not self._workflow().busy)
            )
        if hasattr(self, "recovery_button"):
            self.recovery_button.setEnabled(
                self.workspace is not None and (not self._workflow().busy)
            )
        if hasattr(self, "regenerate_visualization_button"):
            self.regenerate_visualization_button.setEnabled(
                self.workspace is not None and (not self._workflow().busy)
            )
        if hasattr(self, "_language_controller"):
            self._language_controller.retranslate()

    def closeEvent(self, event):
        if self._workflow().busy:
            event.ignore()
            self._workflow().stop()
            self._workflow().status(
                msg("text.waiting.for.task.termination.close.again.after.it.finishes")
            )
        else:
            self._close_viewers(event)
            if self._project_lease is not None:
                self._project_lease.release()
                self._project_lease = None

    def _load_project_models(self):
        return self._workflow().load_models()

    def _scan_states(self):
        return self._workflow().scan()

    def _status(self, text):
        return self._workflow().status(text)

    def run_dfn(self):
        return self._run_action(self._workflow().run_dfn)

    def run_continuum(self, stage):
        return self._run_action(self._workflow().run_continuum, stage)

    def prepare_pflotran_model(self):
        return self._run_action(self._workflow().prepare_input)

    def _check_pflotran(self):
        return self._run_action(self._workflow().check_model)

    def run_pflotran(self):
        return self._run_action(self._workflow().run_pflotran)

    def _stop_pflotran(self):
        return self._run_action(self._workflow().stop)

    def resync_results(self, stage):
        return self._run_action(self._workflow().resync, stage)

    def regenerate_pflotran_visualization(self):
        return self._run_action(self._workflow().regenerate_visualization)


def main():
    import sys

    try:
        argv = configure_startup_locale(sys.argv)
    except ValueError as exc:
        raise SystemExit(exception_text(exc)) from exc
    app = QtWidgets.QApplication(argv)
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
