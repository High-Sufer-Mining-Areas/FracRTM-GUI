from __future__ import annotations

from PyQt5 import QtCore, QtWidgets, sip

from fracrtm.messages import exception_text, join_text, state_text
from fracrtm.messages import message as msg
from fracrtm.project.runs import run_display_status
from fracrtm.ui.localization import (
    diagnostic_summary,
    format_message,
    tr,
    ui_call,
    ui_construct,
)
from fracrtm.ui.qt_helpers import BackgroundCall
from fracrtm.visualization import ProjectVisualizationRepository, VisualizationEngine

REPRESENTATIONS = (
    ("representation.surface", "surface"),
    ("representation.surface_edges", "surface_edges"),
    ("representation.wireframe", "wireframe"),
    ("representation.points", "points"),
)


def _add_representations(combo):
    for key, value in REPRESENTATIONS:
        ui_call(combo, "addItem", tr(key), value)


def _viewer_packages():
    try:
        from pyvistaqt import QtInteractor

        return QtInteractor
    except ImportError as exc:
        raise RuntimeError(msg("text.3d.visualization.requires.pyvistaqt.and.vtk")) from exc


def prepare_viewer_packages():
    """Import heavy libraries in a worker; create widgets only on the GUI thread."""
    import pyvista  # noqa: F401
    from scipy.spatial import cKDTree  # noqa: F401

    _viewer_packages()


class VisualizationCanvas(QtWidgets.QWidget):
    failed = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.plotter = None
        self.engine = None
        if QtWidgets.QApplication.platformName().casefold() in {
            "offscreen",
            "minimal",
            "minimalegl",
        }:
            message = ui_construct(QtWidgets.QLabel, tr("viewer.desktop_required"))
            message.setAlignment(QtCore.Qt.AlignCenter)
            layout.addWidget(message, 1)
            return
        try:
            interactor = _viewer_packages()
            self.plotter = interactor(self)
            self.plotter.set_background("#F4F6F7")
            layout.addWidget(self.plotter.interactor, 1)
            self.engine = VisualizationEngine(self.plotter)
        except Exception as exc:
            message = ui_construct(
                QtWidgets.QLabel,
                join_text("", [tr("viewer.load_failed"), ":\n", diagnostic_summary(exc)]),
            )
            message.setAlignment(QtCore.Qt.AlignCenter)
            message.setWordWrap(True)
            layout.addWidget(message, 1)
            self.failed.emit(exception_text(exc))

    def close(self):
        if self.plotter is not None:
            self.plotter.close()


