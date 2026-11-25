from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from PyQt5 import QtCore, QtGui, QtWidgets

import fracrtm.config.pflotran as model
from fracrtm.config import BlankConfigFactory
from fracrtm.messages import exception_text, join_text
from fracrtm.messages import message as msg
from fracrtm.pflotran.continuum_manifest import ContinuumManifest
from fracrtm.pflotran.spatial_data_inspector import (
    ProjectSpatialDataSummary,
    inspect_project_spatial_data,
)
from fracrtm.pflotran.thermodynamic_database import (
    ParsedThermodynamicDatabase,
    parse_thermodynamic_database,
)
from fracrtm.project.simulation_mesh import DEFAULT_MODEL_SOURCE, ModelSourceProvider
from fracrtm.ui.dialogs.species_selection_dialog import (
    ChemicalConstraintEditorDialog,
    MineralSelectionDialog,
    SpeciesSelectionDialog,
)
from fracrtm.ui.localization import (
    format_message,
    render_text,
    set_label_text,
    ui_call,
    ui_construct,
)
from fracrtm.ui.qt_helpers import LocalizedDialog, MessageBox
from fracrtm.ui.styles import DIALOG_STYLE

from .pflotran_controls import MultiReferenceDialog, ScientificValueEdit, SolverInputError
from .pflotran_pages import PFLOTRANPageBuilder


def _float(text: str, label: str, optional: bool = False) -> float | None:
    value = text.strip()
    if optional and (not value):
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(format_message("validation.number", field=label)) from exc


def _csv(text: str) -> list[str]:
    return [value.strip() for value in text.split(",") if value.strip()]


def _numbers(text: str, label: str) -> list[float]:
    return [float(value) for value in _csv(text)]