class ResultsVisualizationWorkspace(QtWidgets.QWidget):
    TAB_KEYS = ("structure", "mesh", "results")

    def __init__(self, project_directory, config=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.repository = ProjectVisualizationRepository(project_directory)
        self._dataset = None
        self._load_sequence = 0
        self._loading = False
        self._load_worker = None
        self._disposed = False
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.tabs = QtWidgets.QTabBar()
        for key in ("results.structure", "results.mesh_properties", "results.simulation"):
            ui_call(self.tabs, "addTab", tr(key))
        root.addWidget(self.tabs)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        root.addWidget(split, 1)
        controls = QtWidgets.QFrame()
        controls.setObjectName("Card")
        controls.setMinimumWidth(340)
        controls.setMaximumWidth(460)
        form = QtWidgets.QFormLayout(controls)
        form.setContentsMargins(16, 18, 16, 18)
        form.setVerticalSpacing(13)
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        self.data = QtWidgets.QComboBox()
        self.property = QtWidgets.QComboBox()
        self.time = QtWidgets.QComboBox()
        self.representation = QtWidgets.QComboBox()
        self.opacity = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.opacity.setRange(5, 100)
        self.opacity.setValue(100)
        self.legend = ui_construct(QtWidgets.QCheckBox, tr("viewer.show"))
        self.legend.setChecked(True)
        self.log_scale = ui_construct(QtWidgets.QCheckBox, tr("viewer.use_log_scale"))
        self.run = QtWidgets.QComboBox()
        self.run.setObjectName("resultRunSelector")
        self.run_label = ui_construct(QtWidgets.QLabel, msg("text.run.record"))
        ui_call(form, "addRow", self.run_label, self.run)
        self.run.setMinimumContentsLength(22)
        self.run.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.refresh_run_labels()
        _add_representations(self.representation)
        ui_call(form, "addRow", tr("viewer.data"), self.data)
        self.property_label = ui_construct(QtWidgets.QLabel, tr("viewer.property"))
        ui_call(form, "addRow", self.property_label, self.property)
        self.time_label = ui_construct(QtWidgets.QLabel, tr("viewer.time"))
        ui_call(form, "addRow", self.time_label, self.time)
        ui_call(form, "addRow", tr("viewer.representation"), self.representation)
        ui_call(form, "addRow", tr("viewer.opacity"), self.opacity)
        ui_call(form, "addRow", tr("viewer.legend"), self.legend)
        ui_call(form, "addRow", "", self.log_scale)
        reset = ui_construct(QtWidgets.QPushButton, tr("viewer.reset"))
        ui_call(form, "addRow", reset)
        ui_call(
            form,
            "addItem",
            QtWidgets.QSpacerItem(
                1, 1, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding
            ),
        )
        for combo in (self.data, self.property, self.time, self.representation):
            combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(18)
        self.canvas = VisualizationCanvas()
        split.addWidget(controls)
        split.addWidget(self.canvas)
        split.setStretchFactor(1, 1)
        split.setSizes([360, 900])
        self.message = ui_construct(QtWidgets.QLabel)
        self.message.setObjectName("MutedLabel")
        self.message.setWordWrap(True)
        self.message.hide()
        root.addWidget(self.message)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.data.currentIndexChanged.connect(self._data_changed)
        self.property.currentIndexChanged.connect(self._render)
        self.time.currentIndexChanged.connect(self._render)
        self.representation.currentIndexChanged.connect(self._render)
        self._opacity_render_timer = QtCore.QTimer(self)
        self._opacity_render_timer.setSingleShot(True)
        self._opacity_render_timer.setInterval(50)
        self._opacity_render_timer.timeout.connect(self._render)
        self.opacity.valueChanged.connect(lambda _: self._opacity_render_timer.start())
        self.legend.toggled.connect(self._render)
        self.log_scale.toggled.connect(self._render)
        reset.clicked.connect(self._reset)
        self.run.currentIndexChanged.connect(self._data_changed)
        self._tab_changed(0)

    def refresh_language(self):
        self.refresh_run_labels()
        self._render()

    def refresh_run_labels(self):
        from fracrtm.project.runs import get_run, list_runs, run_matches_current

        selected = self.run.currentData()
        self.run.blockSignals(True)
        self.run.clear()
        for record in list_runs(self.repository.root):
            run_id = record.get("run_id")
            if not run_id:
                continue
            try:
                matches = run_matches_current(get_run(self.repository.root, run_id))
                relation = (
                    msg("text.matches.current.configuration")
                    if matches
                    else msg("text.historical.configuration")
                )
            except (ValueError, OSError, KeyError):
                relation = msg("text.legacy.results.provenance.unverified")
            status = {
                "prepared": msg("text.preparing"),
                "running": msg("text.running"),
                "completed": msg("text.completed"),
                "failed": msg("text.failed"),
                "cancelled": msg("text.cancelled"),
                "synchronizing": msg("text.synchronizing"),
                "unknown": msg("text.unknown"),
            }.get(run_display_status(record), run_display_status(record) or msg("text.unknown"))
            label = join_text("", [run_id[:18], " · ", state_text(status), " · ", relation])
            ui_call(self.run, "addItem", label, run_id)
            self.run.setItemData(
                self.run.count() - 1,
                join_text("", [run_id, "\n", record.get("created_at", ""), "\n", relation]),
                QtCore.Qt.ToolTipRole,
            )
        if not self.run.count():
            ui_call(self.run, "addItem", msg("text.no.run.records"), None)
        previous = self.run.findData(selected)
        if previous >= 0 and selected is not None:
            self.run.setCurrentIndex(previous)
        self.run.blockSignals(False)

    def _set_combo(self, combo, items):
        combo.blockSignals(True)
        combo.clear()
        for label, value in items:
            ui_call(combo, "addItem", label, value)
        combo.blockSignals(False)

    def selected_run_id(self):
        return self.run.currentData()

    def _tab_changed(self, index):
        items = (
            ((tr("viewer.fracture_network"), "structure"),)
            if index == 0
            else (
                (tr("viewer.computational_mesh"), "mesh"),
                (tr("viewer.material_regions"), "material"),
                (tr("viewer.permeability"), "permeability"),
                (tr("viewer.porosity"), "porosity"),
            )
            if index == 1
            else ((tr("viewer.pflotran_time_series"), "results"),)
        )
        if index == 1:
            anisotropic = bool(
                self.config
                and any(
                    (
                        m.permeability.anisotropic_dataset or m.permeability.mode == "diagonal"
                        for m in self.config.materials
                    )
                )
            )
            if anisotropic:
                items = tuple((item for item in items if item[1] != "permeability")) + tuple(
                    (
                        (
                            join_text("", [tr("viewer.permeability"), " ", axis]),
                            join_text("", ["permeability_", axis]),
                        )
                        for axis in "XYZ"
                    )
                )
        self.run.setVisible(index == 2)
        self.run_label.setVisible(index == 2)
        self._set_combo(self.data, items)
        self._data_changed(0)

    def reload_artifacts(self, config=None):
        selected_tab = self.tabs.currentIndex()
        selected_data = self.data.currentData()
        selected_property = self.property.currentData()
        if config is not None:
            self.config = config
        self.repository = ProjectVisualizationRepository(
            self.repository.root, self.repository.provider
        )
        self.refresh_run_labels()
        self._dataset = None
        self._tab_changed(selected_tab)
        data_index = self.data.findData(selected_data)
        if data_index >= 0 and data_index != self.data.currentIndex():
            self.data.setCurrentIndex(data_index)
        property_index = self.property.findData(selected_property)
        if property_index >= 0:
            self.property.setCurrentIndex(property_index)

    def _data_changed(self, _=0):
        if self._disposed:
            return
        self._load_sequence += 1
        self._dataset = None
        ui_call(self.message, "setText", msg("state.loading"))
        self.message.show()
        if not self._loading:
            self._start_data_load()

    def _start_data_load(self):
        sequence = self._load_sequence
        key = self.data.currentData()
        run_id = self.run.currentData()
        repository, config = self.repository, self.config
        self._loading = True

        def read():
            if str(key).startswith("permeability"):
                return repository.property(key, config)
            return {
                "structure": repository.spatial_structure,
                "mesh": repository.mesh,
                "material": lambda: repository.material(config),
                "porosity": lambda: repository.property("porosity", config),
                "results": lambda: repository.simulation_results(run_id, generate_missing=True),
            }[key]()

        worker = BackgroundCall(read)
        self._load_worker = worker

        def finish(value, failed=False):
            if sip.isdeleted(self):
                return
            self._loading = False
            self._load_worker = None
            if self._disposed:
                return
            if sequence != self._load_sequence:
                self._start_data_load()
            elif failed:
                self._load_failed(value)
            else:
                self._apply_dataset(value)

        worker.signals.succeeded.connect(finish)
        worker.signals.failed.connect(lambda error: finish(error, True))
        QtCore.QThreadPool.globalInstance().start(worker)

    def _apply_dataset(self, dataset):
        try:
            self._dataset = dataset
            arrays = [(array.label, name) for name, array in self._dataset.arrays.items()] or [
                ("—", None)
            ]
            self._set_combo(self.property, arrays)
            labels = self._dataset.metadata.get("time_labels", ())
            times = [
                (labels[index] if index < len(labels) else self._format_time(value, index), index)
                for index, value in enumerate(self._dataset.time_values)
            ] or [("—", 0)]
            self._set_combo(self.time, times)
            has_time = bool(self._dataset.time_values)
            self.time.setVisible(has_time)
            self.time_label.setVisible(has_time)
            has_array = bool(self._dataset.arrays)
            self.property.setVisible(has_array)
            self.property_label.setVisible(has_array)
            blockers = [
                QtCore.QSignalBlocker(self.representation),
                QtCore.QSignalBlocker(self.log_scale),
            ]
            rep = self.representation.findData(self._dataset.default_representation)
            self.representation.setCurrentIndex(max(0, rep))
            descriptor = self._dataset.arrays.get(self._dataset.default_array)
            self.log_scale.setChecked(bool(descriptor and descriptor.preferred_scale == "log"))
            self.log_scale.setEnabled(bool(descriptor and descriptor.kind == "continuous"))
            del blockers
            self.canvas.engine.set_dataset(self._dataset) if self.canvas.engine else None
            self._show_dataset_notice()
            self._render()
        except Exception as exc:
            self._load_failed(exc)

    def _load_failed(self, error):
        self._dataset = None
        self._set_combo(self.property, [("—", None)])
        self._set_combo(self.time, [("—", 0)])
        if self.canvas.engine:
            self.canvas.engine.clear()
        ui_call(
            self.message,
            "setText",
            join_text("", [tr("viewer.load_failed"), ": ", diagnostic_summary(error)]),
        )
        self.message.show()

    @staticmethod
    def _format_time(seconds, index=0):
        if seconds is None:
            return format_message("results.unknown_time", index=index + 1)
        return (
            format_message("results.days", value=f"{seconds / 86400:.4g}")
            if seconds >= 86400
            else join_text("", [f"{seconds:g}", " s"])
        )

    def _show_dataset_notice(self):
        # Provenance stays in the run record; successful viewing has no footer notice.
        ui_call(self.message, "setText", "")
        self.message.hide()

    def _render(self, *_):
        if not self._dataset or not self.canvas.engine:
            return
        try:
            name = self.property.currentData()
            descriptor = self._dataset.arrays.get(name)
            self.log_scale.setEnabled(bool(descriptor and descriptor.kind == "continuous"))
            self.canvas.engine.render(
                name,
                self.time.currentData() or 0,
                self.representation.currentData(),
                self.opacity.value() / 100,
                self.legend.isChecked(),
                self.log_scale.isChecked(),
            )
            self._show_dataset_notice()
        except Exception as exc:
            ui_call(
                self.message,
                "setText",
                join_text("", [tr("viewer.load_failed"), ": ", diagnostic_summary(exc)]),
            )
            self.message.show()

    def _reset(self):
        if self.canvas.engine:
            self.canvas.engine.reset_camera()

    def dispose(self):
        self._disposed = True
        self._opacity_render_timer.stop()
        self.canvas.close()

    def closeEvent(self, event):
        self.dispose()
        super().closeEvent(event)


class LocateVisualizationPanel(QtWidgets.QWidget):
    def __init__(self, project_directory, config=None, parent=None):
        super().__init__(parent)
        self.repository = ProjectVisualizationRepository(project_directory)
        self.config = config
        root = QtWidgets.QVBoxLayout(self)
        bar = QtWidgets.QHBoxLayout()
        self.object_combo = QtWidgets.QComboBox()
        self.property_combo = QtWidgets.QComboBox()
        self.representation = QtWidgets.QComboBox()
        for label, value in (
            (msg("text.mesh"), "mesh"),
            (msg("viewer.materials"), "material"),
            (msg("text.regions"), "region"),
            (msg("text.assignments"), "assignment"),
        ):
            ui_call(self.object_combo, "addItem", label, value)
        _add_representations(self.representation)
        self.legend = ui_construct(QtWidgets.QCheckBox, tr("viewer.legend"))
        self.legend.setChecked(True)
        reset = ui_construct(QtWidgets.QPushButton, tr("viewer.reset"))
        for label, widget in (
            (tr("viewer.display_object"), self.object_combo),
            (tr("viewer.property"), self.property_combo),
            (tr("viewer.representation"), self.representation),
        ):
            bar.addWidget(ui_construct(QtWidgets.QLabel, label))
            bar.addWidget(widget)
        bar.addWidget(self.legend)
        bar.addStretch()
        bar.addWidget(reset)
        close = ui_construct(QtWidgets.QPushButton, msg("text.close.locator"))
        close.clicked.connect(self.hide)
        bar.addWidget(close)
        root.addLayout(bar)
        self.canvas = VisualizationCanvas()
        root.addWidget(self.canvas, 1)
        self.message = ui_construct(QtWidgets.QLabel)
        self.message.setWordWrap(True)
        self.message.setObjectName("MutedLabel")
        root.addWidget(self.message)
        self.details = ui_construct(QtWidgets.QLabel)
        self.details.setWordWrap(True)
        root.addWidget(self.details)
        self._dataset = None
        self._selection = None
        self.object_combo.currentIndexChanged.connect(self._object_changed)
        self.property_combo.currentIndexChanged.connect(self._property_changed)
        self.representation.currentIndexChanged.connect(self._render)
        self.legend.toggled.connect(self._render)
        reset.clicked.connect(
            lambda: self.canvas.engine.reset_camera() if self.canvas.engine else None
        )
        self._object_changed()

    def _set_properties(self, items):
        self.property_combo.blockSignals(True)
        self.property_combo.clear()
        for label, value in items:
            ui_call(self.property_combo, "addItem", label, value)
        self.property_combo.blockSignals(False)

    def _object_changed(self, *_):
        kind = self.object_combo.currentData()
        try:
            if kind == "mesh":
                self._dataset = self.repository.mesh()
                self._set_properties([("—", None)])
            elif kind == "material":
                self._dataset = self.repository.material(self.config)
                self._set_properties([(msg("viewer.material"), "Material")])
            elif kind == "region":
                self._set_properties([(msg("viewer.region"), "Region")])
                return
            else:
                self._set_properties(
                    [
                        (msg("viewer.assignment"), "Assignment"),
                        (msg("viewer.usage"), "Usage"),
                        (msg("model.flow_condition"), "Flow Condition"),
                        (msg("model.transport_condition"), "Transport Condition"),
                        (msg("viewer.region"), "Region"),
                    ]
                )
                return
            self._activate()
        except Exception as exc:
            ui_call(
                self.message,
                "setText",
                join_text("", [tr("viewer.load_failed"), ": ", diagnostic_summary(exc)]),
            )

    def _property_changed(self, *_):
        if (
            self.object_combo.currentData() == "assignment"
            and hasattr(self, "_assignment")
            and self._assignment
        ):
            self.show_assignment(self._assignment, self._region, self.property_combo.currentData())

    def _activate(self, selection=None):
        self._selection = selection
        ui_call(
            self.message,
            "setText",
            self._dataset.metadata.get("selection_description", state_text(self._dataset.name)),
        )
        if self.canvas.engine:
            self.canvas.engine.set_dataset(self._dataset)
            self._render()

    def _render(self, *_):
        if not self._dataset or not self.canvas.engine:
            return
        try:
            self.canvas.engine.render(
                self.property_combo.currentData(),
                representation=self.representation.currentData(),
                show_legend=self.legend.isChecked(),
                selection=self._selection,
            )
            ui_call(
                self.message,
                "setText",
                self._dataset.metadata.get("selection_description", state_text(self._dataset.name)),
            )
        except Exception as exc:
            ui_call(
                self.message,
                "setText",
                join_text("", [tr("viewer.load_failed"), ": ", diagnostic_summary(exc)]),
            )

    def clear_selection(self, message):
        self._dataset = None
        self._selection = None
        self.details.clear()
        ui_call(self.message, "setText", message)
        if self.canvas.engine:
            self.canvas.engine.clear()

    def show_material(self, material_id):
        self.object_combo.setCurrentIndex(self.object_combo.findData("material"))
        self._dataset = self.repository.material(self.config)
        self._activate(material_id)

    def show_region(self, region):
        self.object_combo.setCurrentIndex(self.object_combo.findData("region"))
        self._dataset = self.repository.selected_region(region, config=self.config)
        self.representation.setCurrentIndex(self.representation.findData("points"))
        self._activate(1)

    def show_assignment(self, assignment, region, property_name="Assignment"):
        self._assignment = assignment
        self._region = region
        self.object_combo.setCurrentIndex(self.object_combo.findData("assignment"))
        self._dataset = self.repository.selected_assignment(
            assignment, region, property_name or "Assignment", config=self.config
        )
        self.representation.setCurrentIndex(self.representation.findData("points"))
        self._activate(1)