class PFLOTRANConfigDialog(PFLOTRANPageBuilder, LocalizedDialog):
    def __init__(
        self,
        config: model.PFLOTRANConfig | None = None,
        parent=None,
        *,
        project_directory: str | Path | None = None,
        octree_directory: str = "octree",
        model_source: ModelSourceProvider = DEFAULT_MODEL_SOURCE,
    ):
        super().__init__(parent)
        source = config if config is not None else BlankConfigFactory.pflotran()
        self._source = model.PFLOTRANConfig.from_dict(source.to_dict())
        self._result: model.PFLOTRANConfig | None = None
        self._tables: list[QtWidgets.QTableWidget] = []
        self._project_directory = (
            Path(project_directory).expanduser() if project_directory else None
        )
        from fracrtm.project.storage import ResourceImports

        self._resource_imports = ResourceImports(self._project_directory)
        self._octree_directory = octree_directory or "octree"
        self._model_source = model_source
        self._continuum_manifest: ContinuumManifest | None = None
        self._spatial_summary = ProjectSpatialDataSummary()
        self._parsed_thermodynamic_database = ParsedThermodynamicDatabase()
        self._selected_primary_species: list[str] = []
        self._selected_secondary_species: list[str] = []
        self._selected_passive_gases: list[str] = []
        self._selected_minerals: list[str] = []
        self._material_records: list[dict[str, Any]] = []
        self._material_loading = False
        self._active_material_row = -1
        self.locate_view = None
        self._locator_kind = None
        ui_call(self, "setWindowTitle", msg("text.pflotran.model.configuration"))
        self.resize(1500, 900)
        self.setMinimumSize(1180, 760)
        self.setStyleSheet(DIALOG_STYLE)
        self._editor_snapshot = None
        self._build_ui()
        self._load(self._source)

    @staticmethod
    def _scientific_spin(widget: QtWidgets.QDoubleSpinBox) -> None:
        widget.setDecimals(323)
        widget.setRange(1e-300, 1e300)
        widget.setSingleStep(1e-08)
        widget.setMinimumWidth(150)
        widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    @staticmethod
    def _unit_row(widget: QtWidgets.QWidget, unit: str) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        box = QtWidgets.QHBoxLayout(row)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(widget)
        box.addWidget(ui_construct(QtWidgets.QLabel, unit))
        box.addStretch()
        return row

    def _section(self, layout, title: str, table: QtWidgets.QTableWidget, default_row: list[str]):
        group = ui_construct(QtWidgets.QGroupBox, title)
        box = QtWidgets.QVBoxLayout(group)
        box.addWidget(table)
        buttons = QtWidgets.QHBoxLayout()
        add = ui_construct(QtWidgets.QPushButton, msg("text.add"))
        remove = ui_construct(QtWidgets.QPushButton, msg("text.delete.selected"))
        add.clicked.connect(lambda: self._append_row(table, default_row))
        remove.clicked.connect(lambda: self._remove_rows(table))
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()
        box.addLayout(buttons)
        layout.addWidget(group)

    def _update_gravity_ui(self, *args) -> None:
        self.gravity_custom_panel.setVisible(self.gravity_mode.currentData() == "custom")

    def _create_process_solver_page(self):
        page = QtWidgets.QWidget()
        page_layout = QtWidgets.QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 8, 4)
        newton_group = ui_construct(QtWidgets.QGroupBox, msg("text.newton.nonlinear.solver"))
        newton_form = QtWidgets.QFormLayout(newton_group)
        newton_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        newton_form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        widgets = {}
        widgets["newton_maximum_iterations"] = QtWidgets.QSpinBox()
        widgets["newton_maximum_iterations"].setRange(0, 10000)
        ui_call(widgets["newton_maximum_iterations"], "setSpecialValueText", msg("text.not.set"))
        widgets["newton_snes_type"] = QtWidgets.QComboBox()
        ui_call(widgets["newton_snes_type"], "addItem", msg("text.not.set"), None)
        ui_call(widgets["newton_snes_type"], "addItem", "LINE_SEARCH", "LINE_SEARCH")
        ui_call(widgets["newton_snes_type"], "addItem", "TRUST_REGION", "TRUST_REGION")
        widgets["newton_rtol"] = ScientificValueEdit(None, optional=True)
        widgets["newton_atol"] = ScientificValueEdit(1e-50, optional=True)
        widgets["newton_itol"] = ScientificValueEdit(None, optional=True)
        widgets["newton_stol"] = ScientificValueEdit(1e-08, optional=True)
        widgets["newton_dtol"] = ScientificValueEdit(10000.0, optional=True)
        ui_call(
            newton_form,
            "addRow",
            msg("text.maximum.newton.iterations"),
            widgets["newton_maximum_iterations"],
        )
        ui_call(
            newton_form,
            "addRow",
            msg("text.nonlinear.method.snes.type"),
            widgets["newton_snes_type"],
        )
        ui_call(
            newton_form,
            "addRow",
            msg("text.relative.residual.tolerance.rtol"),
            widgets["newton_rtol"],
        )
        ui_call(
            newton_form,
            "addRow",
            msg("text.absolute.residual.tolerance.atol"),
            widgets["newton_atol"],
        )
        ui_call(newton_form, "addRow", msg("text.iteration.tolerance.itol"), widgets["newton_itol"])
        ui_call(
            newton_form,
            "addRow",
            msg("text.relative.update.tolerance.stol"),
            widgets["newton_stol"],
        )
        ui_call(
            newton_form, "addRow", msg("text.divergence.tolerance.dtol"), widgets["newton_dtol"]
        )
        linear_group = ui_construct(QtWidgets.QGroupBox, msg("text.linear.solver"))
        linear_form = QtWidgets.QFormLayout(linear_group)
        linear_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        linear_form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        widgets["linear_solver_type"] = QtWidgets.QComboBox()
        for label, keyword in (
            ("BiCGStab (BCGS)", "BCGS"),
            ("GMRES", "GMRES"),
            (msg("text.improved.bicgstab.ibcgs"), "IBCGS"),
            (msg("text.direct.direct"), "DIRECT"),
            (msg("text.iterative.iterative"), "ITERATIVE"),
        ):
            ui_call(widgets["linear_solver_type"], "addItem", label, keyword)
        widgets["linear_preconditioner"] = QtWidgets.QComboBox()
        ui_call(widgets["linear_preconditioner"], "addItem", msg("text.use.default"), None)
        for keyword in ("NONE", "ILU", "LU", "BJACOBI", "ASM", "HYPRE", "CPR"):
            ui_call(widgets["linear_preconditioner"], "addItem", keyword, keyword)
        widgets["linear_rtol"] = ScientificValueEdit(1e-05, optional=True)
        widgets["linear_atol"] = ScientificValueEdit(1e-50, optional=True)
        widgets["linear_dtol"] = ScientificValueEdit(10000.0, optional=True)
        ui_call(linear_form, "addRow", msg("solver.selector"), widgets["linear_solver_type"])
        ui_call(linear_form, "addRow", msg("text.preconditioner"), widgets["linear_preconditioner"])
        ui_call(
            linear_form,
            "addRow",
            msg("text.relative.residual.tolerance.rtol"),
            widgets["linear_rtol"],
        )
        ui_call(
            linear_form,
            "addRow",
            msg("text.absolute.residual.tolerance.atol"),
            widgets["linear_atol"],
        )
        ui_call(
            linear_form, "addRow", msg("text.divergence.tolerance.dtol"), widgets["linear_dtol"]
        )
        content_layout.addWidget(newton_group)
        content_layout.addWidget(linear_group)
        content_layout.addStretch()
        scroll.setWidget(content)
        page_layout.addWidget(scroll)
        return (page, widgets)

    @staticmethod
    def _summary_selection_row(summary, button):
        row = QtWidgets.QWidget()
        row_layout = QtWidgets.QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(summary, 1)
        row_layout.addWidget(button)
        return row

    def _assignment_region_choices(self):
        labels = {
            "COORDINATE": msg("text.point"),
            "COORDINATES": msg("text.spatial.extent"),
            "LIST": msg("text.cell.region"),
            "FILE": msg("text.boundary.surface.file.region"),
        }
        return [
            (
                join_text("", [item.name, " · ", labels.get(item.region_type, item.region_type)]),
                item.name,
            )
            for item in self.region_conditions_editor.regions()
        ]

    def _assignment_flow_choices(self):
        labels = {
            "fixed_liquid_pressure": msg("text.fixed.liquid.pressure"),
            "hydrostatic_head": msg("text.hydraulic.head.or.hydrostatic.pressure"),
            "darcy_flux": msg("text.boundary.darcy.flux"),
            "seepage": msg("text.seepage.face"),
            "scaled_volumetric_rate": msg("text.total.volumetric.flow.rate"),
            "scaled_mass_rate": msg("text.total.mass.flow.rate"),
        }
        return [
            (join_text("", [item.name, " · ", labels.get(item.form, item.form)]), item.name)
            for item in self.region_conditions_editor.flow_conditions()
        ]

    def _assignment_transport_choices(self):
        labels = {
            "initial_solution": msg("text.initial.solution.composition"),
            "inflow_solution": msg("text.inflow.solution.composition"),
            "fixed_solution": msg("text.fixed.solution.composition"),
            "zero_gradient_outflow": msg("text.free.outflow.zero.gradient"),
            "injection_solution": msg("text.injected.solution.composition"),
        }
        return [
            (join_text("", [item.name, " · ", labels.get(item.form, item.form)]), item.name)
            for item in self.region_conditions_editor.transport_conditions()
        ]

    def _spatial_assignment_changed(self):
        self._mark_dirty()

    def _open_spatial_viewer(self, kind="region"):
        from fracrtm.ui.results_viewer import LocateVisualizationPanel

        if self._project_directory is None:
            ui_call(
                self.status,
                "setText",
                msg("text.select.a.project.before.locating.spatial.resources"),
            )
            return
        self._locator_kind = kind
        if self.locate_view is None:
            self.locate_view = LocateVisualizationPanel(self._project_directory, parent=self)
            self.locate_view.setMinimumWidth(420)
            self.body_splitter.addWidget(self.locate_view)
            self.body_splitter.setStretchFactor(1, 1)
        self.locate_view.show()
        self.body_splitter.setSizes([950, 550])
        self._refresh_spatial_locator()

    def _refresh_spatial_locator(self, *_):
        if self.locate_view is None or self.locate_view.isHidden():
            return
        from fracrtm.visualization import ProjectVisualizationRepository

        panel = self.locate_view
        try:
            config = self._collect()
            panel.config = config
            panel.repository = ProjectVisualizationRepository(self._project_directory)
            if self._locator_kind == "assignment":
                assignment = self.spatial_assignment_editor.selected_assignment()
                if assignment is None:
                    raise ValueError(msg("text.select.a.condition.assignment.to.locate"))
                region = next((r for r in config.regions if r.name == assignment.region_name), None)
                if region is None:
                    raise ValueError(
                        format_message(
                            "assignment.missing_region",
                            name=assignment.name,
                            region=f"{assignment.region_name!r}",
                        )
                    )
                panel.show_assignment(assignment, region)
            else:
                region = self.region_conditions_editor.selected_region()
                if region is None:
                    raise ValueError(msg("text.select.a.region.to.locate"))
                panel.show_region(region)
            associations = [
                msg(
                    "assignment.region_summary",
                    name=a.name,
                    state=a.usage,
                    flow=a.flow_condition_name or "—",
                    transport=a.transport_condition_name or "—",
                )
                for a in config.spatial_assignment.condition_assignments
                if a.region_name == region.name
            ]
            ui_call(
                panel.details,
                "setText",
                join_text("\n", associations)
                if associations
                else msg("text.no.condition.assignments.reference.this.region"),
            )
        except Exception as exc:
            panel.clear_selection(exception_text(exc))

    def closeEvent(self, event):
        if self.locate_view is not None:
            self.locate_view.canvas.close()
        super().closeEvent(event)

    def _show_selected_material_properties(self) -> None:
        material_id = self.spatial_assignment_editor.selected_material_id()
        self.navigation.setCurrentRow(2)
        if material_id is None:
            return
        for row, record in enumerate(self._material_records):
            if record["material_id"] == material_id:
                self.materials.setCurrentRow(row)
                break

    def _confirm_region_delete(self, name: str) -> bool:
        referenced = any(
            (item.region_name == name for item in self.spatial_assignment_editor.assignments())
        )
        if referenced:
            answer = MessageBox.question(
                self,
                msg("text.delete.referenced.region"),
                format_message("region.delete_referenced", name=name),
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return False
        return True

    def _confirm_condition_delete(self, process: str, name: str) -> bool:
        referenced = any(
            (
                (item.flow_condition_name if process == "FLOW" else item.transport_condition_name)
                == name
                for item in self.spatial_assignment_editor.assignments()
            )
        )
        if referenced:
            answer = MessageBox.question(
                self,
                msg("text.delete.referenced.condition"),
                format_message("condition.delete_referenced", name=name),
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return False
        return True

    def _append_row(self, table: QtWidgets.QTableWidget, values: list[str]) -> None:
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(values):
            table.setItem(row, column, ui_construct(QtWidgets.QTableWidgetItem, value))
        table.setCurrentCell(row, 0)
        self._mark_dirty()

    def _set_rows(self, table: QtWidgets.QTableWidget, rows: list[list[object]]) -> None:
        table.blockSignals(True)
        table.setRowCount(0)
        for values in rows:
            row = table.rowCount()
            table.insertRow(row)
            for column, value in enumerate(values):
                table.setItem(
                    row,
                    column,
                    ui_construct(QtWidgets.QTableWidgetItem, "" if value is None else str(value)),
                )
        table.blockSignals(False)

    def _remove_rows(self, table: QtWidgets.QTableWidget) -> None:
        rows = sorted({index.row() for index in table.selectedIndexes()}, reverse=True)
        for row in rows:
            table.removeRow(row)
        self._mark_dirty()

    @staticmethod
    def _rows(table: QtWidgets.QTableWidget) -> list[list[str]]:
        return [
            [
                table.item(row, column).text().strip() if table.item(row, column) else ""
                for column in range(table.columnCount())
            ]
            for row in range(table.rowCount())
        ]

    def _column(self, table: QtWidgets.QTableWidget, column: int) -> list[str]:
        return [row[column] for row in self._rows(table) if row[column]]

    def _material_names(self) -> list[str]:
        return [record["name"] for record in self._material_records if record["name"]]

    def _mark_dirty(self, *args) -> None:
        set_label_text(self.status, msg("text.unchecked.changes"))
        self.status.setStyleSheet(
            "padding:6px 12px;border-radius:12px;background:#fff4d8;color:#805d12;"
        )

    def _flow_selection_changed(self, enabled: bool) -> None:
        if not enabled and self.transport_enabled_check.isChecked():
            blocker = QtCore.QSignalBlocker(self.transport_enabled_check)
            self.transport_enabled_check.setChecked(False)
            del blocker
        self._update_task_ui()

    def _transport_selection_changed(self, enabled: bool) -> None:
        if enabled and (not self.flow_enabled_check.isChecked()):
            blocker = QtCore.QSignalBlocker(self.flow_enabled_check)
            self.flow_enabled_check.setChecked(True)
            del blocker
        self._update_task_ui()

    def _update_solver_mode_ui(self, *args) -> None:
        if not hasattr(self, "solver_mode_stack"):
            return
        custom = self.custom_radio.isChecked()
        self.solver_mode_stack.setCurrentIndex(1 if custom else 0)
        self._refresh_solver_process_tabs()
        self._update_recommended_solver_summary()
        self._mark_dirty()

    def _refresh_solver_process_tabs(self) -> None:
        flow_enabled = self.flow_enabled_check.isChecked()
        transport_enabled = self.transport_enabled_check.isChecked()
        self.process_tab_widget.setTabVisible(0, flow_enabled)
        self.process_tab_widget.setTabVisible(1, transport_enabled)
        self.process_tab_widget.setVisible(flow_enabled or transport_enabled)
        self.no_process_solver_label.setVisible(not (flow_enabled or transport_enabled))
        if flow_enabled:
            self.process_tab_widget.setCurrentIndex(0)
        elif transport_enabled:
            self.process_tab_widget.setCurrentIndex(1)

    def _update_recommended_solver_summary(self) -> None:
        lines = [msg("text.using.the.recommended.pflotran.solver.settings")]
        if self.flow_enabled_check.isChecked():
            lines.extend(
                [
                    "",
                    msg("solver.flow_heading"),
                    msg("text.flow.uses.the.default.pflotran.newton.and.linear.solver.settings"),
                ]
            )
        if self.transport_enabled_check.isChecked():
            lines.extend(
                [
                    "",
                    msg("solver.transport_heading"),
                    msg(
                        "text.transport.uses.the.default.pflotran.newton.and.linear.solver.settings"
                    ),
                ]
            )
        if not self.flow_enabled_check.isChecked() and (
            not self.transport_enabled_check.isChecked()
        ):
            lines = [msg("text.no.configurable.simulation.process.is.enabled")]
        ui_call(self.recommended_summary_label, "setText", join_text("\n", lines))

    @staticmethod
    def _load_process_solver_widgets(widgets: dict, process) -> None:
        widgets["newton_maximum_iterations"].setValue(process.newton.maximum_iterations or 0)
        index = widgets["newton_snes_type"].findData(process.newton.snes_type)
        widgets["newton_snes_type"].setCurrentIndex(max(0, index))
        widgets["newton_rtol"].setValue(process.newton.rtol)
        widgets["newton_atol"].setValue(process.newton.atol)
        widgets["newton_itol"].setValue(process.newton.itol)
        widgets["newton_stol"].setValue(process.newton.stol)
        widgets["newton_dtol"].setValue(process.newton.dtol)
        solver_index = widgets["linear_solver_type"].findData(process.linear.solver_type)
        widgets["linear_solver_type"].setCurrentIndex(max(0, solver_index))
        index = widgets["linear_preconditioner"].findData(process.linear.preconditioner)
        widgets["linear_preconditioner"].setCurrentIndex(max(0, index))
        widgets["linear_rtol"].setValue(process.linear.rtol)
        widgets["linear_atol"].setValue(process.linear.atol)
        widgets["linear_dtol"].setValue(process.linear.dtol)

    @staticmethod
    def _collect_process_solver_widgets(widgets: dict, process) -> None:
        process.newton.maximum_iterations = widgets["newton_maximum_iterations"].value() or None
        process.newton.snes_type = widgets["newton_snes_type"].currentData()
        process.newton.rtol = widgets["newton_rtol"].value()
        process.newton.atol = widgets["newton_atol"].value()
        process.newton.itol = widgets["newton_itol"].value()
        process.newton.stol = widgets["newton_stol"].value()
        process.newton.dtol = widgets["newton_dtol"].value()
        process.linear.solver_type = widgets["linear_solver_type"].currentData()
        process.linear.preconditioner = widgets["linear_preconditioner"].currentData()
        process.linear.rtol = widgets["linear_rtol"].value()
        process.linear.atol = widgets["linear_atol"].value()
        process.linear.dtol = widgets["linear_dtol"].value()

    def _update_curve_ui(self, *args) -> None:
        if not hasattr(self, "curve_type_combo"):
            return
        kind = self.curve_type_combo.currentData()
        self.vg_parameter_panel.setVisible(kind in {"van_genuchten", "brooks_corey"})
        self.vg_m.setRange(1e-8, 0.99999999 if kind == "van_genuchten" else 1e6)
        self.curve_shape_label.setText("m" if kind == "van_genuchten" else "λ")

    def _update_porosity_editor(self, *args) -> None:
        if hasattr(self, "porosity_stack"):
            self.porosity_stack.setCurrentIndex(
                0 if self.porosity_source_combo.currentData() == "constant" else 1
            )

    def _update_permeability_editor(self, *args) -> None:
        if not hasattr(self, "permeability_stack"):
            return
        key = (
            self.permeability_source_combo.currentData(),
            self.permeability_form_combo.currentData(),
        )
        self.permeability_stack.setCurrentIndex(
            {
                ("constant", "isotropic"): 0,
                ("constant", "anisotropic"): 1,
                ("dataset", "isotropic"): 2,
                ("dataset", "anisotropic"): 3,
            }[key]
        )

    def _material_rows_from_config(self, c: model.PFLOTRANConfig) -> list[dict]:
        curves = {curve.id: curve for curve in c.characteristic_curves}
        rows = []
        for material in c.materials:
            permeability = material.permeability
            curve = curves.get(material.characteristic_curve_id or "")
            rows.append(
                {
                    "internal_id": material.id,
                    "name": material.name,
                    "material_id": material.material_id,
                    "porosity_source": material.porosity.mode,
                    "porosity_value": material.porosity.value
                    if material.porosity.value is not None
                    else 0.1,
                    "porosity_dataset": material.porosity.dataset_id or "",
                    "permeability_source": "dataset"
                    if permeability.mode == "dataset"
                    else "constant",
                    "permeability_form": "anisotropic"
                    if permeability.mode == "diagonal" or permeability.anisotropic_dataset
                    else "isotropic",
                    "k": permeability.isotropic if permeability.isotropic is not None else 1e-18,
                    "kx": (permeability.diagonal or [1e-18, 1e-18, 1e-18])[0],
                    "ky": (permeability.diagonal or [1e-18, 1e-18, 1e-18])[1],
                    "kz": (permeability.diagonal or [1e-18, 1e-18, 1e-18])[2],
                    "permeability_dataset": permeability.dataset_id or "",
                    "permeability_dataset_x": getattr(permeability, "dataset_x", None) or "",
                    "permeability_dataset_y": getattr(permeability, "dataset_y", None) or "",
                    "permeability_dataset_z": getattr(permeability, "dataset_z", None) or "",
                    "tortuosity": material.tortuosity,
                    "longitudinal": material.longitudinal_dispersivity,
                    "transverse_h": material.transverse_dispersivity_horizontal,
                    "transverse_v": material.transverse_dispersivity_vertical,
                    "curve_id": material.characteristic_curve_id or "",
                    "curve_type": curve.model if curve else "default",
                    "curve_permeability_function": curve.permeability_function
                    if curve
                    else "MUALEM",
                    "curve_smooth": curve.smooth if curve else False,
                    "vg_alpha": curve.alpha if curve and curve.alpha is not None else 0.0001,
                    "vg_m": curve.m_or_lambda if curve and curve.m_or_lambda is not None else 0.5,
                    "vg_residual": curve.liquid_residual_saturation if curve else 0.1,
                    "vg_max_capillary": curve.maximum_capillary_pressure if curve else None,
                }
            )
        return rows

    def _set_material_records(self, rows: list[dict]) -> None:
        self._material_records = rows
        self._active_material_row = -1
        self._refresh_material_dataset_choices()
        self._refresh_material_list()
        if rows:
            self._active_material_row = 0
            self.materials.setCurrentRow(0)
            self._load_material_editor(rows[0])

    def _material_dataset_ids(self, kind: str) -> list[str]:
        result = []
        for dataset_id, name, _file, hdf5_name in self._rows(self.datasets):
            text = join_text("", [dataset_id, " ", name, " ", hdf5_name]).casefold()
            if kind in text:
                result.append(dataset_id)
        return result

    @staticmethod
    def _set_combo_options(combo: QtWidgets.QComboBox, values: list[str]) -> None:
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.setProperty("fracrtm_user_content", bool(values))
        if values:
            for value in values:
                ui_call(combo, "addItem", value, value)
        else:
            ui_call(combo, "addItem", msg("text.no.datasets.available"), "")
            combo.model().item(0).setEnabled(False)
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _refresh_material_dataset_choices(self) -> None:
        if not hasattr(self, "porosity_dataset_combo"):
            return
        self._set_combo_options(self.porosity_dataset_combo, self._material_dataset_ids("porosity"))
        permeability_ids = self._material_dataset_ids("permeability")
        for combo in (
            self.permeability_dataset_combo,
            self.permeability_dataset_x_combo,
            self.permeability_dataset_y_combo,
            self.permeability_dataset_z_combo,
        ):
            self._set_combo_options(combo, permeability_ids)

    def _material_issues(self, record: dict, index: int) -> list[str]:
        issues = []
        name = record["name"].strip()
        if not name:
            issues.append(msg("validation.material.name.is.required"))
        if name and sum((1 for x in self._material_records if x["name"].strip() == name)) > 1:
            issues.append(msg("text.material.name.is.duplicated"))
        if record["material_id"] < 1:
            issues.append(msg("validation.material.id.must.be.a.positive.integer"))
        if (
            sum((1 for x in self._material_records if x["material_id"] == record["material_id"]))
            > 1
        ):
            issues.append(msg("text.material.id.is.duplicated"))
        if record["porosity_source"] == "constant":
            if not 0 < record["porosity_value"] <= 1:
                issues.append(msg("validation.porosity.must.satisfy.0.porosity.1"))
        elif record["porosity_dataset"] not in self._material_dataset_ids("porosity"):
            issues.append(msg("text.no.compatible.porosity.dataset"))
        if record["permeability_source"] == "constant":
            values = (
                [record["k"]]
                if record["permeability_form"] == "isotropic"
                else [record["kx"], record["ky"], record["kz"]]
            )
            if any((value <= 0 for value in values)):
                issues.append(msg("validation.permeability.must.be.positive"))
        elif record["permeability_form"] == "isotropic":
            if record["permeability_dataset"] not in self._material_dataset_ids("permeability"):
                issues.append(msg("text.no.compatible.permeability.dataset"))
        else:
            for axis in "xyz":
                if record[
                    join_text("", ["permeability_dataset_", axis])
                ] not in self._material_dataset_ids("permeability"):
                    issues.append(format_message("dataset.missing_axis", axis=axis.upper()))
        if self.transport_enabled_check.isChecked():
            if record["tortuosity"] <= 0:
                issues.append(msg("validation.tortuosity.must.be.positive.2"))
            if any((record[key] < 0 for key in ("longitudinal", "transverse_h", "transverse_v"))):
                issues.append(msg("validation.dispersivity.must.not.be.negative.2"))
        if record["curve_type"] in {"van_genuchten", "brooks_corey"}:
            if record["vg_alpha"] <= 0:
                issues.append(msg("validation.van.genuchten.must.be.positive"))
            if record["vg_m"] <= 0 or (
                record["curve_type"] == "van_genuchten" and record["vg_m"] >= 1
            ):
                issues.append(msg("validation.van.genuchten.m.must.be.between.0.and.1"))
            if not 0 <= record["vg_residual"] < 1:
                issues.append(msg("validation.residual.liquid.saturation.must.be.in.0.1"))
            if record.get("vg_max_capillary") is not None and record["vg_max_capillary"] <= 0:
                issues.append(msg("validation.maximum.capillary.pressure.must.be.positive.2"))
        return issues

    def _refresh_material_list(self) -> None:
        current = self.materials.currentRow() if hasattr(self, "materials") else -1
        self.materials.blockSignals(True)
        self.materials.clear()
        for index, record in enumerate(self._material_records):
            issues = self._material_issues(record, index)
            item = ui_construct(
                QtWidgets.QListWidgetItem,
                join_text(
                    "",
                    ["!" if issues else "✓", "  ", record["name"] or msg("text.unnamed.material")],
                ),
            )
            duplicate = any(
                issue.key in {"text.material.name.is.duplicated", "text.material.id.is.duplicated"}
                for issue in issues
            )
            item.setForeground(
                QtGui.QColor("#b42318" if duplicate else "#b54708" if issues else "#247a5a")
            )
            ui_call(
                item,
                "setToolTip",
                join_text("\n", issues)
                if issues
                else msg("validation.all.required.parameters.are.valid"),
            )
            ui_call(self.materials, "addItem", item)
        if self._material_records:
            self.materials.setCurrentRow(max(0, min(current, len(self._material_records) - 1)))
        self.materials.blockSignals(False)

    def _material_selection_changed(self) -> None:
        selected = self.materials.currentRow()
        if selected == self._active_material_row:
            return
        self._save_active_material()
        self._active_material_row = selected
        if 0 <= selected < len(self._material_records):
            self._load_material_editor(self._material_records[selected])

    @staticmethod
    def _select_combo_data(combo: QtWidgets.QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    @staticmethod
    def _set_optional_spin(widget: QtWidgets.QDoubleSpinBox, value: float | None) -> None:
        widget.setValue(widget.minimum() if value is None else float(value))

    @staticmethod
    def _optional_spin_value(widget: QtWidgets.QDoubleSpinBox) -> float | None:
        return None if widget.value() == widget.minimum() else widget.value()

    def _load_material_editor(self, values: dict) -> None:
        self._material_loading = True
        try:
            ui_call(self.material_name, "setText", values["name"])
            self.material_numeric_id.setValue(values["material_id"])
            self._select_combo_data(self.porosity_source_combo, values["porosity_source"])
            self.porosity_value_spin.setValue(values["porosity_value"])
            self._select_combo_data(self.porosity_dataset_combo, values["porosity_dataset"])
            self._select_combo_data(self.permeability_source_combo, values["permeability_source"])
            self._select_combo_data(self.permeability_form_combo, values["permeability_form"])
            self.permeability_k_spin.setValue(values["k"])
            self.permeability_kx_spin.setValue(values["kx"])
            self.permeability_ky_spin.setValue(values["ky"])
            self.permeability_kz_spin.setValue(values["kz"])
            self._select_combo_data(self.permeability_dataset_combo, values["permeability_dataset"])
            self._select_combo_data(
                self.permeability_dataset_x_combo, values["permeability_dataset_x"]
            )
            self._select_combo_data(
                self.permeability_dataset_y_combo, values["permeability_dataset_y"]
            )
            self._select_combo_data(
                self.permeability_dataset_z_combo, values["permeability_dataset_z"]
            )
            self.tortuosity.setValue(values["tortuosity"])
            self.longitudinal_disp.setValue(values["longitudinal"])
            self.transverse_h_disp.setValue(values["transverse_h"])
            self.transverse_v_disp.setValue(values["transverse_v"])
            self._select_combo_data(self.curve_type_combo, values["curve_type"])
            self.vg_alpha.setValue(values["vg_alpha"])
            self.vg_m.setValue(values["vg_m"])
            self.vg_residual.setValue(values["vg_residual"])
            self.vg_max_capillary.setValue(values.get("vg_max_capillary"))
            function = values.get("curve_permeability_function", "MUALEM")
            if self.curve_permeability_function.findText(function) < 0:
                self.curve_permeability_function.addItem(function)
            self.curve_permeability_function.setCurrentText(function)
            self.curve_smooth.setChecked(values.get("curve_smooth", False))
            self._update_porosity_editor()
            self._update_permeability_editor()
            self._update_curve_ui()
        finally:
            self._material_loading = False

    def _save_active_material(self) -> None:
        row = self._active_material_row
        if self._material_loading or not 0 <= row < len(self._material_records):
            return
        record = self._material_records[row]
        _old_name = record["name"]
        record.update(
            {
                "name": self.material_name.text().strip(),
                "material_id": self.material_numeric_id.value(),
                "porosity_source": self.porosity_source_combo.currentData(),
                "porosity_value": self.porosity_value_spin.value(),
                "porosity_dataset": self.porosity_dataset_combo.currentData() or "",
                "permeability_source": self.permeability_source_combo.currentData(),
                "permeability_form": self.permeability_form_combo.currentData(),
                "k": self.permeability_k_spin.value(),
                "kx": self.permeability_kx_spin.value(),
                "ky": self.permeability_ky_spin.value(),
                "kz": self.permeability_kz_spin.value(),
                "permeability_dataset": self.permeability_dataset_combo.currentData() or "",
                "permeability_dataset_x": self.permeability_dataset_x_combo.currentData() or "",
                "permeability_dataset_y": self.permeability_dataset_y_combo.currentData() or "",
                "permeability_dataset_z": self.permeability_dataset_z_combo.currentData() or "",
                "tortuosity": self.tortuosity.value(),
                "longitudinal": self.longitudinal_disp.value(),
                "transverse_h": self.transverse_h_disp.value(),
                "transverse_v": self.transverse_v_disp.value(),
                "curve_type": self.curve_type_combo.currentData(),
                "vg_alpha": self.vg_alpha.value(),
                "vg_m": self.vg_m.value(),
                "vg_residual": self.vg_residual.value(),
                "vg_max_capillary": self.vg_max_capillary.value(),
                "curve_permeability_function": self.curve_permeability_function.currentText(),
                "curve_smooth": self.curve_smooth.isChecked(),
            }
        )
        if hasattr(self, "spatial_assignment_editor"):
            self._refresh_material_mapping()
        self._refresh_material_list()

    def _material_editor_changed(self, *args) -> None:
        if self._material_loading:
            return
        self._save_active_material()
        self._mark_dirty()

    def _next_material_id(self) -> int:
        used = {record["material_id"] for record in self._material_records}
        candidate = 1
        while candidate in used:
            candidate += 1
        return candidate

    def _new_material_internal_id(self) -> str:
        used = {record["internal_id"] for record in self._material_records}
        number = 1
        while join_text("", ["material_", number]) in used:
            number += 1
        return join_text("", ["material_", number])

    def _add_material(self) -> None:
        self._save_active_material()
        number = 1
        names = {record["name"] for record in self._material_records}
        while format_message("material.new_name", index=number).render() in names:
            number += 1
        self._material_records.append(
            {
                "internal_id": self._new_material_internal_id(),
                "name": format_message("material.new_name", index=number).render(),
                "material_id": self._next_material_id(),
                "porosity_source": "constant",
                "porosity_value": 0.1,
                "porosity_dataset": "",
                "permeability_source": "constant",
                "permeability_form": "isotropic",
                "k": 1e-18,
                "kx": 1e-18,
                "ky": 1e-18,
                "kz": 1e-18,
                "permeability_dataset": "",
                "permeability_dataset_x": "",
                "permeability_dataset_y": "",
                "permeability_dataset_z": "",
                "tortuosity": 1.0,
                "longitudinal": 0.0,
                "transverse_h": 0.0,
                "transverse_v": 0.0,
                "curve_id": "",
                "curve_type": "default",
                "vg_alpha": 0.0001,
                "vg_m": 0.5,
                "vg_residual": 0.1,
                "vg_max_capillary": None,
            }
        )
        self._refresh_material_list()
        self.materials.setCurrentRow(len(self._material_records) - 1)
        self.material_name.setFocus()
        self.material_name.selectAll()
        self._mark_dirty()

    def _remove_material(self) -> None:
        row = self.materials.currentRow()
        if not 0 <= row < len(self._material_records):
            return
        if len(self._material_records) == 1:
            MessageBox.information(
                self,
                msg("text.cannot.delete"),
                msg("text.the.model.requires.at.least.one.material"),
            )
            return
        record = self._material_records[row]
        mapping = (
            self.spatial_assignment_editor.config().material_mapping
            if hasattr(self, "spatial_assignment_editor")
            else None
        )
        referenced = bool(
            mapping and any((item.material_id == record["material_id"] for item in mapping.items))
        )
        message = format_message("material.delete", name=record["name"])
        if referenced:
            message += msg(
                "text.this.material.is.referenced.by.a.spatial.assignment.references.must.be.selected"
            )
        answer = MessageBox.question(
            self,
            msg("text.delete.material"),
            message,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self._material_records.pop(row)
        self._active_material_row = -1
        self._refresh_material_list()
        if self._material_records:
            self.materials.setCurrentRow(min(row, len(self._material_records) - 1))
        self._mark_dirty()

    def _load_continuum_manifest(self) -> None:
        project = self._project_directory
        if project is None:
            return
        try:
            artifacts = self._model_source.simulation_mesh(project)

            def relative(path):
                return path.relative_to(project).as_posix() if path else ""

            self._continuum_manifest = ContinuumManifest(
                mesh=relative(artifacts.grid),
                materials=relative(artifacts.materials),
                permeability=relative(artifacts.permeability),
                porosity=relative(artifacts.porosity),
                boundary_regions={
                    name: item.relative_path for name, item in artifacts.boundaries.items()
                },
                visualization=relative(artifacts.visualization_geometry),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self._continuum_manifest = None

    def _rescan_spatial_data(self) -> None:
        self._load_continuum_manifest()
        self._apply_continuum_manifest()

    @staticmethod
    def _file_status_text(status: str) -> str:
        return {
            "available": msg("text.loaded"),
            "warning": msg("text.loaded.with.warnings"),
            "missing": msg("text.not.found"),
            "read_failed": msg("text.read.failed"),
            "invalid": msg("validation.invalid.format"),
        }.get(status, msg("text.read.failed"))

    @staticmethod
    def _status_symbol(status: str) -> str:
        return {
            "available": "✓",
            "warning": "!",
            "invalid": "×",
            "empty": "×",
            "read_failed": "×",
            "missing": "—",
        }.get(status, "×")

    @staticmethod
    def _number_range(minimum, maximum) -> str:
        if minimum is None or maximum is None:
            return "—"
        return join_text("", [f"{minimum:.4G}", "–", f"{maximum:.4G}"])

    def _refresh_spatial_data_view(self) -> None:
        summary = self._spatial_summary
        mesh = summary.mesh
        materials = summary.materials
        ui_call(self.mesh_file_status, "setText", self._file_status_text(mesh.status))
        ui_call(self.mesh_file_status, "setToolTip", mesh.message)
        ui_call(self.material_file_status, "setText", self._file_status_text(materials.status))
        ui_call(self.material_file_status, "setToolTip", materials.message)
        values = {
            "type": mesh.mesh_type or "—",
            "cells": str(mesh.cell_count) if mesh.cell_count else "—",
            "connections": str(mesh.connection_count) if mesh.status == "available" else "—",
            "materials": str(materials.material_count) if materials.material_count else "—",
            "bounds": "—",
            "unit": mesh.coordinate_unit if mesh.status == "available" else "—",
        }
        if len(mesh.coordinate_bounds) == 3:
            values["bounds"] = join_text(
                "  ",
                (
                    join_text("", [axis, " [", f"{bounds[0]:.6G}", ", ", f"{bounds[1]:.6G}", "]"])
                    for axis, bounds in zip("XYZ", mesh.coordinate_bounds)
                ),
            )
        for key, value in values.items():
            ui_call(self.mesh_summary_labels[key], "setText", value)
        self.spatial_dataset_table.setRowCount(0)
        for dataset in summary.datasets:
            row = self.spatial_dataset_table.rowCount()
            self.spatial_dataset_table.insertRow(row)
            details = format_message(
                "resources.dataset_details",
                path=dataset.relative_file or "—",
                dataset=dataset.hdf5_path or "—",
                dtype=dataset.dtype or "—",
                minimum=dataset.minimum if dataset.minimum is not None else "—",
                maximum=dataset.maximum if dataset.maximum is not None else "—",
                missing=dataset.missing_count,
            )
            if dataset.message:
                details += format_message("validation.reason", reason=dataset.message)
            columns = (
                self._status_symbol(dataset.status),
                dataset.property_name,
                dataset.dataset_name,
                dataset.cell_count or "—",
                self._number_range(dataset.minimum, dataset.maximum),
                dataset.unit,
            )
            for column, value in enumerate(columns):
                item = ui_construct(QtWidgets.QTableWidgetItem, str(value))
                ui_call(item, "setToolTip", details)
                self.spatial_dataset_table.setItem(row, column, item)
        self.boundary_tree.clear()
        boundary_status_text = {
            "available": msg("text.recognized"),
            "warning": msg("text.recognized.with.potential.risks"),
            "missing": msg("text.not.found"),
            "empty": msg("text.empty.file"),
            "invalid": msg("validation.invalid.format"),
            "read_failed": msg("validation.invalid.format"),
        }
        for boundary in summary.boundaries:
            tooltip = boundary_status_text.get(boundary.status, msg("validation.invalid.format"))
            if boundary.message:
                tooltip += join_text("", ["\n", boundary.message])
            item = ui_construct(
                QtWidgets.QTreeWidgetItem,
                [
                    self._status_symbol(boundary.status),
                    boundary.display_name,
                    boundary.internal_name,
                    str(boundary.face_count) if boundary.face_count else "—",
                ],
            )
            ui_call(item, "setToolTip", 0, tooltip)
            ui_call(
                item,
                "setToolTip",
                1,
                format_message(
                    "resources.status_path", state=tooltip, path=boundary.file_path or "—"
                ),
            )
            self.boundary_tree.addTopLevelItem(item)
        errors = [issue for issue in summary.issues if issue.severity == "error"]
        warnings = [issue for issue in summary.issues if issue.severity == "warning"]
        self.spatial_check_details.clear()
        for issue in summary.issues:
            label = msg("text.error") if issue.severity == "error" else msg("text.warning")
            item = ui_construct(QtWidgets.QTreeWidgetItem, [label, issue.message])
            item.setForeground(
                0, QtGui.QColor("#b42318" if issue.severity == "error" else "#b54708")
            )
            self.spatial_check_details.addTopLevelItem(item)
        if errors or warnings:
            ui_call(
                self.spatial_check_status,
                "setText",
                format_message("validation.found", errors=len(errors), warnings=len(warnings)),
            )
            color = "#b42318" if errors else "#b54708"
            self.spatial_check_status.setStyleSheet(join_text("", ["color:", color, ";"]))
            self.spatial_details_button.setEnabled(True)
        else:
            ui_call(
                self.spatial_check_status,
                "setText",
                msg("text.mesh.material.mapping.spatial.datasets.and.boundaries.are.valid"),
            )
            self.spatial_check_status.setStyleSheet("color:#247a5a;")
            self.spatial_details_button.setChecked(False)
            self.spatial_details_button.setEnabled(False)
        if hasattr(self, "region_conditions_editor") and self._project_directory is not None:
            self.region_conditions_editor.sync_spatial(summary)
        self._refresh_material_mapping()

    def _refresh_material_mapping(self) -> None:
        if not hasattr(self, "spatial_assignment_editor"):
            return
        if self._project_directory is None:
            return

        def issues(record):
            try:
                index = self._material_records.index(record)
            except ValueError:
                index = 0
            return self._material_issues(record, index)

        self.spatial_assignment_editor.update_material_mapping(
            self._spatial_summary, self._material_records, issues
        )

    def _apply_continuum_manifest(self) -> None:
        manifest = self._continuum_manifest
        if manifest is not None:
            if manifest.mesh and (not self.mesh.text().strip()):
                ui_call(self.mesh, "setText", manifest.mesh)
            if manifest.materials and (not self.material_file.text().strip()):
                ui_call(self.material_file, "setText", manifest.materials)
            file_by_kind = {"porosity": manifest.porosity, "permeability": manifest.permeability}
            rows = self._rows(self.datasets)
            for row in rows:
                haystack = join_text("", [row[0], " ", row[1]]).casefold()
                kind = (
                    "porosity"
                    if "porosity" in haystack
                    else "permeability"
                    if "permeability" in haystack
                    else ""
                )
                if kind and file_by_kind[kind] and (not row[2].strip()):
                    row[2] = file_by_kind[kind]
            self._set_rows(self.datasets, rows)
        manifest = manifest or ContinuumManifest()
        effective_manifest = ContinuumManifest(
            schema_version=manifest.schema_version,
            mesh=self.mesh.text().strip(),
            materials=self.material_file.text().strip(),
            permeability="",
            porosity="",
            boundary_regions={
                **manifest.boundary_regions,
                **{
                    item.name: item.file_path
                    for item in self.region_conditions_editor.regions()
                    if item.region_type == "FILE" and item.file_path
                },
            },
            visualization=manifest.visualization,
        )
        definitions = [
            (dataset_id, name, relative, hdf5_name or None)
            for dataset_id, name, relative, hdf5_name in self._rows(self.datasets)
        ]
        self._spatial_summary = inspect_project_spatial_data(
            self._project_directory,
            effective_manifest,
            definitions,
            octree_directory=self._octree_directory,
        )
        self._refresh_spatial_data_view()

    def _choose_observation_regions(self) -> None:
        points = [
            item.name
            for item in self.region_conditions_editor.regions()
            if item.region_type == "COORDINATE"
        ]
        dialog = MultiReferenceDialog(
            msg("text.select.observation.points"),
            points,
            _csv(self.observation_regions.text()),
            self,
        )
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            ui_call(self.observation_regions, "setText", join_text(", ", dialog.selected_values()))
            self._mark_dirty()

    def _choose_mass_balance_regions(self) -> None:
        regions = [item.name for item in self.region_conditions_editor.regions()]
        dialog = MultiReferenceDialog(
            msg("text.select.mass.balance.regions"),
            regions,
            _csv(self.mass_balance_file_regions.text()),
            self,
        )
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            ui_call(
                self.mass_balance_file_regions, "setText", join_text(", ", dialog.selected_values())
            )
            self._mark_dirty()

    def _choose_observation_outputs(self) -> None:
        checks = list(self.observation_flow_output_checks.items()) + list(
            self.observation_chem_output_checks.items()
        )
        self._choose_variable_checks(msg("text.select.observation.variables"), checks)
        self._refresh_observation_output_summary()

    def _choose_variable_checks(self, title: str, checks) -> None:
        dialog = LocalizedDialog(self)
        ui_call(dialog, "setWindowTitle", title)
        dialog.resize(640, 560)
        layout = QtWidgets.QVBoxLayout(dialog)
        search = ui_construct(QtWidgets.QLineEdit)
        ui_call(search, "setPlaceholderText", msg("text.search.variables"))
        catalog = QtWidgets.QListWidget()
        catalog.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        items = []
        for key, check in checks:
            item = ui_construct(QtWidgets.QListWidgetItem, key)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if check.isChecked() else QtCore.Qt.Unchecked)
            item.setData(QtCore.Qt.UserRole, check)
            ui_call(catalog, "addItem", item)
            items.append(item)
        count = ui_construct(QtWidgets.QLabel)

        def update_count():
            ui_call(
                count,
                "setText",
                format_message(
                    "selection.count",
                    selected=sum((item.checkState() == QtCore.Qt.Checked for item in items)),
                    total=len(items),
                ),
            )

        def filter_items(text):
            lowered = text.lower()
            for item in items:
                item.setHidden(lowered not in item.text().lower())

        search.textChanged.connect(filter_items)
        catalog.itemChanged.connect(lambda _: update_count())
        update_count()
        clear = ui_construct(QtWidgets.QPushButton, msg("text.clear"))
        clear.clicked.connect(lambda: [item.setCheckState(QtCore.Qt.Unchecked) for item in items])
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(search)
        layout.addWidget(catalog, 1)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(count)
        row.addStretch(1)
        row.addWidget(clear)
        layout.addLayout(row)
        layout.addWidget(buttons)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            for item in items:
                item.data(QtCore.Qt.UserRole).setChecked(item.checkState() == QtCore.Qt.Checked)
            self._mark_dirty()

    def _refresh_observation_output_summary(self) -> None:
        checks = list(self.observation_flow_output_checks.items()) + list(
            self.observation_chem_output_checks.items()
        )
        selected = [key for key, check in checks if check.isChecked()]
        ui_call(
            self.observation_output_summary,
            "setText",
            msg("text.no.variable.selected")
            if not selected
            else format_message(
                "selection.summary",
                count=len(selected),
                names=join_text(", ", selected[:5]) + ("…" if len(selected) > 5 else ""),
            ),
        )

    def _update_schedule_stack(self, prefix: str) -> None:
        mode = getattr(self, join_text("", [prefix, "_mode"])).currentData()
        getattr(self, join_text("", [prefix, "_schedule_stack"])).setCurrentIndex(
            {"periodic_time": 0, "specified_times": 1, "periodic_timestep": 2}.get(mode, 0)
        )

    def _update_output_schedule_ui(self, *args) -> None:
        self._update_schedule_stack("output")

    def _update_output_variable_availability(self) -> None:
        if not hasattr(self, "chem_output_checks"):
            return
        has_secondary = bool(self._selected_secondary_species)
        for group in (self.chem_output_checks, getattr(self, "observation_chem_output_checks", {})):
            if not group:
                continue
            secondary = group["secondary_species"]
            if not has_secondary:
                secondary.setChecked(False)
            secondary.setEnabled(has_secondary)
            ui_call(
                secondary,
                "setToolTip",
                "" if has_secondary else msg("text.no.secondary.species.defined"),
            )
        has_minerals = bool(self._selected_minerals)
        for group in (self.chem_output_checks, getattr(self, "observation_chem_output_checks", {})):
            if not group:
                continue
            for key in ("mineral_volume_fraction", "mineral_saturation_index"):
                check = group[key]
                if not has_minerals:
                    check.setChecked(False)
                check.setEnabled(has_minerals)
                ui_call(
                    check, "setToolTip", "" if has_minerals else msg("text.no.minerals.defined")
                )
        has_kinetics = bool(self._kinetics_mineral_names())
        for group in (self.chem_output_checks, getattr(self, "observation_chem_output_checks", {})):
            if not group:
                continue
            rate = group["mineral_rate"]
            if not (has_minerals and has_kinetics):
                rate.setChecked(False)
            rate.setEnabled(has_minerals and has_kinetics)
            ui_call(
                rate, "setToolTip", "" if has_kinetics else msg("text.no.kinetic.minerals.defined")
            )
        if hasattr(self, "observation_output_summary"):
            self._refresh_observation_output_summary()

    def _rename_output_reference(self, kind: str, old_name: str, new_name: str) -> None:
        if kind != "region":
            return
        for editor in (self.observation_regions, self.mass_balance_file_regions):
            names = [new_name if name == old_name else name for name in _csv(editor.text())]
            ui_call(editor, "setText", join_text(", ", names))

    def _jump_to_review_item(self, item, column=0) -> None:
        page = item.data(0, QtCore.Qt.UserRole)
        if isinstance(page, int):
            self.navigation.setCurrentRow(page)

    def _refresh_navigation_status(self, report: model.ValidationReport) -> None:
        page_errors = [False] * len(self._nav_labels)
        routes = {
            0: ("simulation",),
            1: ("grid", "datasets"),
            2: ("materials", "characteristic_curves", "curve"),
            3: ("chemistry", "minerals", "chemical_constraints", "constraint", "species"),
            4: ("regions",),
            5: ("flow_conditions", "transport_conditions"),
            6: (
                "spatial_assignment",
                "assignment.",
                "material_mapping",
                "reference.region",
                "reference.material",
                "reference.flow",
                "reference.transport",
            ),
            7: ("time", "numerical_methods", "numerics"),
            8: ("output",),
        }
        for issue in report.errors:
            haystack = join_text("", [issue.code, " ", issue.path])
            for page, prefixes in routes.items():
                if any((prefix in haystack for prefix in prefixes)):
                    page_errors[page] = True
                    break
        for index, label in enumerate(self._nav_labels):
            item = self.navigation.item(index)
            flow_enabled = self.flow_enabled_check.isChecked()
            enabled = index == 0 or (
                flow_enabled and (index != 3 or self.transport_enabled_check.isChecked())
            )
            item.setHidden(False)
            if enabled:
                item.setFlags(item.flags() | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                marker = "!" if page_errors[index] else "✓"
                ui_call(item, "setToolTip", "")
            else:
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEnabled & ~QtCore.Qt.ItemIsSelectable)
                marker = "—"
                ui_call(
                    item,
                    "setToolTip",
                    msg("text.solute.transport.is.disabled.for.the.current.simulation"),
                )
            ui_call(item, "setText", join_text("", [marker, "  ", index + 1, "  ", label]))
        width = self.navigation.sizeHintForColumn(0) + 2 * self.navigation.frameWidth()
        self.navigation.setFixedWidth(max(220, width + 8))

    def _database_absolute_path(self) -> Path | None:
        text = self.database_path_edit.text().strip()
        if not text:
            return None
        path = Path(text)
        if not path.is_absolute() and self._project_directory is not None:
            path = self._resource_imports.resolve(text)
        return path

    def _select_thermodynamic_database(self) -> None:
        initial = (
            str(self._project_directory / "database")
            if self._project_directory
            else str(Path.cwd())
        )
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            render_text(msg("text.choose.thermodynamic.database")),
            initial,
            render_text(msg("text.thermodynamic.databases.dat.txt.all.files")),
            options=QtWidgets.QFileDialog.DontUseNativeDialog,
        )
        if not file_path:
            return
        source = Path(file_path)
        target = source
        relative = str(source)
        if self._project_directory is not None:
            try:
                relative = self._resource_imports.stage(source, "database")
                target = self._resource_imports.resolve(relative)
            except OSError as exc:
                MessageBox.critical(self, msg("text.database.copy.failed"), exception_text(exc))
                return
        ui_call(self.database_path_edit, "setText", relative)
        self._parse_current_database(target)
        self._mark_dirty()

    def _set_database_status(self, parsed: bool, label: str, message: str = "") -> None:
        self._database_status = "parsed" if parsed else "invalid"
        self._database_message = message
        ui_call(self.database_status_label, "setText", label)
        self.database_status_label.setStyleSheet("color:#247a5a;" if parsed else "color:#b42318;")
        ui_call(self.database_status_label, "setToolTip", message)

    def _parse_current_database(self, path: Path | None = None) -> None:
        source = path or self._database_absolute_path()
        try:
            if source is None:
                raise FileNotFoundError(msg("text.no.database.file.selected"))
            self._parsed_thermodynamic_database = parse_thermodynamic_database(source)
        except (OSError, ValueError) as exc:
            self._parsed_thermodynamic_database = ParsedThermodynamicDatabase()
            self._set_database_status(
                False, msg("text.database.status.parsing.failed"), exception_text(exc)
            )
        else:
            self._set_database_status(True, msg("text.database.status.parsed"))
        self._refresh_chemistry_summaries()

    def _select_aqueous_species(self, mode: str) -> None:
        parsed = self._parsed_thermodynamic_database
        candidates = parsed.primary_species if mode == "primary" else parsed.secondary_species
        selected = (
            self._selected_primary_species
            if mode == "primary"
            else self._selected_secondary_species
        )
        dialog = SpeciesSelectionDialog(
            mode, candidates, selected, self._selected_primary_species, self
        )
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        if mode == "primary":
            self._selected_primary_species = dialog.selected_species()
        else:
            self._selected_secondary_species = dialog.selected_species()
        self._refresh_chemistry_summaries()
        self._refresh_kinetics_species_combos()
        self._mark_dirty()

    def _select_passive_gases(self) -> None:
        dialog = SpeciesSelectionDialog(
            "gas",
            self._parsed_thermodynamic_database.gas_species,
            self._selected_passive_gases,
            self._selected_primary_species,
            self,
        )
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self._selected_passive_gases = dialog.selected_species()
            self._refresh_chemistry_summaries()
            self._mark_dirty()

    def _select_minerals(self) -> None:
        initial = list(self._selected_minerals)
        candidate_selection = initial
        while True:
            dialog = MineralSelectionDialog(
                self._parsed_thermodynamic_database.minerals, candidate_selection, self
            )
            if dialog.exec_() != QtWidgets.QDialog.Accepted:
                return
            selected = dialog.selected_minerals()
            configured = set(self._kinetics_mineral_names())
            removed_configured = [
                name for name in initial if name not in selected and name in configured
            ]
            if removed_configured:
                box = MessageBox(self)
                box.setIcon(QtWidgets.QMessageBox.Warning)
                ui_call(box, "setWindowTitle", msg("text.remove.mineral.kinetics"))
                ui_call(
                    box,
                    "setText",
                    format_message(
                        "mineral.remove_kinetics", names=join_text("\n", removed_configured)
                    ),
                )
                continue_button = ui_call(
                    box, "addButton", msg("text.continue"), QtWidgets.QMessageBox.AcceptRole
                )
                ui_call(
                    box,
                    "addButton",
                    msg("text.return.to.editing"),
                    QtWidgets.QMessageBox.RejectRole,
                )
                box.exec_()
                if box.clickedButton() is not continue_button:
                    candidate_selection = selected
                    continue
                self._remove_kinetics_by_names(set(removed_configured))
            self._selected_minerals = selected
            self._refresh_chemistry_summaries()
            self._refresh_kinetics_mineral_combos()
            self._mark_dirty()
            return

    def _refresh_chemistry_summaries(self) -> None:

        def show(editor, values, empty):
            editor.setProperty("fracrtm_user_content", bool(values))
            ui_call(editor, "setText", join_text(", ", values) if values else empty)

        show(
            self.primary_species_summary,
            self._selected_primary_species,
            msg("text.no.primary.species.selected"),
        )
        show(
            self.secondary_species_summary,
            self._selected_secondary_species,
            msg("text.no.secondary.species.selected"),
        )
        show(self.passive_gas_summary, self._selected_passive_gases, msg("text.no.gases.selected"))
        show(
            self.selected_minerals_summary,
            self._selected_minerals,
            msg("text.no.minerals.selected"),
        )
        parsed = self._parsed_thermodynamic_database
        enabled = bool(
            parsed.primary_species
            or parsed.secondary_species
            or parsed.gas_species
            or parsed.minerals
        )
        for button in (
            self.select_primary_species_button,
            self.select_secondary_species_button,
            self.select_passive_gas_button,
            self.select_minerals_button,
        ):
            button.setEnabled(enabled)
        checks = (
            (
                self.primary_species_summary,
                self._selected_primary_species,
                set(parsed.primary_species),
            ),
            (
                self.secondary_species_summary,
                self._selected_secondary_species,
                set(parsed.secondary_species),
            ),
            (self.passive_gas_summary, self._selected_passive_gases, set(parsed.gas_species)),
            (self.selected_minerals_summary, self._selected_minerals, set(parsed.minerals)),
        )
        parsed_loaded = self._database_status == "parsed"
        for editor, values, available in checks:
            invalid = [name for name in values if parsed_loaded and name not in available]
            editor.setStyleSheet("border:1px solid #b42318;" if invalid else "")
            ui_call(
                editor,
                "setToolTip",
                msg("text.not.found.in.the.database") + join_text(", ", invalid) if invalid else "",
            )
        if hasattr(self, "chem_output_checks"):
            self._update_output_variable_availability()
        if hasattr(self, "region_conditions_editor"):
            self.region_conditions_editor.update_chemistry_catalogs(
                self._selected_primary_species,
                self._selected_passive_gases,
                self._selected_minerals,
            )

    def _add_kinetic_mineral(self) -> None:
        available = [
            name for name in self._selected_minerals if name not in self._kinetics_mineral_names()
        ]
        if not self._selected_minerals:
            MessageBox.information(
                self, msg("text.cannot.add"), msg("text.select.a.mineral.under.minerals.first")
            )
            return
        if not available:
            MessageBox.information(
                self,
                msg("text.cannot.add"),
                msg("text.kinetics.have.already.been.configured.for.all.defined.minerals"),
            )
            return
        self._append_kinetics_row(
            model.MineralKineticsConfig(
                mineral_name=available[0], rate_constant=1e-10, rate_unit="mol/m^2-sec"
            )
        )
        self._refresh_kinetics_mineral_combos()
        self._mark_dirty()

    def _append_kinetics_row(self, kinetic) -> None:
        row = self.mineral_kinetics_table.rowCount()
        self.mineral_kinetics_table.insertRow(row)
        mineral = QtWidgets.QComboBox()
        mineral.setProperty("fracrtm_user_content", True)
        mineral.currentIndexChanged.connect(self._refresh_kinetics_mineral_combos)
        rate = ScientificValueEdit(kinetic.rate_constant)
        rate_unit = QtWidgets.QComboBox()
        ui_call(rate_unit, "addItem", "mol/m²-sec", "mol/m^2-sec")
        surface_model = QtWidgets.QComboBox()
        ui_call(surface_model, "addItem", msg("text.pflotran.default"), "default")
        ui_call(surface_model, "addItem", msg("text.by.mineral.mass.mineral.mass"), "mineral_mass")
        surface = ScientificValueEdit(kinetic.specific_surface_area, optional=True)
        surface_unit = QtWidgets.QComboBox()
        for label, value in (("m²/kg", "m2/kg"), ("m²/g", "m2/g"), ("cm²/g", "cm2/g")):
            ui_call(surface_unit, "addItem", label, value)
        species = QtWidgets.QComboBox()
        species.setProperty("fracrtm_user_content", True)
        alpha = ui_construct(
            QtWidgets.QLineEdit, "" if kinetic.alpha is None else str(kinetic.alpha)
        )
        alpha.setValidator(QtGui.QDoubleValidator(alpha))
        for column, widget in enumerate(
            (mineral, rate, rate_unit, surface_model, surface, surface_unit, species, alpha)
        ):
            self.mineral_kinetics_table.setCellWidget(row, column, widget)
        ui_call(mineral, "addItem", kinetic.mineral_name)
        rate_unit.setCurrentIndex(max(0, rate_unit.findData(kinetic.rate_unit)))
        surface_model.setCurrentIndex(max(0, surface_model.findData(kinetic.surface_area_function)))
        surface_unit.setCurrentIndex(max(0, surface_unit.findData(kinetic.surface_area_unit)))

        def update_surface_area_editor(*_):
            enabled = surface_model.currentData() == "mineral_mass"
            surface.setEnabled(enabled)
            surface_unit.setEnabled(enabled)

        surface_model.currentIndexChanged.connect(update_surface_area_editor)
        update_surface_area_editor()
        species.setProperty("desired_value", kinetic.prefactor_species or "")
        self._refresh_kinetics_species_combos()

    def _kinetics_mineral_names(self) -> list[str]:
        return [
            self.mineral_kinetics_table.cellWidget(row, 0).currentText()
            for row in range(self.mineral_kinetics_table.rowCount())
        ]

    def _refresh_kinetics_mineral_combos(self, *args) -> None:
        current = self._kinetics_mineral_names()
        blockers = []
        for row in range(self.mineral_kinetics_table.rowCount()):
            combo = self.mineral_kinetics_table.cellWidget(row, 0)
            value = combo.currentText()
            blockers.append(QtCore.QSignalBlocker(combo))
            combo.clear()
            used_elsewhere = {name for index, name in enumerate(current) if index != row}
            ui_call(
                combo,
                "addItems",
                [name for name in self._selected_minerals if name not in used_elsewhere],
            )
            combo.setCurrentText(value)
        del blockers

    def _compatible_secondary_species(self) -> list[str]:
        return list(self._selected_secondary_species)

    def _refresh_kinetics_species_combos(self) -> None:
        choices = self._selected_primary_species + self._compatible_secondary_species()
        for row in range(self.mineral_kinetics_table.rowCount()):
            combo = self.mineral_kinetics_table.cellWidget(row, 6)
            current = combo.currentData() or combo.property("desired_value") or ""
            combo.clear()
            ui_call(combo, "addItem", "", "")
            for name in choices:
                ui_call(combo, "addItem", name, name)
            index = combo.findData(current)
            if current and index < 0:
                ui_call(
                    combo, "addItem", format_message("reference.invalid", name=current), current
                )
                index = combo.count() - 1
                combo.model().item(index).setEnabled(False)
            combo.setCurrentIndex(max(0, index))
            combo.setProperty("desired_value", "")

    def _delete_kinetic_minerals(self) -> None:
        rows = sorted(
            {index.row() for index in self.mineral_kinetics_table.selectedIndexes()}, reverse=True
        )
        for row in rows:
            self.mineral_kinetics_table.removeRow(row)
        self._refresh_kinetics_mineral_combos()
        self._mark_dirty()

    def _remove_kinetics_by_names(self, names: set[str]) -> None:
        for row in range(self.mineral_kinetics_table.rowCount() - 1, -1, -1):
            if self.mineral_kinetics_table.cellWidget(row, 0).currentText() in names:
                self.mineral_kinetics_table.removeRow(row)

    def _collect_kinetics(self) -> list[model.MineralKineticsConfig]:
        result = []
        for row in range(self.mineral_kinetics_table.rowCount()):
            mineral = self.mineral_kinetics_table.cellWidget(row, 0).currentText()
            rate = self.mineral_kinetics_table.cellWidget(row, 1).value()
            rate_unit = self.mineral_kinetics_table.cellWidget(row, 2).currentData()
            surface_model = self.mineral_kinetics_table.cellWidget(row, 3).currentData()
            surface = (
                self.mineral_kinetics_table.cellWidget(row, 4).value()
                if surface_model == "mineral_mass"
                else None
            )
            surface_unit = self.mineral_kinetics_table.cellWidget(row, 5).currentData()
            species = self.mineral_kinetics_table.cellWidget(row, 6).currentData() or None
            alpha_text = self.mineral_kinetics_table.cellWidget(row, 7).text().strip()
            try:
                alpha = float(alpha_text) if alpha_text else None
            except ValueError as exc:
                raise ValueError(format_message("mineral.invalid_alpha", name=mineral)) from exc
            result.append(
                model.MineralKineticsConfig(
                    mineral_name=mineral,
                    rate_constant=rate,
                    rate_unit=rate_unit,
                    surface_area_function=surface_model,
                    specific_surface_area=surface,
                    surface_area_unit=surface_unit,
                    prefactor_species=species,
                    alpha=alpha,
                )
            )
        return result

    def _add_constraint(self) -> None:
        row = self.constraints.rowCount()
        self._append_row(
            self.constraints,
            [
                join_text("", ["solution_", row + 1]),
                format_message("chemistry.new_recipe", index=row + 1).render(),
                "",
                "",
            ],
        )
        self._edit_constraint(row)

    def _edit_constraint(self, row: int | None = None) -> None:
        if row is None:
            row = self.constraints.currentRow()
        if row is None or row < 0 or row >= self.constraints.rowCount():
            MessageBox.information(
                self,
                msg("text.select.aqueous.solution"),
                msg("text.select.an.aqueous.solution.first"),
            )
            return
        values = self._rows(self.constraints)[row]
        try:
            constraint = model.ChemicalConstraintConfig(
                values[0],
                values[1],
                self._parse_concentrations(values[2]),
                self._parse_minerals(values[3]),
            )
        except Exception as exc:
            MessageBox.warning(
                self, msg("text.unable.to.read.the.existing.solution"), exception_text(exc)
            )
            return
        minerals = list(self._selected_minerals)
        dialog = ChemicalConstraintEditorDialog(
            constraint, self._selected_primary_species, minerals, self
        )
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        result = dialog.result_constraint()
        updated = [
            result.id,
            result.name,
            self._format_concentrations(result),
            self._format_mineral_constraints(result),
        ]
        self.constraints.blockSignals(True)
        for column, value in enumerate(updated):
            item = self.constraints.item(row, column)
            if item is None:
                item = ui_construct(QtWidgets.QTableWidgetItem)
                self.constraints.setItem(row, column, item)
            ui_call(item, "setText", value)
        self.constraints.blockSignals(False)
        self._mark_dirty()

    def _load(self, c: model.PFLOTRANConfig) -> None:
        self._editor_snapshot = None
        self.flow_enabled_check.blockSignals(True)
        self.flow_enabled_check.setChecked(
            c.simulation.task_type in {"flow", "conservative_transport", "reactive_transport"}
        )
        self.flow_enabled_check.blockSignals(False)
        self.transport_enabled_check.blockSignals(True)
        self.transport_enabled_check.setChecked(c.simulation.uses_transport())
        self.transport_enabled_check.blockSignals(False)
        self._select_combo_data(self.flow_state_combo, c.simulation.flow_state)
        ui_call(self.mesh, "setText", c.grid.file)
        self._select_combo_data(self.gravity_mode, c.grid.gravity.mode)
        for editor, value in zip(self.gravity_edits, c.grid.gravity.vector):
            ui_call(editor, "setText", str(value))
        self._update_gravity_ui()
        ui_call(self.database_path_edit, "setText", c.chemistry.database.relative_path)
        self._set_optional_spin(self.final_time_value, c.time.final_time)
        self._select_combo_data(self.final_time_unit, c.time.final_unit)
        self._set_optional_spin(self.initial_timestep_value, c.time.initial_timestep)
        self._select_combo_data(self.initial_timestep_unit, c.time.initial_timestep_unit)
        self._set_optional_spin(self.maximum_timestep_value, c.time.maximum_timestep)
        self._select_combo_data(self.maximum_timestep_unit, c.time.maximum_timestep_unit)
        self._load_process_solver_widgets(self.flow_solver_widgets, c.numerical_methods.flow)
        self._load_process_solver_widgets(
            self.transport_solver_widgets, c.numerical_methods.transport
        )
        self.recommended_radio.setChecked(c.numerical_methods.mode != "custom")
        self.custom_radio.setChecked(c.numerical_methods.mode == "custom")
        self._set_rows(
            self.datasets, [[x.id, x.name, x.file, x.hdf5_dataset_name] for x in c.datasets]
        )
        self._set_material_records(self._material_rows_from_config(c))
        self.diffusion_coefficient.setValue(c.fluid_properties.diffusion_coefficient)
        self._selected_primary_species = list(c.chemistry.aqueous_species.primary_species)
        self._selected_secondary_species = list(c.chemistry.aqueous_species.secondary_species)
        self._selected_passive_gases = list(c.chemistry.gas_species.passive_gases)
        self._selected_minerals = list(c.chemistry.selected_minerals)
        self.log_formulation.setChecked(c.chemistry.log_formulation)
        self._select_combo_data(self.activity_coefficients, c.chemistry.activity_coefficients)
        self.update_porosity.setChecked(c.chemistry.update_porosity)
        self.update_permeability.setChecked(c.chemistry.update_permeability)
        self.mineral_kinetics_table.setRowCount(0)
        for kinetic in c.chemistry.mineral_kinetics:
            self._append_kinetics_row(kinetic)
        database_path = self._database_absolute_path()
        if database_path is not None and database_path.is_file():
            self._parse_current_database(database_path)
        else:
            status = c.chemistry.database.status
            trusted = status == "parsed" and self._project_directory is None
            self._set_database_status(
                trusted,
                msg("text.database.status.parsed")
                if trusted
                else msg("text.database.status.not.found"),
                c.chemistry.database.message
                or ("" if trusted else msg("text.database.file.does.not.exist")),
            )
            self._refresh_chemistry_summaries()
        self._set_rows(
            self.constraints,
            [
                [x.id, x.name, self._format_concentrations(x), self._format_mineral_constraints(x)]
                for x in c.chemical_constraints
            ],
        )
        self.region_conditions_editor.load(
            c.regions,
            c.flow_conditions,
            c.transport_conditions,
            c.chemistry.aqueous_species.primary_species,
            c.chemical_constraints,
            c.chemistry.gas_species.passive_gases,
            c.chemistry.selected_minerals,
        )
        self.spatial_assignment_editor.load(c.spatial_assignment)
        material_file = (
            c.spatial_assignment.material_mapping.file_path
            if c.spatial_assignment.material_mapping
            else ""
        )
        ui_call(self.material_file, "setText", material_file)
        self._load_continuum_manifest()
        self._apply_continuum_manifest()
        self._refresh_material_dataset_choices()
        if 0 <= self._active_material_row < len(self._material_records):
            self._load_material_editor(self._material_records[self._active_material_row])
        out = c.output
        self.output_format.setCurrentIndex(max(0, self.output_format.findData(out.snapshot_format)))
        self._load_output_schedule("output", out.schedule)
        self.print_initial.setChecked(out.print_initial)
        self.print_final.setChecked(out.print_final)
        self.velocity_at_center.setChecked(out.velocity_at_center or out.flow_variables.velocity)
        self.mass_flowrate.setChecked(out.mass_flowrate)
        self.mass_balance.setChecked(out.mass_balance)
        for keyword, check in self.flow_output_checks.items():
            check.setChecked(bool(getattr(out.flow_variables, keyword)))
        for keyword, check in self.chem_output_checks.items():
            check.setChecked(bool(getattr(out.chemistry_variables, keyword)))
        mass_file = out.mass_balance_file
        self.mass_balance_file_group.setChecked(mass_file.enabled)
        self._load_output_schedule("mass_balance_file", mass_file.schedule)
        ui_call(self.mass_balance_file_regions, "setText", join_text(", ", mass_file.regions))
        self.mass_balance_file_print_initial.setChecked(mass_file.print_initial)
        self.mass_balance_file_print_final.setChecked(mass_file.print_final)
        observation = out.observation
        self.observation_group.setChecked(observation.enabled)
        ui_call(self.observation_regions, "setText", join_text(", ", observation.regions))
        self._load_output_schedule("observation", observation.schedule)
        self.observation_print_initial.setChecked(observation.print_initial)
        self.observation_print_final.setChecked(observation.print_final)
        for keyword, check in self.observation_flow_output_checks.items():
            check.setChecked(bool(getattr(observation.flow_variables, keyword)))
        for keyword, check in self.observation_chem_output_checks.items():
            check.setChecked(bool(getattr(observation.chemistry_variables, keyword)))
        self._refresh_observation_output_summary()
        self.save_final_input.setChecked(out.save_final_input)
        self._update_task_ui()
        self._update_curve_ui()
        self._update_output_schedule_ui()
        self._editor_snapshot = self._collect().to_dict()
        self._refresh_review()

    @staticmethod
    def _format_property(value: model.PropertySource) -> str:
        return (
            join_text("", ["dataset:", value.dataset_id or ""])
            if value.mode == "dataset"
            else join_text("", ["constant:", value.value])
        )

    @staticmethod
    def _format_permeability(value: model.PermeabilitySource) -> str:
        if value.mode == "dataset":
            return join_text("", ["dataset:", value.dataset_id or ""])
        if value.mode == "diagonal":
            return "diagonal:" + join_text(",", map(str, value.diagonal or []))
        return join_text("", ["isotropic:", value.isotropic])

    @staticmethod
    def _format_region(value: model.RegionConfig) -> str:
        if value.geometry_type == "boundary_file":
            return value.file or ""
        if value.geometry_type in {"point", "box"}:
            return join_text(",", map(str, value.coordinates))
        if value.geometry_type == "cell_list":
            return join_text(",", map(str, value.cell_ids))
        return ""

    @staticmethod
    def _format_concentrations(value: model.ChemicalConstraintConfig) -> str:
        return join_text(
            "; ",
            (
                join_text("", [x.species, "=", x.value, ":", x.constraint_type])
                + (join_text("", [":", x.associated_species]) if x.associated_species else "")
                for x in value.concentrations
            ),
        )

    @staticmethod
    def _format_mineral_constraints(value: model.ChemicalConstraintConfig) -> str:
        return join_text(
            "; ",
            (
                join_text("", [x.mineral_id, "=", x.volume_fraction, ",", x.specific_surface_area])
                for x in value.minerals
            ),
        )

    @staticmethod
    def _split_source(text: str, label: str) -> tuple[str, str]:
        if ":" not in text:
            raise ValueError(format_message("validation.source_format", field=label))
        return tuple((value.strip() for value in text.split(":", 1)))

    def _collect(self) -> model.PFLOTRANConfig:
        c = model.PFLOTRANConfig.from_dict(self._source.to_dict())
        if not self.flow_enabled_check.isChecked():
            c.simulation.task_type = None
        elif self.transport_enabled_check.isChecked():
            c.simulation.task_type = (
                "reactive_transport" if self._selected_minerals else "conservative_transport"
            )
        else:
            c.simulation.task_type = "flow"
        c.simulation.flow_state = self.flow_state_combo.currentData()
        c.simulation.flow_mode = "RICHARDS"
        c.simulation.transport_mode = "GIRT"
        if c.simulation.saturation_state is None:
            c.simulation.saturation_state = (
                "variably_saturated"
                if any(curve.model != "default" for curve in c.characteristic_curves)
                else "saturated"
            )
        c.grid.file = self.mesh.text().strip()
        c.grid.gravity.mode = self.gravity_mode.currentData()
        try:
            c.grid.gravity.vector = [float(editor.text()) for editor in self.gravity_edits]
        except ValueError as exc:
            raise ValueError(
                msg("validation.gravity.vector.components.must.be.valid.numbers")
            ) from exc
        c.fluid_properties.diffusion_coefficient = self.diffusion_coefficient.value()
        c.time.final_time = self._optional_spin_value(self.final_time_value)
        c.time.final_unit = self.final_time_unit.currentData()
        c.time.initial_timestep = self._optional_spin_value(self.initial_timestep_value)
        c.time.initial_timestep_unit = self.initial_timestep_unit.currentData()
        c.time.maximum_timestep = self._optional_spin_value(self.maximum_timestep_value)
        c.time.maximum_timestep_unit = self.maximum_timestep_unit.currentData()
        c.numerical_methods.mode = "custom" if self.custom_radio.isChecked() else "recommended"
        for process_label, enabled, widgets, process in (
            (
                "FLOW",
                self.flow_enabled_check.isChecked(),
                self.flow_solver_widgets,
                c.numerical_methods.flow,
            ),
            (
                "TRANSPORT",
                self.transport_enabled_check.isChecked(),
                self.transport_solver_widgets,
                c.numerical_methods.transport,
            ),
        ):
            if c.numerical_methods.mode == "custom" and enabled:
                try:
                    self._collect_process_solver_widgets(widgets, process)
                except ValueError as exc:
                    process_name = process_label.lower()
                    heading = msg(
                        "solver.flow_heading"
                        if process_name == "flow"
                        else "solver.transport_heading"
                    )
                    raise SolverInputError(join_text(" ", [heading, exc]), process_name) from exc
            else:
                try:
                    self._collect_process_solver_widgets(widgets, process)
                except ValueError:
                    pass
        c.datasets = [
            model.DatasetConfig(a, b, d, e or None) for a, b, d, e in self._rows(self.datasets)
        ]
        self._save_active_material()
        curves = {curve.id: curve for curve in c.characteristic_curves}
        assigned_curve_specs: dict[str, tuple] = {}
        c.materials = []
        for record in self._material_records:
            p = model.PropertySource(
                record["porosity_source"],
                record["porosity_value"] if record["porosity_source"] == "constant" else None,
                record["porosity_dataset"] if record["porosity_source"] == "dataset" else None,
            )
            if record["permeability_source"] == "constant":
                k = model.PermeabilitySource(
                    mode="isotropic" if record["permeability_form"] == "isotropic" else "diagonal",
                    isotropic=record["k"] if record["permeability_form"] == "isotropic" else None,
                    diagonal=[record["kx"], record["ky"], record["kz"]]
                    if record["permeability_form"] == "anisotropic"
                    else None,
                )
            else:
                k = model.PermeabilitySource(
                    mode="dataset",
                    dataset_id=record["permeability_dataset"]
                    if record["permeability_form"] == "isotropic"
                    else None,
                    anisotropic_dataset=record["permeability_form"] == "anisotropic",
                    dataset_x=record["permeability_dataset_x"]
                    if record["permeability_form"] == "anisotropic"
                    else None,
                    dataset_y=record["permeability_dataset_y"]
                    if record["permeability_form"] == "anisotropic"
                    else None,
                    dataset_z=record["permeability_dataset_z"]
                    if record["permeability_form"] == "anisotropic"
                    else None,
                )
            curve_type = record["curve_type"]
            spec = (
                curve_type,
                record["vg_alpha"],
                record["vg_m"],
                record["vg_residual"],
                record.get("vg_max_capillary"),
                record.get("curve_permeability_function", "MUALEM"),
                record.get("curve_smooth", False),
            )
            curve_id = record["curve_id"]
            curve = curves.get(curve_id)
            if (
                curve is None
                or curve.model != curve_type
                or (curve_id in assigned_curve_specs and assigned_curve_specs[curve_id] != spec)
            ):
                if curve_type == "default":
                    curve = next((x for x in curves.values() if x.model == "default"), None)
                if curve is None or (
                    curve.id in assigned_curve_specs and assigned_curve_specs[curve.id] != spec
                ):
                    base = join_text("", [record["internal_id"], "_", curve_type])
                    curve_id = base
                    suffix = 2
                    while curve_id in curves:
                        curve_id = join_text("", [base, "_", suffix])
                        suffix += 1
                    curve = model.CharacteristicCurveConfig(
                        curve_id,
                        "DEFAULT" if curve_type == "default" else f"{record['name']} van Genuchten",
                        curve_type,
                    )
                    c.characteristic_curves.append(curve)
                    curves[curve_id] = curve
            curve_id = curve.id
            curve.model = curve_type
            if curve_type in {"van_genuchten", "brooks_corey"}:
                curve.permeability_function = record.get("curve_permeability_function", "MUALEM")
                if curve.permeability_function == "DEFAULT":
                    curve.permeability_function = "MUALEM"
                curve.smooth = record.get("curve_smooth", False)
                curve.alpha = record["vg_alpha"]
                curve.m_or_lambda = record["vg_m"]
                curve.liquid_residual_saturation = record["vg_residual"]
                curve.maximum_capillary_pressure = record.get("vg_max_capillary")
            assigned_curve_specs[curve_id] = spec
            c.materials.append(
                model.MaterialConfig(
                    record["internal_id"],
                    record["name"],
                    record["material_id"],
                    p,
                    k,
                    tortuosity=record["tortuosity"],
                    longitudinal_dispersivity=record["longitudinal"],
                    transverse_dispersivity_horizontal=record["transverse_h"],
                    transverse_dispersivity_vertical=record["transverse_v"],
                    characteristic_curve_id=curve_id,
                )
            )
        c.chemistry = model.ChemistryConfig(
            database=model.ThermodynamicDatabaseConfig(
                self.database_path_edit.text().strip(),
                self._database_status,
                self._database_message,
            ),
            aqueous_species=model.AqueousSpeciesConfig(
                list(self._selected_primary_species), list(self._selected_secondary_species)
            ),
            gas_species=model.GasSpeciesConfig(list(self._selected_passive_gases)),
            selected_minerals=list(self._selected_minerals),
            mineral_kinetics=self._collect_kinetics(),
            log_formulation=self.log_formulation.isChecked(),
            activity_coefficients=self.activity_coefficients.currentData(),
            update_porosity=self.update_porosity.isChecked(),
            update_permeability=self.update_permeability.isChecked(),
        )
        c.regions = self.region_conditions_editor.regions()
        c.flow_conditions = self.region_conditions_editor.flow_conditions()
        c.chemical_constraints = self.region_conditions_editor.chemical_constraints()
        c.transport_conditions = self.region_conditions_editor.transport_conditions()
        self._refresh_material_mapping()
        c.spatial_assignment = self.spatial_assignment_editor.config()
        c.output = model.OutputConfig(
            snapshot_format=self.output_format.currentData(),
            schedule=self._collect_output_schedule("output"),
            print_initial=self.print_initial.isChecked(),
            print_final=self.print_final.isChecked(),
            velocity_at_center=self.velocity_at_center.isChecked(),
            mass_flowrate=self.mass_flowrate.isChecked(),
            mass_balance=self.mass_balance.isChecked(),
            mass_balance_file=model.MassBalanceFileConfig(
                enabled=self.mass_balance_file_group.isChecked(),
                schedule=self._collect_output_schedule("mass_balance_file"),
                regions=_csv(self.mass_balance_file_regions.text()),
                print_initial=self.mass_balance_file_print_initial.isChecked(),
                print_final=self.mass_balance_file_print_final.isChecked(),
            ),
            observation=model.ObservationFileConfig(
                enabled=self.observation_group.isChecked(),
                regions=_csv(self.observation_regions.text()),
                schedule=self._collect_output_schedule("observation"),
                print_initial=self.observation_print_initial.isChecked(),
                print_final=self.observation_print_final.isChecked(),
                flow_variables=model.FlowOutputVariables(
                    **{
                        key: check.isChecked()
                        for key, check in self.observation_flow_output_checks.items()
                    }
                ),
                chemistry_variables=model.ChemistryOutputVariables(
                    **{
                        key: check.isChecked()
                        for key, check in self.observation_chem_output_checks.items()
                    }
                ),
            ),
            save_final_input=self.save_final_input.isChecked(),
            flow_variables=model.FlowOutputVariables(
                **{key: check.isChecked() for key, check in self.flow_output_checks.items()}
            ),
            chemistry_variables=model.ChemistryOutputVariables(
                **{key: check.isChecked() for key, check in self.chem_output_checks.items()}
            ),
        )
        if self._source.simulation.saturation_state is None:
            c.simulation.saturation_state = (
                "variably_saturated"
                if any(curve.model != "default" for curve in c.characteristic_curves)
                else "saturated"
            )
        if self._editor_snapshot is not None:
            from fracrtm.config.editor_values import preserve_unedited_numbers

            c = model.PFLOTRANConfig.from_dict(
                preserve_unedited_numbers(
                    self._source.to_dict(), self._editor_snapshot, c.to_dict()
                )
            )
        return c

    def _load_output_schedule(self, prefix: str, schedule: model.OutputScheduleConfig) -> None:
        mode = getattr(self, join_text("", [prefix, "_mode"]))
        mode.setCurrentIndex(max(0, mode.findData(schedule.mode)))
        getattr(self, join_text("", [prefix, "_interval"])).setValue(schedule.interval_value)
        getattr(self, join_text("", [prefix, "_interval_unit"])).setCurrentText(
            schedule.interval_unit
        )
        ui_call(
            getattr(self, join_text("", [prefix, "_times"])),
            "setText",
            join_text(", ", map(str, schedule.specified_times)),
        )
        getattr(self, join_text("", [prefix, "_times_unit"])).setCurrentText(
            schedule.specified_times_unit
        )
        getattr(self, join_text("", [prefix, "_timestep_interval"])).setValue(
            schedule.timestep_interval
        )
        self._update_schedule_stack(prefix)

    def _collect_output_schedule(self, prefix: str) -> model.OutputScheduleConfig:
        return model.OutputScheduleConfig(
            mode=getattr(self, join_text("", [prefix, "_mode"])).currentData(),
            interval_value=getattr(self, join_text("", [prefix, "_interval"])).value(),
            interval_unit=getattr(self, join_text("", [prefix, "_interval_unit"])).currentText(),
            specified_times=self._parse_output_times(
                getattr(self, join_text("", [prefix, "_times"])).text()
            ),
            specified_times_unit=getattr(
                self, join_text("", [prefix, "_times_unit"])
            ).currentText(),
            timestep_interval=getattr(self, join_text("", [prefix, "_timestep_interval"])).value(),
        )

    @staticmethod
    def _parse_output_times(text: str) -> list[float]:
        normalized = text.replace("，", " ").replace(",", " ")
        try:
            values = [float(value) for value in normalized.split()]
        except ValueError as exc:
            raise ValueError(
                msg("validation.output.times.must.be.numbers.separated.by.commas.or.spaces")
            ) from exc
        return sorted(set(values))

    @staticmethod
    def _parse_concentrations(text: str) -> list[model.ConcentrationConstraint]:
        result = []
        for entry in [x.strip() for x in text.split(";") if x.strip()]:
            species, value_part = entry.split("=", 1)
            values = [x.strip() for x in value_part.split(":")]
            result.append(
                model.ConcentrationConstraint(
                    species.strip(),
                    float(values[0]),
                    values[1] if len(values) > 1 else "T",
                    values[2] if len(values) > 2 else None,
                )
            )
        return result

    @staticmethod
    def _parse_minerals(text: str) -> list[model.MineralConstraint]:
        result = []
        for entry in [x.strip() for x in text.split(";") if x.strip()]:
            mineral, values = entry.split("=", 1)
            volume, surface = _numbers(values, msg("text.mineral.constraints"))
            result.append(model.MineralConstraint(mineral.strip(), volume, surface))
        return result

    def _display_issue_location(self, issue, config):
        path = issue.path
        page = 8
        label = msg("text.output.review")
        routes = (
            ("simulation", 0, msg("text.simulation.task")),
            ("grid", 1, msg("text.grid.and.spatial.data")),
            ("datasets", 1, msg("text.grid.and.spatial.data")),
            ("materials", 2, msg("text.materials")),
            ("characteristic", 2, msg("text.materials")),
            ("chemistry", 3, msg("text.chemistry")),
            ("regions", 4, msg("text.regions")),
            ("flow_conditions", 5, msg("text.conditions")),
            ("transport_conditions", 5, msg("text.conditions")),
            ("spatial_assignment", 6, msg("text.assignments")),
            ("time", 7, msg("text.time.and.solver.settings")),
            ("numerical", 7, msg("text.time.and.solver.settings")),
            ("output", 8, msg("text.output.review")),
        )
        for prefix, candidate, title in routes:
            if path.startswith(prefix):
                page, label = (candidate, title)
                break
        if config is not None:
            match = re.search("regions\\[(\\d+)\\]", path)
            if match and int(match.group(1)) < len(config.regions):
                label += " / " + config.regions[int(match.group(1))].name
            match = re.search("flow_conditions\\[(\\d+)\\]", path)
            if match and int(match.group(1)) < len(config.flow_conditions):
                label += " / " + config.flow_conditions[int(match.group(1))].name
            match = re.search("transport_conditions\\[(\\d+)\\]", path)
            if match and int(match.group(1)) < len(config.transport_conditions):
                label += " / " + config.transport_conditions[int(match.group(1))].name
            match = re.search("condition_assignments\\[(\\d+)\\]", path)
            assignments = config.spatial_assignment.condition_assignments
            if match and int(match.group(1)) < len(assignments):
                label += " / " + assignments[int(match.group(1))].name
            match = re.search("materials\\[(\\d+)\\]", path)
            if match and int(match.group(1)) < len(config.materials):
                label += " / " + config.materials[int(match.group(1))].name
        if "database" in path:
            label = msg("text.chemistry.thermodynamic.database")
            page = 3
        return (label, page)

    def _spatial_assignment_ui_issues(self, config) -> list[model.ValidationIssue]:
        return []

    def _region_condition_ui_issues(self, config) -> list[model.ValidationIssue]:
        return []

    def _chemistry_ui_issues(self, config) -> list[model.ValidationIssue]:
        return []

    def _update_chemistry_error_styles(self, report) -> None:
        kinetics_messages = {}
        for issue in report.errors:
            if issue.path.startswith("chemistry.mineral_kinetics["):
                try:
                    row = int(issue.path.split("[", 1)[1].split("]", 1)[0])
                except ValueError:
                    continue
                kinetics_messages.setdefault(row, []).append(issue.message)
        for row in range(self.mineral_kinetics_table.rowCount()):
            message = join_text("\n", kinetics_messages.get(row, []))
            for column in range(self.mineral_kinetics_table.columnCount()):
                widget = self.mineral_kinetics_table.cellWidget(row, column)
                if widget is not None:
                    widget.setStyleSheet("border:1px solid #b42318;" if message else "")
                    ui_call(widget, "setToolTip", message)

    def _update_solver_tab_errors(self, report: model.ValidationReport) -> None:
        for index, process, label in (
            (0, "flow", msg("text.flow.flow")),
            (1, "transport", msg("text.solute.transport.transport")),
        ):
            process_errors = [
                issue.message
                for issue in report.errors
                if join_text("", ["numerical_methods.", process]) in issue.path
            ]
            ui_call(
                self.process_tab_widget,
                "setTabText",
                index,
                join_text("", ["! ", label]) if process_errors else label,
            )
            ui_call(
                self.process_tab_widget, "setTabToolTip", index, join_text("\n", process_errors)
            )

    def _accept(self) -> None:
        if not self._refresh_review():
            self.navigation.setCurrentRow(self.stack.count() - 1)
            errors = sum(
                (
                    1
                    for row in range(self.review_output.topLevelItemCount())
                    if self.review_output.topLevelItem(row).data(0, QtCore.Qt.UserRole + 121)
                    == "error"
                )
            )
            box = MessageBox(self)
            box.setIcon(QtWidgets.QMessageBox.Warning)
            ui_call(box, "setWindowTitle", msg("text.model.contains.errors"))
            ui_call(box, "setText", format_message("validation.save_draft", count=errors))
            save_button = ui_call(
                box, "addButton", msg("text.save.anyway"), QtWidgets.QMessageBox.AcceptRole
            )
            ui_call(
                box, "addButton", msg("text.return.to.checks"), QtWidgets.QMessageBox.RejectRole
            )
            box.exec_()
            if box.clickedButton() is not save_button:
                return
        self._result = self._collect()
        self.accept()

    def _reset(self) -> None:
        self._source = BlankConfigFactory.pflotran()
        self._load(self._source)

    def _import_json(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            render_text(msg("text.import.pflotran.configuration")),
            "",
            render_text(msg("text.json.configuration.json")),
            options=QtWidgets.QFileDialog.DontUseNativeDialog,
        )
        if not path:
            return
        try:
            self._source = model.PFLOTRANConfig.load_json(path)
            self._load(self._source)
        except Exception as exc:
            MessageBox.critical(self, msg("text.import.failed"), exception_text(exc))

    def _export_json(self) -> None:
        try:
            config = self._collect()
        except Exception as exc:
            MessageBox.warning(self, msg("text.cannot.export"), exception_text(exc))
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            render_text(msg("text.export.pflotran.configuration")),
            render_text("pflotran.json"),
            render_text(msg("text.json.configuration.json")),
            options=QtWidgets.QFileDialog.DontUseNativeDialog,
        )
        if path:
            config.save_json(path)

    def get_config(self) -> model.PFLOTRANConfig:
        return model.PFLOTRANConfig.from_dict((self._result or self._collect()).to_dict())

    def _update_process_controls(self, *args) -> None:
        flow_enabled = self.flow_enabled_check.isChecked()
        transport_enabled = self.transport_enabled_check.isChecked()
        if transport_enabled and self.flow_state_combo.currentData() == "steady_state":
            self._select_combo_data(self.flow_state_combo, "transient")
        self.flow_state_combo.setEnabled(flow_enabled and (not transport_enabled))
        for index, label in enumerate(self._nav_labels):
            item = self.navigation.item(index)
            enabled = index == 0 or (flow_enabled and (index != 4 or transport_enabled))
            item.setHidden(False)
            if enabled:
                item.setFlags(item.flags() | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                ui_call(item, "setText", join_text("", ["✓  ", index + 1, "  ", label]))
                ui_call(item, "setToolTip", "")
            else:
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEnabled & ~QtCore.Qt.ItemIsSelectable)
                ui_call(item, "setText", join_text("", ["—  ", index + 1, "  ", label]))
                ui_call(
                    item,
                    "setToolTip",
                    msg("text.enable.the.relevant.process.on.the.simulation.page.first"),
                )
            page = self.stack.widget(index)
            if page is not None:
                page.setEnabled(enabled)
        self.mineral_kinetics_table.setEnabled(transport_enabled)
        if hasattr(self, "region_conditions_editor"):
            transport_item = self.region_conditions_editor.condition_process.model().item(1)
            if transport_item is not None:
                transport_item.setEnabled(transport_enabled)
        if hasattr(self, "spatial_assignment_editor"):
            self.spatial_assignment_editor.assignment_table.setColumnHidden(3, not flow_enabled)
            self.spatial_assignment_editor.assignment_table.setColumnHidden(
                4, not transport_enabled
            )
        self.flow_output_group.setVisible(flow_enabled)
        self.chem_output_group.setVisible(transport_enabled)
        self._update_output_variable_availability()
        current_row = self.navigation.currentRow()
        if current_row > 0 and (not (flow_enabled and (current_row != 4 or transport_enabled))):
            self.navigation.setCurrentRow(0)
        transport = "GIRT" if transport_enabled else msg("status.not_enabled")
        chemistry = (
            msg("text.configure.on.the.chemistry.and.minerals.page")
            if transport_enabled
            else msg("status.not_enabled")
        )
        ui_call(
            self.task_mapping,
            "setText",
            format_message(
                "model.process_summary",
                flow="RICHARDS" if flow_enabled else msg("status.not_enabled"),
                transport=transport,
                chemistry=chemistry,
            ),
        )
        if hasattr(self, "transport_group"):
            self.transport_group.setVisible(transport_enabled)
            self.global_transport_group.setVisible(transport_enabled)
            self._refresh_material_list()
        if hasattr(self, "process_tab_widget"):
            self._refresh_solver_process_tabs()
            self._update_recommended_solver_summary()
        self._mark_dirty()

    def _validate_model(self) -> bool:
        with self._resource_imports.overlay():
            return self._validate_model_with_imports()

    def commit_project_edit(self, config):
        self._resource_imports.commit_config(config)

    def _validate_model_with_imports(self) -> bool:
        self.review_output.clear()
        try:
            config = self._collect()
            report = config.validate(self._project_directory)
            from fracrtm.pflotran.spatial_validation import validate_spatial_resources

            mandatory = validate_spatial_resources(self._project_directory, config)
            report = model.ValidationReport(report.issues + mandatory.issues)
            chemistry_issues = []
            region_issues = self._region_condition_ui_issues(config)
            spatial_issues = self._spatial_assignment_ui_issues(config)
            if chemistry_issues or region_issues or spatial_issues:
                report = model.ValidationReport(
                    report.issues
                    + tuple(chemistry_issues)
                    + tuple(region_issues)
                    + tuple(spatial_issues)
                )
            issues = report.issues
        except SolverInputError as exc:
            process = exc.process
            issues = (
                model.ValidationIssue(
                    "error",
                    "numerics.parse",
                    join_text("", ["numerical_methods.", process]),
                    exception_text(exc),
                ),
            )
            report = model.ValidationReport(issues)
        except Exception as exc:
            issues = (
                model.ValidationIssue("error", "ui.parse", "ui.editor_input", exception_text(exc)),
            )
            report = model.ValidationReport(issues)
        for issue in issues:
            label = msg("text.error") if issue.severity == "error" else msg("text.warning")
            location, page = self._display_issue_location(
                issue, config if "config" in locals() else None
            )
            from fracrtm.config.validation_messages import format_validation_issue

            message = format_validation_issue(issue, config if "config" in locals() else None)
            item = ui_construct(QtWidgets.QTreeWidgetItem, [label, location, message])
            item.setData(0, QtCore.Qt.UserRole, page)
            color = QtGui.QColor("#b42318" if issue.severity == "error" else "#b54708")
            item.setForeground(0, QtGui.QBrush(color))
            item.setData(0, QtCore.Qt.UserRole + 121, issue.severity)
            self.review_output.addTopLevelItem(item)
        set_label_text(
            self.review_summary,
            format_message(
                "validation.counts", errors=len(report.errors), warnings=len(report.warnings)
            ),
        )
        set_label_text(
            self.status,
            msg("text.valid.references")
            if report.valid
            else format_message("validation.errors", count=len(report.errors)),
        )
        color = "#e8f5ef;color:#246c57" if report.valid else "#fcebea;color:#a33b31"
        self.status.setStyleSheet(
            join_text("", ["padding:6px 12px;border-radius:12px;background:", color, ";"])
        )
        self._refresh_navigation_status(report)
        self._update_solver_tab_errors(report)
        self._update_chemistry_error_styles(report)
        self.region_conditions_editor.apply_validation(report)
        self.spatial_assignment_editor.apply_validation(report)
        return report.valid

    def _show_page(self, row: int) -> None:
        if row < 0:
            return
        stack_row = (0, 1, 3, 4, 5, 5, 6, 2, 7)[row]
        self.stack.setCurrentIndex(stack_row)
        if row in (4, 5):
            self.region_conditions_editor.set_section("region" if row == 4 else "condition")
        if row == 1:
            self._rescan_spatial_data()
        if row == 6:
            self._refresh_material_mapping()
        if row == 8:
            self._refresh_review()

    def _output_checks(self):
        return list(self.flow_output_checks.items()) + list(self.chem_output_checks.items())

    def _refresh_output_summary(self) -> None:
        selected = [key for key, check in self._output_checks() if check.isChecked()]
        ui_call(
            self.output_variable_summary,
            "setText",
            msg("text.no.variable.selected")
            if not selected
            else format_message(
                "selection.summary",
                count=len(selected),
                names=join_text(", ", selected[:5]) + ("…" if len(selected) > 5 else ""),
            ),
        )

    def _choose_outputs(self) -> None:
        dialog = LocalizedDialog(self)
        ui_call(dialog, "setWindowTitle", msg("dialog.select_output_variables.title"))
        dialog.resize(640, 560)
        layout = QtWidgets.QVBoxLayout(dialog)
        search = ui_construct(QtWidgets.QLineEdit)
        ui_call(search, "setPlaceholderText", msg("text.search.variables"))
        catalog = QtWidgets.QListWidget()
        catalog.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        items = []
        for key, check in self._output_checks():
            item = ui_construct(QtWidgets.QListWidgetItem, key)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if check.isChecked() else QtCore.Qt.Unchecked)
            item.setData(QtCore.Qt.UserRole, check)
            ui_call(catalog, "addItem", item)
            items.append(item)
        count = ui_construct(QtWidgets.QLabel)

        def update_count():
            ui_call(
                count,
                "setText",
                format_message(
                    "selection.count",
                    selected=sum((item.checkState() == QtCore.Qt.Checked for item in items)),
                    total=len(items),
                ),
            )

        def filter_items(text):
            lowered = text.lower()
            for item in items:
                item.setHidden(lowered not in item.text().lower())

        search.textChanged.connect(filter_items)
        catalog.itemChanged.connect(lambda _: update_count())
        update_count()
        clear = ui_construct(QtWidgets.QPushButton, msg("text.clear"))
        clear.clicked.connect(lambda: [item.setCheckState(QtCore.Qt.Unchecked) for item in items])
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(search)
        layout.addWidget(catalog, 1)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(count)
        row.addStretch(1)
        row.addWidget(clear)
        layout.addLayout(row)
        layout.addWidget(buttons)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            for item in items:
                item.data(QtCore.Qt.UserRole).setChecked(item.checkState() == QtCore.Qt.Checked)
            self._refresh_output_summary()
            self._mark_dirty()

    def _update_task_ui(self, *args) -> None:
        current_row = self.navigation.currentRow()
        self._update_process_controls(*args)
        flow_enabled = self.flow_enabled_check.isChecked()
        transport_enabled = self.transport_enabled_check.isChecked()
        for index, label in enumerate(self._nav_labels):
            item = self.navigation.item(index)
            if item is None:
                continue
            enabled = index == 0 or (flow_enabled and (index != 3 or transport_enabled))
            if enabled:
                item.setFlags(item.flags() | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                ui_call(item, "setToolTip", "")
            else:
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEnabled & ~QtCore.Qt.ItemIsSelectable)
                ui_call(
                    item,
                    "setToolTip",
                    msg("text.solute.transport.is.disabled.for.the.current.simulation")
                    if index == 3
                    else msg("text.enable.groundwater.flow.first"),
                )
            item.setHidden(False)
            ui_call(item, "setText", join_text("", [index + 1, "  ", label]))
        if current_row >= 0:
            current_enabled = current_row == 0 or (
                flow_enabled and (current_row != 3 or transport_enabled)
            )
            self.navigation.setCurrentRow(current_row if current_enabled else 0)
        self.flow_output_group.hide()
        self.chem_output_group.hide()

    def _refresh_review(self) -> bool:
        valid = self._validate_model()
        self.review_output.setVisible(self.review_output.topLevelItemCount() > 0)
        ui_call(
            self.review_summary,
            "setText",
            msg("text.model.checks.passed")
            if valid and self.review_output.topLevelItemCount() == 0
            else self.review_summary.text(),
        )
        return valid
